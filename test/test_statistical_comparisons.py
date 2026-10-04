"""Comparative rebuttals retain both sides, counterevidence and honest activity states."""
from test.test_debate import review_response
import json
import unittest
from unittest.mock import patch
from backend.activity import ActivityLog
from backend.data.nba import DataUnavailable
from backend.debate.agent import run_debate, evidence_catalog, semantic_review, Draft
from backend.debate.comparison import PANEL_METRICS, paired_request
from backend.debate.tools import DebateTools, DebateConfig, Query, Scope, Compare
from test.test_debate import FakeData, row, tool_call, submit, response

PASS = review_response({'status': 'pass', 'issues': []})


class StatisticalComparisonTests(unittest.TestCase):
    def setUp(self):
        self.config = DebateConfig(supported_player=1, opponent_player=2)
        self.names = {'1':'Alpha', '2':'Beta'}
        self.data = FakeData()
        self.data.career = {1:[row(PTS=160, AST=80)], 2:[row(PTS=200, AST=50)]}

    def run_chat(self, **kwargs):
        return run_debate([dict(role='system',content=''),dict(role='user',content='Beta is way better than Alpha')],
                          'rebuttal', self.config, self.names, data=self.data, **kwargs)

    def test_single_player_lookup_supplies_both_sides_and_promotes_citations(self):
        step = 0
        def model(messages, **kwargs):
            nonlocal step
            if 'response_format' in kwargs:
                payload = json.loads(messages[-1]['content'])
                self.assertEqual(payload['verified_cards'][0]['other_value'], 20)
                self.assertEqual(payload['verified_cards'][1]['other_value'], 5)
                self.assertEqual(len(payload['statistical_comparisons'][0]['rows']), 8)
                return PASS
            step += 1
            if step == 1:
                return tool_call('query_evidence', dict(player_id=1))
            result = json.loads(messages[-1]['content'])
            table = result['statistical_comparisons'][0]
            pts = next(r for r in table['rows'] if r['metric'] == 'PTS')
            self.assertEqual((pts['left_value'],pts['right_value'],pts['relation']), (16,20,'lower'))
            # Simulate a writer still selecting individual facts; the audit includes both sides.
            facts = [f['fact_id'] for f in result['catalog']['facts'] if '; Beta:' not in f['fact']
                     and (f['fact'].startswith('Alpha: PTS ') or f['fact'].startswith('Alpha: AST '))]
            return submit(dict(response='Beta scores more, 20 to 16; Alpha averages more assists, 8 to 5. These are different dimensions.', fact_ids=facts))
        with patch('backend.debate.agent.complete', side_effect=model): result = self.run_chat()
        self.assertEqual(result[2]['review_status'], 'reviewed')
        self.assertEqual(len(result[2]['comparisons']), 1)
        self.assertTrue(all(c.get('other_player') == 'Beta' for c in result[2]['cards']))
        self.assertEqual([l['name'] for l in result[1]].count('compare_players'), 1)
        self.assertEqual({r['metric'] for r in result[2]['comparisons'][0]['rows']}, set(PANEL_METRICS))

    def test_saved_single_player_citation_also_gets_a_comparison(self):
        tools = DebateTools(self.config, data=self.data, names=self.names)
        record = tools.query_evidence(Query(player_id=1))
        catalog = evidence_catalog(tools, [record])
        fact = next(f['fact_id'] for f in catalog['facts'] if f['fact'].startswith('Alpha: AST '))
        with patch('backend.debate.agent.complete', side_effect=[submit(dict(response='Alpha has more assists, 8 to 5.', fact_ids=[fact])), PASS]):
            result = self.run_chat(evidence=tools.evidence)
        self.assertEqual(result[2]['cards'][0]['other_value'], 5)
        self.assertEqual(len(result[2]['comparisons']), 1)

    def test_direct_comparison_includes_unrequested_counterevidence(self):
        tools = DebateTools(self.config, data=self.data, names=self.names)
        result = tools.compare_players(Compare(left_player=1, right_player=2, metrics=['AST']))
        self.assertEqual(result['metrics']['AST']['relation'], 'higher')
        self.assertEqual(result['metrics']['PTS']['relation'], 'lower')
        self.assertIn('TS_PCT', result['metrics'])

    def test_missing_opponent_stays_missing_and_is_not_retried(self):
        original = self.data.fetch
        requests = []
        def fetch(kind, **params):
            requests.append((kind,params))
            if params.get('player_id') == 2:
                raise DataUnavailable('Opponent request unavailable')
            return original(kind, **params)
        step = 0
        def model(messages, **kwargs):
            nonlocal step
            if 'response_format' in kwargs: return PASS
            step += 1
            if step == 1: return tool_call('query_evidence', dict(player_id=1))
            catalog = json.loads(messages[-1]['content'])['catalog']['facts']
            fact = next(f['fact_id'] for f in catalog if f['fact'].startswith('Alpha: AST '))
            return submit(dict(response='Alpha averaged 8 assists; the opponent data is unavailable, so no advantage is established.', fact_ids=[fact]))
        with patch.object(self.data,'fetch',side_effect=fetch), patch('backend.debate.agent.complete',side_effect=model):
            result = self.run_chat()
        rows = result[2]['comparisons'][0]['rows']
        self.assertTrue(all(r['right_value'] is None and not r['comparable'] and r['relation'] is None for r in rows))
        self.assertEqual(sum(p.get('player_id') == 2 for _,p in requests), 1)
        self.assertIn('Opponent request unavailable', result[2]['validation']['data_gaps'])

    def test_configured_seasons_and_targeted_window_do_not_mix_phases(self):
        config = DebateConfig(supported_player=1, opponent_player=2, supported_season=1995, opponent_season=1996)
        record = dict(player_id=1, scope=Scope(start=1995,end=1995,phase='Playoffs').model_dump())
        request = paired_request(config, record)
        self.assertEqual(request['right_scope']['start'], 1996)
        self.assertEqual(request['right_scope']['phase'], 'Playoffs')
        record['scope'] = Scope(start=1998,end=2000).model_dump()
        request = paired_request(config, record)
        self.assertEqual(request['left_scope'], request['right_scope'])

    def test_wrong_numeric_claim_is_neither_fixed_nor_used_to_trigger_retrieval(self):
        tools = DebateTools(self.config, data=self.data)
        record = tools.query_evidence(Query(player_id=1))
        draft = dict(response='Alpha scores 999.', claims=[dict(evidence_id=record['id'], player_id=1,
                     scope=record['scope'], metric='PTS', value=999)])
        with patch.object(self.data,'fetch',side_effect=AssertionError('Invalid claim must not trigger retrieval')), patch('backend.debate.agent.complete',return_value=submit(draft)):
            result = self.run_chat(evidence=tools.evidence)
        self.assertFalse(result[2]['validation']['numeric_valid'])
        self.assertFalse(result[2]['comparisons'])

    def test_review_receives_unfavorable_table_even_if_not_cited(self):
        table = {'rows':[dict(metric='PTS', left_value=16, right_value=20)]}
        with patch('backend.debate.agent.complete',return_value=PASS) as complete:
            semantic_review(Draft(response='Alpha is prolific.'), dict(cards=[],comparisons=[table]),
                            'Beta is better', 'rebuttal', self.config, None)
        messages = complete.call_args.args[0]
        self.assertEqual(json.loads(messages[-1]['content'])['statistical_comparisons'], [table])
        self.assertIn('acknowledge material disadvantages', messages[0]['content'])

    def test_review_feedback_is_not_an_execution_failure(self):
        logs = ActivityLog()
        for status in ('needs_evidence','revise','pass'):
            logs.append(dict(name='review_argument',args={},result=json.dumps(dict(status=status))))
        self.assertEqual([c['status'] for c in logs], ['needs_evidence','needs_revision','completed'])
        logs.append(dict(name='lookup',args={},result='{"error":"Connection failed"}'))
        self.assertEqual(logs[-1]['status'],'failed')


if __name__ == '__main__': unittest.main()
