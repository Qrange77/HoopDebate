"""Offline coverage for automatic selection, bounded investigation and continuation."""
import copy
import json
import time
import unittest
from unittest.mock import patch
from backend.debate.tools import DebateConfig, DebateTools, Claim, Scope, Audit
from backend.debate.agent import run_debate
from backend.debate.research import ResearchIntent, ResearchBatch, research_summary
from test.test_competitive_context import ContextData
from test.test_debate import row, entry, response, submit
from backend.data.nba import DataUnavailable


class ResearchData(ContextData):
    def __init__(self):
        super().__init__()
        self.career = {1:[row(year=f'{y}-{str(y+1)[-2:]}', team=10) for y in range(1995, 2001)],
                       2:[row(year=f'{y}-{str(y+1)[-2:]}', team=20) for y in range(1995, 2001)]}
        self.postseason = copy.deepcopy(self.career)
        self.awards = [{'DESCRIPTION':'NBA Champion','PERSON_ID':2,'SEASON':f'{y}-{str(y+1)[-2:]}'} for y in range(1995,2001)]

    def fetch(self, kind, **params):
        if kind == 'career':
            self.requests.append((kind,params))
            return entry({'SeasonTotalsRegularSeason':self.career[params['player_id']],
                          'SeasonTotalsPostSeason':self.postseason[params['player_id']]})
        if kind == 'team_history':
            self.requests.append((kind,params))
            result = entry({'TeamStats':[dict(TEAM_ID=params['team_id'], YEAR=f'{y}-{str(y+1)[-2:]}',
                TEAM_CITY='City', TEAM_NAME='Team', GP=82, WINS=50, LOSSES=32, WIN_PCT=50/82, PO_WINS=16 if params['team_id']==20 else 4,
                PO_LOSSES=5, NBA_FINALS_APPEARANCE='LEAGUE CHAMPION' if params['team_id']==20 else 'N/A') for y in range(1946,2026)]})
            result['parameters'] = params
            return result
        return super().fetch(kind, **params)


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.data = ResearchData()
        self.config = DebateConfig(supported_player=1, opponent_player=2)
        self.tools = DebateTools(self.config, data=self.data, names={'1':'Alpha','2':'Beta'})

    def plan(self, **kwargs):
        return self.tools.plan_team_context_research(ResearchIntent(argument=kwargs.pop('argument','championships'), **kwargs))

    def batch(self, plan):
        return self.tools.research_team_context(ResearchBatch(plan_id=plan['plan_id']))


    def test_championship_scope_and_unknown_not_zero(self):
        plan = self.plan()
        self.assertEqual(len(plan['queue']),6)
        self.assertEqual([u['left_season'] for u in plan['queue']],list(range(1995,2001)))
        self.assertTrue(any('unknown' in g for g in plan['gaps']))
        self.assertEqual(plan['queue'][0]['title_sides'],['right'])

    def test_bounded_batches_restore_and_followup(self):
        plan = self.plan()
        for turn in range(3):
            batch = self.batch(plan)
            self.assertEqual(len(batch['batch']),2)
            self.assertEqual(len(batch['research']['completed']),2*(turn+1))
            state = json.loads(json.dumps(self.tools.research_state))
            intent = ResearchIntent(argument='championships')
            self.assertEqual(intent.argument,'championships')
            self.tools = DebateTools(self.config,data=self.data,evidence=self.tools.evidence,research_state=state)
            plan = self.tools.plan_team_context_research(intent)
        self.assertFalse(research_summary(self.tools.research_state)['pending'])

    def test_base_both_before_optional_and_directed_lookup_remains_available(self):
        plan=self.plan()
        self.batch(plan)
        reads=[p for k,p in self.data.requests if k=='team_players']
        first_optional=next(i for i,p in enumerate(reads) if p['measure_type_detailed_defense']=='Advanced')
        self.assertEqual({p['team_id'] for p in reads[:first_optional]}, {10,20})
        result=self.tools.run('query_competitive_context',{'player_id':1,'season':1998,'selection_reason':'Inspect an additional season.'})
        self.assertEqual(result['kind'],'competitive_context')
        self.assertEqual(self.tools.research_state['directed_queries'][0]['request']['season'],1998)

    def test_same_year_and_stage_pairing_without_cherry_pick(self):
        plan=self.plan(argument='record')
        expected=[(u['left_season'],u['right_season']) for u in plan['queue']]
        swapped=DebateTools(DebateConfig(supported_player=2,opponent_player=1),data=self.data)
        other=swapped.plan_team_context_research(ResearchIntent(argument='record'))
        self.assertEqual(expected,[(u['right_season'],u['left_season']) for u in other['queue']])
        self.data.career[1]=[row(year='1960-61',team=10),row(year='1962-63',team=10)]
        self.data.postseason[1]=copy.deepcopy(self.data.career[1])
        fresh=DebateTools(self.config,data=self.data)
        era=fresh.plan_team_context_research(ResearchIntent(argument='record'))
        self.assertIn('Nth',era['pairing_rule'])
        self.assertEqual([(u['left_season'],u['right_season']) for u in era['queue']][:3],[(1960,1995),(1962,1996),(None,1997)])

    def test_missing_postseason_separate_regular_supplement(self):
        self.data.postseason[1]=[]
        plan=self.plan(left_season=1995,right_season=1995)
        self.assertEqual([u['phase'] for u in plan['queue']],['Playoffs','Regular Season'])
        batch=self.batch(plan)
        self.assertEqual(len(batch['research']['unavailable']),1)
        self.assertEqual(len(batch['research']['completed']),1)
        self.assertTrue(batch['research']['completed'][0]['supplement'])
        comparisons=[r for r in self.tools.evidence.values() if r['kind']=='comparison']
        self.assertTrue(all(r['left']['scope']['phase']==r['right']['scope']['phase'] for r in comparisons))

    def test_trade_stints_and_team_title_verification(self):
        self.data.postseason[2].append(row(team=30))
        self.data.rosters[30]=copy.deepcopy(self.data.rosters[20])
        plan=self.plan()
        self.assertEqual([u['right_team_id'] for u in plan['queue'][:2]], [20,30])
        batch=self.batch(plan)
        self.assertEqual(batch['batch'][0]['status'],'completed')
        self.assertEqual(batch['batch'][1]['status'],'unavailable')
        self.assertTrue(any('not a verified championship team' in e for e in batch['batch'][1]['errors']))

    def test_explicit_priority_then_resume_parent(self):
        main=self.plan(argument='record')
        self.batch(main)
        # Each batch has its own two-unit cap.
        selected=self.plan(argument='record',left_season=2000,right_season=2000)
        self.assertEqual(selected['parent_plan_id'],main['plan_id'])
        self.batch(selected)
        restored=self.plan(argument='record')
        self.assertEqual(restored['plan_id'],main['plan_id'])
        self.assertEqual(restored['queue'][2]['status'],'pending')

    def test_budget_keeps_pending_and_partial_is_not_comparison(self):
        plan=self.plan(argument='record')
        self.data.remaining_seconds=0
        self.assertFalse(self.batch(plan)['batch'])
        self.assertEqual(len(research_summary(self.tools.research_state)['pending']),6)
        del self.data.remaining_seconds
        self.data.team_fail=20
        batch=self.batch(plan)
        self.assertTrue(batch['research']['unavailable'])
        self.assertFalse(any(r['kind']=='comparison' for r in self.tools.evidence.values()))

    def test_team_outcome_citations_are_phase_scoped(self):
        plan=self.plan(argument='record')
        self.batch(plan)
        record=next(r for r in self.tools.evidence.values() if r['kind']=='team_result')
        self.assertNotIn('PO_WINS',record['values'])
        self.assertIn('Season stints',record['limitations'][0])
        claim=Claim(evidence_id=record['id'],player_id=record['player_id'],metric='WINS',value=50,scope=Scope(**record['scope']))
        self.assertTrue(self.tools.audit_argument(Audit(claims=[claim]))['valid'])

    def test_postseason_sample_uses_playoff_outcomes_even_for_old_evidence(self):
        self.batch(self.plan(argument='record',left_season=1995,right_season=1995,phase='Playoffs'))
        record=next(r for r in self.tools.evidence.values() if r['kind']=='team_result' and r['player_id']==1)
        self.assertEqual(record['values']['PO_WINS']['games'],9)  # four wins + five losses, not 82
        for metric in record['values'].values():
            metric['games']=82  # Legacy snapshot: audit must derive the display sample.
        claim=Claim(evidence_id=record['id'],player_id=1,metric='PO_WINS',value=4,scope=Scope(**record['scope']))
        audited=self.tools.audit_argument(Audit(claims=[claim]))
        self.assertTrue(audited['valid'])
        self.assertEqual(audited['cards'][0]['games'],9)
        self.assertEqual(record['values']['PO_WINS']['games'],82)  # source snapshot remains untouched
        record['values']['PO_LOSSES']['value']=None
        self.assertIsNone(self.tools.audit_argument(Audit(claims=[claim]))['cards'][0]['games'])


    def test_stale_sources_and_incomplete_selection_can_recover(self):
        original=self.data.fetch
        def fetch(kind,**params):
            if kind=='career' and params['player_id']==1:
                raise DataUnavailable('First request timed out')
            value=original(kind,**params)
            value['stale']=True
            return value
        with patch.object(self.data,'fetch',side_effect=fetch):
            partial=self.plan(argument='record')
        self.assertTrue(partial['selection_incomplete'])
        self.assertTrue(partial['sources'][0]['stale'])
        self.assertNotIn('Nth',partial['pairing_rule'])
        recovered=self.plan(argument='record')
        self.assertFalse(recovered['selection_incomplete'])
        self.assertTrue(all(u['left_team_id']==10 for u in recovered['queue']))

    def test_second_plan_can_run_another_batch(self):
        self.batch(self.plan(argument='record'))
        second=self.plan(argument='support',left_season=2000,right_season=2000)
        self.assertEqual(len(self.batch(second)['batch']),1)


    def test_model_requests_never_combine_tools_and_constrained_output(self):
        def model(messages,**kwargs):
            self.assertFalse('tools' in kwargs and 'response_format' in kwargs)
            return response('invalid')
        with patch('backend.debate.agent.complete',side_effect=model):
            run_debate([{'role':'system','content':''},{'role':'user','content':'He has more champions'}],
                       'debate',self.config,{},data=self.data)


if __name__ == '__main__':
    unittest.main()
