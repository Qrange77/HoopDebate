"""The model selects representative teammates; the tool verifies rather than ranks them."""
from test.test_debate import review_response
import json
import unittest
from unittest.mock import patch
from backend.debate.agent import run_debate, evidence_catalog
from backend.debate.tools import DebateTools, DebateConfig, Audit
from test.test_competitive_context import ContextData
from test.test_debate import row, tool_call, submit, response


class KeyTeammateTests(unittest.TestCase):
    def setUp(self):
        self.data = ContextData()
        self.config = DebateConfig(supported_player=1, opponent_player=2)
        self.names = {'1':'Alpha','2':'Beta'}
        self.tools = DebateTools(self.config, data=self.data, names=self.names)
        self.args = dict(left_player=1,right_player=2,left_season=1995,right_season=1995,
                         selection_reason='Compare both supporting players in the same discussed season.')

    def discover(self, **extra):
        return self.tools.run('compare_competitive_context', dict(self.args, **extra))

    def select(self, **extra):
        return self.discover(**dict(left_teammate_id=3,right_teammate_id=5,
            teammate_selection_reason='Both are leading secondary scorers in this sample; inspect their efficiency and availability as well.', **extra))

    def test_discovery_lists_both_non_focal_rosters_without_selecting_a_winner(self):
        result = self.discover()
        self.assertEqual(result['status'], 'select_teammates')
        self.assertEqual([r['player_id'] for r in result['left']['candidates']], [3,4])
        self.assertEqual([r['player_id'] for r in result['right']['candidates']], [5])
        self.assertFalse(any(kind == 'awards' for kind,_ in self.data.requests))
        self.assertNotIn('winner', result)
        self.assertNotIn('selected_teammate',result['left'])
        self.assertTrue(self.tools.research_state['directed_queries'][0]['evidence_ids'])
        self.assertTrue(self.tools.research_state['directed_queries'][0]['sources'])

    def test_selected_pair_has_exact_stats_attribution_and_no_award_queries(self):
        result = self.select()
        self.assertEqual(result['focus'],'key_teammates')
        self.assertEqual(result['left']['player_id'],3)
        self.assertEqual(result['right']['player_id'],5)
        self.assertEqual(result['left']['context']['focal_player_id'],1)
        self.assertEqual(result['right']['context']['focal_player_id'],2)
        self.assertEqual(result['metrics']['PTS']['left_value'],30)
        self.assertEqual(result['metrics']['PTS']['right_value'],15)
        self.assertNotIn('teammate_honors',result)
        catalog=evidence_catalog(self.tools,[result])
        claims=[self.tools.fact_claims[f['fact_id']] for f in catalog['facts']]
        compared=next(c for c in claims if c.evidence_id==result['id'] and c.metric=='PTS')
        self.assertTrue(self.tools.audit_argument(Audit(claims=[compared]))['valid'])
        self.assertFalse(self.tools.audit_argument(Audit(claims=[compared.model_copy(update={'player_id':1})]))['valid'])
        self.assertFalse(any(c.metric.startswith(('ALL_', 'MVP')) for c in claims))
        awards=[p['player_id'] for k,p in self.data.requests if k=='awards']
        self.assertEqual(awards,[])

    def test_cached_teammate_awards_are_not_offered_as_facts_or_displayed(self):
        from backend.debate.comparison import comparison_summary
        from backend.debate.tools import scope_text
        result = self.select()
        legacy = dict(result['left'], id='ev_legacy_award', kind='awards', award_categories=True,
            values={'MVP':dict(result['left']['values']['PTS'], value=1, label='MVP')})
        self.tools.evidence[legacy['id']] = legacy
        result['teammate_honors'] = {'left':legacy, 'sources':[{'url':'legacy-awards'}]}
        catalog = evidence_catalog(self.tools,[result,legacy])
        self.assertTrue(any(f['metric']=='PTS' for f in catalog['facts']))
        self.assertFalse(any(f['metric']=='MVP' for f in catalog['facts']))
        request = {side+'_player':result[side]['player_id'] for side in ('left','right')}
        request.update({side+'_scope':result[side]['scope'] for side in ('left','right')})
        summary = comparison_summary(result,request,self.names,scope_text)
        self.assertNotIn('honors',summary)
        self.assertNotIn({'url':'legacy-awards'},summary['sources'])

    def test_no_selection_can_include_focal_player_or_only_one_side(self):
        for extra in [dict(left_teammate_id=1,right_teammate_id=5,teammate_selection_reason='Invalid'),
                      dict(left_teammate_id=3),dict(left_teammate_id=3,right_teammate_id=5)]:
            with self.subTest(extra=extra):self.assertEqual(self.discover(**extra)['status'],'unavailable')
        self.assertFalse(self.data.requests)

    def test_wrong_roster_does_not_create_a_comparison(self):
        result = self.discover(left_teammate_id=5,right_teammate_id=3,teammate_selection_reason='Swapped rosters incorrectly.')
        self.assertEqual(result['status'],'partial')
        self.assertIn('roster',result['left']['error'])
        self.assertNotIn('metrics',result)

    def test_model_can_select_a_candidate_outside_old_scoring_minutes_shortlist(self):
        for ident in range(6,12):
            self.data.rosters[10].append(dict(row(team=10,PTS=400,MIN=500),PLAYER_ID=ident,PLAYER_NAME=str(ident)))
        discovered=self.discover()
        self.assertIn(4,[c['player_id'] for c in discovered['left']['candidates']])
        result=self.discover(left_teammate_id=4,right_teammate_id=5,
            teammate_selection_reason='Inspect a lower-scoring defensive-role candidate; do not claim he is strongest without additional evidence.')
        self.assertEqual(result['left']['player_id'],4)
        self.assertEqual(result['metrics']['PTS']['relation'],'lower')
        self.assertNotIn('winner',result)

    def test_unavailable_award_service_is_never_called(self):
        self.data.awards_fail=True
        result=self.select()
        self.assertEqual(result['metrics']['PTS']['left_value'],30)
        self.assertNotIn('teammate_honors',result)
        self.assertFalse(any('honor' in note.lower() for note in result['limitations']))
        self.assertFalse(any(kind=='awards' for kind,_ in self.data.requests))

    def test_playoff_comparison_preserves_statistical_scope(self):
        result=self.select(phase='Playoffs')
        self.assertEqual(result['left']['scope']['phase'],'Playoffs')
        self.assertEqual(result['right']['scope']['phase'],'Playoffs')
        self.assertFalse(any(kind=='awards' for kind,_ in self.data.requests))

    def test_traded_season_requires_team_choice(self):
        self.data.career[1].append(row(team=30))
        result=self.discover()
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['left']['status'],'needs_team')
        self.assertEqual(self.discover(left_team_id=10)['status'],'select_teammates')

    def test_reversing_sides_preserves_unfavorable_evidence(self):
        forward=self.select()
        reverse=self.tools.run('compare_competitive_context',dict(self.args,left_player=2,right_player=1,
            left_teammate_id=5,right_teammate_id=3,teammate_selection_reason='Same representative choices with reversed focal sides.'))
        self.assertEqual(forward['metrics']['PTS']['relation'],'higher')
        self.assertEqual(reverse['metrics']['PTS']['relation'],'lower')
        self.assertEqual(reverse['selection']['left_focal_name'],'Beta')

    def test_agent_discovery_selection_review_and_panel_use_the_same_pair(self):
        step=0
        def model(messages,**kwargs):
            nonlocal step
            if 'response_format' in kwargs:
                payload=json.loads(messages[-1]['content'])
                panel=payload['statistical_comparisons'][0]
                self.assertEqual(panel['kind'],'key_teammates')
                self.assertEqual(panel['left_player'],'Mate')
                self.assertEqual(panel['selection']['left_focal_name'],'Alpha')
                return review_response({'status': 'pass', 'issues': []})
            step+=1
            if step==1:return tool_call('compare_competitive_context',self.args)
            if step==2:
                feedback=json.loads(messages[-1]['content'])
                self.assertEqual(feedback['status'],'select_teammates')
                self.assertEqual(len(feedback['left']['candidates']),2)
                return tool_call('compare_competitive_context',dict(self.args,left_teammate_id=3,right_teammate_id=5,
                    teammate_selection_reason='Both are representative secondary scorers; this does not settle whole-roster strength.'))
            result=json.loads(messages[-1]['content'])
            fact=next(f['fact_id'] for f in result['catalog']['facts'] if f['fact'].startswith('Mate: PTS 30') and '; Rival mate:' in f['fact'])
            return submit(dict(response='In the checked season Mate averaged 30 points against Rival mate’s 15; this qualifies individual credit for team success, not the validity of the wins.',fact_ids=[fact]))
        with patch('backend.debate.agent.complete',side_effect=model):
            result=run_debate([dict(role='system',content=''),dict(role='user',content='Discuss how their teammates contributed.')],
                'debate',self.config,self.names,data=self.data)
        self.assertEqual(len(result[2]['comparisons']),1)
        self.assertEqual(result[2]['comparisons'][0]['right_player'],'Rival mate')
        self.assertNotIn('honors',result[2]['comparisons'][0])
        self.assertEqual(result[2]['review_status'],'reviewed')
        self.assertEqual(len(result[2]['research']['directed_queries']),2)
