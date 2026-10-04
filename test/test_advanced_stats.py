"""Verify actual metric values and undefined-data behavior, without network calls."""
import copy
import json
import unittest
from unittest.mock import patch

from backend.assistant import tools
from backend.assistant.advanced_stats import PLAYER_METRICS, calculate, shooting_inputs
from test.test_tools import sample


class AdvancedTests(unittest.TestCase):
    def setUp(self):
        self.raw = {'PTS': '30', 'FG': '10-20', '3PT': '4-8', 'FT': '6-8',
                    'OREB': '2', 'DREB': '6', 'AST': '5', 'STL': '2', 'BLK': '1', 'PF': '3', 'TO': '2'}
        self.data = sample()
        group = self.data['boxscore']['players'][0]['statistics'][0]
        group['labels'] = list(self.raw)
        group['athletes'][0]['stats'] = list(self.raw.values())
        self.player = group['athletes'][0]
        fields = ['fieldGoalsMade-fieldGoalsAttempted', 'threePointFieldGoalsMade-threePointFieldGoalsAttempted',
                  'freeThrowsMade-freeThrowsAttempted', 'offensiveRebounds', 'defensiveRebounds', 'totalTurnovers']
        competitors = self.data['header']['competitions'][0]['competitors']
        self.data['boxscore']['teams'] = [
            {'team': competitors[i]['team'], 'statistics': [dict(name=k, displayValue=v) for k, v in zip(fields, values)]}
            for i, values in enumerate([['40-85', '10-30', '20-25', '10', '30', '12'],
                                        ['40-90', '5-20', '20-25', '12', '28', '15']])]
        patcher = patch.object(tools, '_fetch', return_value=self.data)
        patcher.start(); self.addCleanup(patcher.stop)

    def player_result(self, **kwargs):
        return json.loads(tools.player_advanced_stats(player_name='Alex Adams', event_id='100', **kwargs))

    def team_result(self, **kwargs):
        return json.loads(tools.team_advanced_stats(team_name='AAA', event_id='100', **kwargs))

    def test_player_known_values(self):
        result = self.player_result()
        m = result['metrics']
        self.assertEqual(m['efg_pct']['value'], 60)
        self.assertAlmostEqual(m['ts_pct']['value'], 63.776, places=3)
        self.assertEqual(m['game_score']['value'], 25.4)
        self.assertEqual(m['ast_to_ratio']['value'], 2.5)
        self.assertEqual(m['three_point_attempt_rate']['value'], 40)
        self.assertEqual(m['free_throw_rate']['value'], .4)
        self.assertTrue(m['ts_pct']['estimated'])
        self.assertEqual(m['efg_pct']['inputs'], {'FGM': 10, '3PM': 4, 'FGA': 20})
        self.assertEqual(result['headshot'], 'https://example.com/p.png')
        self.assertNotIn('scores', result)

    def test_team_known_values_and_net_rating(self):
        m = self.team_result()['metrics']
        self.assertEqual(m['estimated_possessions']['value'], 101)
        self.assertAlmostEqual(m['offensive_rating']['value'], 110 / 101 * 100, places=3)
        self.assertAlmostEqual(m['defensive_rating']['value'], 105 / 101 * 100, places=3)
        self.assertAlmostEqual(m['net_rating']['value'], 5 / 101 * 100, places=3)
        self.assertAlmostEqual(m['oreb_pct']['value'], 10 / 38 * 100, places=3)
        self.assertAlmostEqual(m['tov_pct']['value'], 12 / 108 * 100, places=3)
        self.assertNotIn('game_score', m)

    def test_single_metric_alias_and_invalid_metric(self):
        self.assertEqual(set(self.player_result(metric='TS%')['metrics']), {'ts_pct'})
        self.assertEqual(set(self.team_result(metric='ORtg')['metrics']), {'offensive_rating'})
        self.assertIn('error', self.player_result(metric='unknown'))
        self.assertIn('error', self.team_result(metric='game_score'))

    def test_zero_and_missing_inputs_do_not_become_zero_metrics(self):
        zero = dict(self.raw, FG='0-0', FT='0-0', TO='0', **{'3PT': '0-0', 'PTS': '0'})
        m = calculate(shooting_inputs(zero), PLAYER_METRICS)
        for name in ('ts_pct', 'efg_pct', 'ast_to_ratio', 'free_throw_rate', 'three_point_attempt_rate'):
            self.assertIsNone(m[name]['value'])
            self.assertIn('unavailable_reason', m[name])
        partial = dict(self.raw, FG='--', FT='NaN-8')
        m = calculate(shooting_inputs(partial), PLAYER_METRICS)
        self.assertIsNone(m['efg_pct']['value'])
        self.assertEqual(m['ast_to_ratio']['value'], 2.5)
        # Percentages above 100 and negative Game Scores are mathematically valid.
        high = dict(self.raw, PTS='3', FG='1-1', FT='0-0', **{'3PT': '1-1'})
        self.assertEqual(calculate(shooting_inputs(high), PLAYER_METRICS, 'ts_pct')['ts_pct']['value'], 150)
        bad_game = dict(self.raw, PTS='0', FG='0-20', FT='0-0', OREB='0', DREB='0', AST='0', STL='0', BLK='0', PF='0', TO='2')
        self.assertEqual(calculate(shooting_inputs(bad_game), PLAYER_METRICS, 'game_score')['game_score']['value'], -16)

    def test_missing_opponent_preserves_independent_metrics(self):
        self.data['boxscore']['teams'].pop()
        m = self.team_result()['metrics']
        self.assertIsNotNone(m['efg_pct']['value'])
        self.assertIsNone(m['offensive_rating']['value'])

    def test_pregame_dnp_and_live(self):
        status = self.data['header']['competitions'][0]['status']['type']
        status.update(state='in', completed=False)
        self.assertTrue(self.player_result()['provisional'])
        self.assertTrue(self.team_result()['provisional'])
        status.update(state='pre')
        self.assertIn('message', self.player_result())
        self.assertIn('message', self.team_result())
        status.update(state='post', completed=True)
        self.player['didNotPlay'] = True
        self.assertIn('message', self.player_result())


if __name__ == '__main__':
    unittest.main()
