"""Offline source/coverage and review-follow-up regression tests."""
from test.test_debate import review_response
import copy
import json
import unittest
from unittest.mock import patch
from nba_api.stats.static import teams
from backend.debate.agent import Draft, run_debate, evidence_catalog
from backend.debate.tools import DebateTools, DebateConfig, Audit, Claim, Scope
from test.test_debate import entry, row, tool_call, submit, response


class PerformanceData:
    def __init__(self):
        self.requests = []
        self.team_rows = [dict(TEAM_ID=t['id'], TEAM_NAME=t['full_name'], TEAM_ABBREVIATION=t['abbreviation'],
                               GP=82, OFF_RATING=100+i, DEF_RATING=100+i)
                          for i, t in enumerate(teams.get_teams())]
        self.players = [dict(PLAYER_ID=1, GP=50, PTS=36, AST=8),
                        dict(PLAYER_ID=2, GP=70, PTS=30, AST=10),
                        dict(PLAYER_ID=3, GP=5, PTS=60, AST=20)]
        self.stints = [row(year='2018-19', team=self.team_rows[10]['TEAM_ID'])]
        self.logs = [dict(row(year='2018-19', PTS=pts, AST=8), Game_ID=str(i),
                          MATCHUP='HOU vs. '+self.team_rows[opponent]['TEAM_ABBREVIATION'])
                     for i, (opponent, pts) in enumerate([(0,30),(0,40),(4,50),(10,10)])]
        self.fail = None

    def fetch(self, kind, **params):
        from backend.data.nba import DataUnavailable
        self.requests.append((kind, params))
        if kind == self.fail:
            raise DataUnavailable('NBA data budget exhausted')
        if kind == 'league_players': data = {'LeagueDashPlayerStats':self.players}
        elif kind == 'league': data = {'LeagueDashTeamStats':self.team_rows}
        elif kind == 'career': data = {'SeasonTotalsRegularSeason':self.stints, 'SeasonTotalsPostSeason':self.stints}
        elif kind == 'games': data = {'PlayerGameLog':self.logs}
        else: raise AssertionError(kind)
        result = entry(copy.deepcopy(data))
        result['parameters'] = params
        result['source_url'] = 'https://stats.nba.com/stats/'+kind
        return result


class PerformanceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.data = PerformanceData()
        self.config = DebateConfig(supported_player=1, opponent_player=2)
        self.tools = DebateTools(self.config, data=self.data, names={'1':'Alpha','2':'Beta'})

    def query(self, dimension='player_ranks', **extra):
        return self.tools.run('query_performance_context', dict(player_id=1, season=2018,
            dimension=dimension, selection_reason='Check the season discussed in the disputed evaluation.', **extra))

    def test_player_rank_threshold_and_ties(self):
        result = self.query()
        self.assertEqual(result['values']['PTS_LEAGUE_RANK']['value'], 1)
        self.assertEqual(result['values']['AST_LEAGUE_RANK']['value'], 2)
        self.assertEqual(result['values']['PTS_LEAGUE_RANK']['inputs']['eligible_players'], 2)
        self.data.players[1]['PTS'] = 36
        self.assertEqual(self.query()['values']['PTS_LEAGUE_RANK']['value'], 1)
        self.assertIn('not official', ' '.join(result['limitations']))
        self.assertEqual(self.tools.research_state['directed_queries'][0]['request']['season'], 2018)

    def test_rank_missing_duplicate_below_threshold_unavailable(self):
        self.assertEqual(self.query(min_games=60)['status'], 'unavailable')
        self.data.players[1]['AST'] = None
        self.assertEqual(self.query()['status'], 'unavailable')
        self.data.players[1]['AST'] = 10
        self.data.players.append(copy.deepcopy(self.data.players[0]))
        self.assertEqual(self.query()['status'], 'unavailable')
        self.assertFalse(self.tools.evidence)

    def test_team_offense_rank_and_verified_stint(self):
        result = self.query('team_offense')
        self.assertEqual(result['values']['TEAM_OFF_RATING']['value'], 110)
        self.assertEqual(result['values']['TEAM_OFF_RANK']['value'], 20)
        self.assertEqual(result['context']['team_id'], self.data.stints[0]['TEAM_ID'])
        self.assertEqual(self.query('team_offense', team_id=123)['status'], 'unavailable')
        self.data.stints.append(row(year='2018-19', team=self.data.team_rows[11]['TEAM_ID']))
        self.assertEqual(self.query('team_offense')['status'], 'needs_team')
        self.assertIn('id', self.query('team_offense', team_id=self.data.team_rows[11]['TEAM_ID']))

    def test_incomplete_team_table_and_old_era_fail_closed(self):
        self.data.team_rows.pop()
        self.assertEqual(self.query('team_offense')['status'], 'unavailable')
        self.assertEqual(self.query('opponent_splits')['status'], 'unavailable')
        result = self.tools.run('query_performance_context', dict(player_id=1, season=1995,
            dimension='team_offense', selection_reason='Check an old season.'))
        self.assertEqual(result['status'], 'unavailable')

    def test_opponent_definition_samples_and_ties(self):
        self.data.team_rows[5]['DEF_RATING'] = 104
        result = self.query('opponent_splits')
        self.assertEqual(result['values']['VS_TOP_DEF_GP']['value'], 3)
        self.assertEqual(result['values']['VS_TOP_DEF_PTS']['value'], 40)
        self.assertEqual(result['values']['VS_TOP_DEF_AST']['value'], 8)
        sample = result['values']['VS_TOP_DEF_PTS']['inputs']
        self.assertEqual(sample['checked_games'], 4)
        self.assertEqual(sample['game_ids'], ['0','1','2'])
        self.assertEqual(len(sample['opponents']), 6)
        self.assertIn('not all elite', ' '.join(result['limitations']))

    def test_playoff_sample_uses_explicit_regular_season_benchmark(self):
        result = self.query('opponent_splits', phase='Playoffs')
        self.assertEqual(result['scope']['phase'], 'Playoffs')
        queries = dict(self.data.requests)
        self.assertEqual(queries['league']['season_type_all_star'], 'Regular Season')
        self.assertEqual(queries['games']['season_type_all_star'], 'Playoffs')

    def test_malformed_missing_and_duplicate_logs_do_not_make_splits(self):
        original = copy.deepcopy(self.data.logs)
        for logs in [[], [dict(original[0], MATCHUP='HOU vs. ???')], original+[original[0]]]:
            with self.subTest(logs=logs):
                self.data.logs = logs
                self.assertEqual(self.query('opponent_splits')['status'], 'unavailable')
        self.assertFalse(self.tools.evidence)

    def test_no_games_against_selected_opponents_is_unknown(self):
        self.data.logs = self.data.logs[-1:]
        self.assertEqual(self.query('opponent_splits')['status'], 'unavailable')
        self.assertFalse(self.tools.evidence)

    def test_missing_metric_is_not_in_citation_catalog(self):
        self.data.logs[0]['AST'] = None
        result = self.query('opponent_splits')
        self.assertNotIn('VS_TOP_DEF_AST', result['values'])
        self.assertIn('VS_TOP_DEF_PTS', result['values'])
        self.assertTrue(any('AST unavailable' in note for note in result['limitations']))

    def test_catalog_cards_preserve_scope_label_sources_and_sample(self):
        result = self.query('opponent_splits')
        catalog = evidence_catalog(self.tools, [result])
        claim = next(self.tools.fact_claims[f['fact_id']] for f in catalog['facts']
                     if self.tools.fact_claims[f['fact_id']].metric == 'VS_TOP_DEF_PTS')
        audited = self.tools.audit_argument(Audit(claims=[claim]))
        card = audited['cards'][0]
        self.assertIn('top-5', card['label'])
        self.assertIn('2018-19', card['scope'])
        self.assertEqual(card['games'], 3)
        self.assertEqual(len(card['sources']), 2)
        self.assertEqual(card['inputs']['matched_games'], 3)
        for change in [dict(value=999), dict(player_id=2), dict(scope=Scope(start=2019,end=2019))]:
            self.assertFalse(self.tools.audit_argument(Audit(claims=[claim.model_copy(update=change)]))['valid'])

    def test_budget_failure_and_locked_players_record_gaps(self):
        self.data.fail = 'league_players'
        self.assertEqual(self.query()['status'], 'unavailable')
        self.assertTrue(self.tools.research_state['directed_queries'][0]['errors'])
        result = self.tools.run('query_performance_context', dict(player_id=3, season=2018,
            dimension='player_ranks', selection_reason='Wrong player.'))
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(len(self.data.requests), 1)

    def test_review_queries_all_dimensions_then_resubmits_seven_citations(self):
        rounds, reviews, facts = 0, 0, []
        final = ('In the 2018-19 regular season, Alpha showed strong scoring and playmaking: '
                 '36 points and 8 assists per game, first and second among returned players with at least 20 games. '
                 'His team ranked 20th in offense at 110 points per 100 possessions. '
                 'He averaged 40 points in three games against top-five regular-season defenses; that sample is limited.')
        def model(messages, **kwargs):
            nonlocal rounds, reviews
            if 'response_format' in kwargs:
                reviews += 1
                if reviews == 1:
                    return response(json.dumps(dict(status='needs_evidence', issues=[dict(
                        clause="Alpha has an elite peak and dominated strong opposition.",
                        reason='Awards do not support performance.', missing_evidence='Season scoring/assist benchmarks, team offense and opponent splits.')])) )
                payload = json.loads(messages[-1]['content'])
                self.assertEqual(len(payload['verified_cards']), 7)
                self.assertEqual(payload['draft']['response'], final)
                self.assertTrue(all(c['sources'] for c in payload['verified_cards']))
                return review_response({'status': 'pass', 'issues': []})
            rounds += 1
            if rounds == 1:
                return submit(dict(response='Alpha has an elite peak and dominated strong opposition.'))
            if rounds == 2:
                feedback = json.loads(messages[-1]['content'])
                self.assertIn('First use relevant saved evidence or fetch', feedback['instruction'])
                self.assertIn('strong_opponents', feedback['evidence_options'])
                self.assertIn('query_performance_context', [t['function']['name'] for t in kwargs['tools']])
            else:
                catalog = json.loads(messages[-1]['content'])['catalog']['facts']
                facts.extend(f['fact_id'] for f in catalog if rounds != 5 or 'PTS against top-5' in f['fact'])
            if rounds <= 4:
                return tool_call('query_performance_context', dict(player_id=1, season=2018,
                    dimension=['player_ranks','team_offense','opponent_splits'][rounds-2],
                    selection_reason='Verify the disputed 2018-19 evaluation.'))
            return submit(dict(response=final, fact_ids=facts))
        with patch('backend.debate.agent.complete', side_effect=model):
            result = run_debate([dict(role='system',content=''),dict(role='user',content='Can you support that evaluation?')],
                                'rebuttal', self.config, {'1':'Alpha','2':'Beta'}, data=self.data)
        self.assertEqual(result[0], final)
        self.assertEqual(result[2]['validation']['semantic_status'], 'pass')
        self.assertEqual(result[2]['validation']['submissions'], 2)
        self.assertEqual(len(result[2]['cards']), 7)
        self.assertEqual(len(result[2]['research']['directed_queries']), 3)

    def test_eight_citation_limit_is_enforced(self):
        with self.assertRaises(ValueError):
            Draft(response='A claim', fact_ids=['fact_'+str(i) for i in range(9)])


if __name__ == '__main__':
    unittest.main()
