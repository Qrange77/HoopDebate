"""Offline behavioral tests for historical evidence, audits, modes and rollback."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
import app
from backend.debate import agent as debate_agent
from backend.debate.tools import (DebateConfig, DebateTools, Scope, Query, Compare, Counterexample, Edges, Claim, Audit)
from backend.data.nba import NBAData, DataUnavailable, aggregate, season_rows


def row(year='1995-96', team=1, **values):
    r={'SEASON_ID':year,'TEAM_ID':team,'LEAGUE_ID':'00','GP':10,'PTS':200,'FGA':100,'FGM':60,
       'FG3M':10,'FG3A':20,'FTM':70,'FTA':80,'REB':60,'AST':50,'STL':10,'BLK':5,'TOV':20,
       'MIN':300,'OREB':20,'DREB':40}
    r.update(values)
    return r


def entry(data):
    return {'data':data,'source_url':'https://stats.nba.com/stats/test','parameters':{},
            'fetched_at':'2026-01-01T00:00:00+00:00','stale':False}


class FakeData:
    def __init__(self):
        self.career={1:[row()],2:[row(PTS=160,AST=80,TOV=10)]}
        self.awards=[{'DESCRIPTION':'NBA Most Valuable Player','SEASON':'1995-96'},
                     {'DESCRIPTION':'NBA Most Valuable Player','SEASON':'1995-96'}]
        self.fail=False
    def fetch(self,kind,**params):
        if self.fail:
            raise DataUnavailable('Offline')
        if kind=='career':
            return entry({'SeasonTotalsRegularSeason':self.career[params['player_id']],
                          'SeasonTotalsPostSeason':self.career[params['player_id']]})
        if kind=='awards': return entry({'PlayerAwards':self.awards})
        if kind=='league': return entry({'LeagueDashTeamStats':[]})
        if kind=='games': return entry({'PlayerGameLog':[dict(row(PTS=40),Game_ID='nba_1',GAME_DATE='JAN 1',MATCHUP='A vs B'),
                                                        dict(row(PTS=12),Game_ID='nba_2',GAME_DATE='JAN 2',MATCHUP='A vs C')]})
    def resolve(self,q): return {'players':[],'resolved':False,'complete':True}


def response(content=None,calls=None):
    m=SimpleNamespace(content=content,tool_calls=calls)
    m.model_dump=lambda: {'role':'assistant','content':content,'tool_calls':[
        {'id':c.id,'type':'function','function':vars(c.function)} for c in calls or []]}
    return m


def assessment(**overrides):
    """Complete live-review fixture; individual behavior tests set actual findings."""
    return dict(dict(relevance='limited_concession', evidence_followthrough='not_needed',
        reason='An explicitly limited answer does not assert an empirical advantage.',
        target_claim='The evidence settles individual superiority.', evidence_direction='inconclusive',
        evidence_ids=[], response_strategy='qualified_answer', conclusion_supported=True,
        conclusion_addresses_objection=True, objection_faithful=True, advantage_example_count=0,
        team_success_argument=False, required_support_scope='none', single_pair_user_quote=''), **overrides)


def review_response(payload=None):
    payload = dict(payload or {'status':'pass', 'issues':[]})
    payload.setdefault('argument_assessment', assessment())
    payload.setdefault('user_attributions', [])
    return response(json.dumps(payload))


def tool_call(name, args):
    if isinstance(args, str): args=json.loads(args)
    return response(calls=[SimpleNamespace(id='call_'+name, function=SimpleNamespace(name=name,arguments=json.dumps(args)))])


def submit(draft):
    return tool_call('submit_argument',draft)


class HistoricalTests(unittest.TestCase):
    def test_traded_season_uses_total_once_and_recomputes_rates(self):
        rows=season_rows([row(team=1,PTS=100,GP=5),row(team=2,PTS=100,GP=5),row(team=0),row(year='1996-97',GP=20,PTS=100,FGA=10,FGM=1)])
        metrics=aggregate(rows)
        self.assertEqual(len(rows),2)
        self.assertEqual(metrics['PTS']['value'],10)
        self.assertAlmostEqual(metrics['FG_PCT']['value'],100*61/110)
        self.assertAlmostEqual(metrics['TS_PCT']['value'],100*300/(2*(110+.44*160)))

    def test_team_split_fallback_and_duplicates(self):
        a=row(team=1,GP=3,PTS=60)
        rows=season_rows([a,a,row(team=2,GP=7,PTS=140)])
        self.assertEqual(aggregate(rows)['PTS']['value'],20)
        self.assertEqual(aggregate(rows)['GP']['value'],10)

    def test_historical_unrecorded_not_zero(self):
        old=row(year='1960-61',STL=0,BLK=0,TOV=0,FG3M=0)
        metrics=aggregate(season_rows([old,row()]))
        self.assertFalse(metrics['STL']['complete'])
        self.assertEqual(metrics['STL']['covered_seasons'],['1995-96'])
        self.assertIsNone(metrics['EFG_PCT']['value'])
        self.assertIsNone(aggregate(season_rows([old]))['STL']['value'])

    def test_zero_denominator_and_missing_inputs(self):
        metrics=aggregate(season_rows([row(FGA=0,FTA=0,FGM=0,PTS=0)]))
        self.assertIsNone(metrics['TS_PCT']['value'])
        self.assertIsNone(metrics['FG_PCT']['value'])
        self.assertIsNone(aggregate(season_rows([row(FTA=None)]))['TS_PCT']['value'])

    def test_cache_hit_stale_fallback_and_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cache.db'
            live=({'resultSets':[1]}, {'rows':[{'id':1}]})
            with patch.object(NBAData,'_live',return_value=live) as fetch:
                first=NBAData(path).fetch('career',player_id=1)
                cached=NBAData(path).fetch('career',player_id=1)
                self.assertEqual(first,cached)
                fetch.assert_called_once()
            with patch('backend.data.nba.time.time',return_value=time.time()+40*86400), patch.object(NBAData,'_live',side_effect=TimeoutError):
                stale=NBAData(path).fetch('career',player_id=1)
                self.assertTrue(stale['stale'])
                self.assertEqual(stale['fetched_at'],first['fetched_at'])
                self.assertIn('refresh_error',stale)
            data=NBAData(path,budget=-1)
            with patch.object(NBAData,'_live') as fetch:
                with self.assertRaises(DataUnavailable): data.fetch('career',player_id=999)
                fetch.assert_not_called()

    def test_failed_lookup_not_retried_in_same_turn(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(NBAData,'_live',side_effect=TimeoutError) as fetch:
            data=NBAData(Path(folder)/'cache.db')
            for _ in range(2):
                with self.assertRaises(DataUnavailable): data.fetch('career',player_id=1)
            fetch.assert_called_once()

    def test_alias_ambiguity_and_unknown(self):
        data=NBAData()
        directory=[{'id':893,'name':'Michael Jordan'},{'id':2,'name':'Michael Smith'}]
        with patch.object(data,'directory',return_value=directory):
            self.assertTrue(data.resolve('MJ')['resolved'])
            self.assertFalse(data.resolve('Michael')['resolved'])
            self.assertEqual(len(data.resolve('Michael')['players']),2)
            self.assertEqual(data.resolve('nobody')['players'],[])


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.data=FakeData()
        self.config=DebateConfig(supported_player=1,opponent_player=2)
        self.tools=DebateTools(self.config,data=self.data,names={'1':'Alpha','2':'Beta'})
    def compare(self):
        return self.tools.compare_players(Compare(left_player=1,right_player=2))
    def claim(self,record,**kwargs):
        values=dict(evidence_id=record['id'],metric='PTS',player_id=1,scope=Scope(),value=20,other_value=16,relation='higher')
        values.update(kwargs)
        return Claim(**values)
    def test_valid_and_invalid_claims(self):
        r=self.compare()
        self.assertTrue(self.tools.audit_argument(Audit(claims=[self.claim(r)]))['valid'])
        for changes in ({'evidence_id':'invented'},{'value':21},{'player_id':2},{'relation':'lower'},
                        {'scope':Scope(phase='Playoffs')},{'other_value':None}):
            self.assertFalse(self.tools.audit_argument(Audit(claims=[self.claim(r,**changes)]))['valid'])
        reverse=self.claim(r,player_id=2,value=16,other_value=20,relation='lower')
        self.assertTrue(self.tools.audit_argument(Audit(claims=[reverse]))['valid'])
    def test_incompatible_scopes_and_players(self):
        for args in ({'right_scope':Scope(phase='Playoffs')},{'right_scope':Scope(basis='totals')},
                     {'right_scope':Scope(start=1995,end=1995)},{'right_player':1}):
            with self.assertRaises(ValueError): Compare(**dict({'left_player':1,'right_player':2},**args))
        self.assertIn('error',self.tools.run('query_evidence',{'player_id':3}))
        with self.assertRaises(ValueError): DebateConfig(supported_player=1,opponent_player=1)
    def test_partial_historical_comparison_not_certified(self):
        self.data.career[1]=[row(year='1960-61',STL=0)]
        r=self.tools.compare_players(Compare(left_player=1,right_player=2,metrics=['STL']))
        self.assertFalse(r['metrics']['STL']['comparable'])
        self.assertFalse(self.tools.audit_argument(Audit(claims=[self.claim(r,metric='STL',value=0)]))['valid'])
    def test_missing_requested_season_prevents_full_range_comparison(self):
        scope=Scope(start=1995,end=1996)
        self.data.career[2].append(row(year='1996-97'))
        result=self.tools.compare_players(Compare(left_player=1,right_player=2,left_scope=scope,right_scope=scope))
        self.assertFalse(result['metrics']['PTS']['comparable'])
        self.assertTrue(any('1996-97' in note for note in result['limitations']))
    def test_missing_metric_can_be_cited_as_unavailable_but_not_zero(self):
        self.data.career[1]=[row(year='1960-61',BLK=0)]
        r=self.tools.query_evidence(Query(player_id=1))
        claim=Claim(evidence_id=r['id'],metric='BLK',player_id=1,scope=Scope(),value=None)
        audit=self.tools.audit_argument(Audit(claims=[claim]))
        self.assertTrue(audit['valid'])
        self.assertIsNone(audit['cards'][0]['value'])
        claim.value=0
        self.assertFalse(self.tools.audit_argument(Audit(claims=[claim]))['valid'])

    def test_both_sides_returned_and_lower_turnovers(self):
        result=self.tools.find_comparative_edges(Edges(left_player=1,right_player=2))
        self.assertIn('PTS',result['advantages'])
        self.assertIn('AST',result['disadvantages'])
        self.assertIn('TOV',result['disadvantages'])
    def test_counterexample_bounds_and_predicate(self):
        result=self.tools.find_counterexamples(Counterexample(player_id=1,scope=Scope(start=1995,end=1995),metric='PTS',comparison='gt',threshold=30))
        self.assertEqual(result['checked_games'],2)
        self.assertEqual(result['matching_games'],1)
        self.assertEqual(result['matches'][0]['nba_game_id'],'nba_1')
        with self.assertRaises(ValueError): self.tools.query_evidence(Query(player_id=1,kind='games'))
    def test_awards_deduplicated_and_absence_not_zero(self):
        r=self.tools.query_evidence(Query(player_id=1,kind='awards'))
        self.assertEqual(r['values']['NBA Most Valuable Player']['value'],1)
        self.assertNotIn('NBA Defensive Player of the Year',r['values'])
        self.data.awards=[]
        with self.assertRaises(DataUnavailable): self.tools.query_evidence(Query(player_id=1,kind='awards'))
    def test_missing_era_baseline_still_returns_raw_stats(self):
        r=self.tools.query_evidence(Query(player_id=1,scope=Scope(start=1995,end=1995)))
        self.assertIn('TS_PCT',r['values'])
        self.assertNotIn('REL_TS',r['values'])
        self.assertTrue(any('Era baseline' in text for text in r['limitations']))
    def test_complete_era_baseline(self):
        original=self.data.fetch
        def fetch(kind,**kwargs):
            if kind=='league':return entry({'LeagueDashTeamStats':[row(team=i) for i in range(29)]})
            return original(kind,**kwargs)
        with patch.object(self.data,'fetch',side_effect=fetch):
            r=self.tools.query_evidence(Query(player_id=1,scope=Scope(start=1995,end=1995)))
        self.assertAlmostEqual(r['values']['REL_TS']['value'],0)


class HarnessTests(unittest.TestCase):
    setUp = EvidenceTests.setUp
    compare = EvidenceTests.compare
    claim = EvidenceTests.claim

    def test_mandatory_audit_repair_and_no_bad_draft_in_history(self):
        r=self.compare()
        bad={'response':'Alpha averaged 999 points.','claims':[self.claim(r,value=999).model_dump()]}
        good={'response':'Alpha has the scoring edge here: 20 points per game versus 16. That does not settle every part of the debate.',
              'claims':[self.claim(r).model_dump()],'topic':'Scoring','conceded_claim_indexes':[]}
        messages=[{'role':'system','content':''},{'role':'user','content':'Alpha is worse'}]
        with patch.object(debate_agent,'complete',side_effect=[submit(bad),submit(good),review_response({'status': 'pass', 'issues': []})]):
            output=debate_agent.run_debate(messages,'debate',self.config,{'1':'Alpha','2':'Beta'},self.tools.evidence,data=self.data)
        self.assertEqual(output[2]['review_status'],'reviewed')
        self.assertEqual(len([log for log in output[1] if log['name']=='audit_argument']),2)
        self.assertNotIn('999',json.dumps(messages))
    def test_failed_debate_review_does_not_dump_unapproved_examples(self):
        r=self.compare()
        draft={'response':'Alpha is objectively the best ever.','claims':[self.claim(r).model_dump()]}
        with patch.object(debate_agent,'complete',side_effect=[submit(draft),review_response({'status': 'revise', 'issues': [{'clause': 'Alpha is objectively the best ever.', 'reason': 'Overstatement'}]})]*3):
            output=debate_agent.run_debate([{'role':'system','content':''}],'debate',self.config,{'1':'Alpha','2':'Beta'},self.tools.evidence,data=self.data)
        self.assertEqual(output[2]['review_status'],'limited')
        self.assertNotIn('objectively',output[0])
        self.assertNotIn('Alpha: PTS 20',output[0])
        self.assertFalse(output[2]['cards'])
        self.assertFalse(output[4].get('used_fact_keys'))
    def test_roles_and_three_turn_concessions(self):
        r=self.compare()
        draft={'response':'Alpha does score more: 20 versus 16 points per game. I concede that scoring advantage.',
               'claims':[self.claim(r).model_dump()],'topic':'Scoring','conceded_claim_indexes':[0]}
        for mode,expected in [('debate','You defend NBA player 2'),('rebuttal','You defend NBA player 1')]:
            state=None
            messages=[{'role':'system','content':''}]
            evidence=self.tools.evidence
            with patch.object(debate_agent,'complete',side_effect=lambda *a,**kw:review_response({'status': 'pass', 'issues': []}) if kw.get('response_format',{}).get('type') == 'json_schema' else submit(draft)):
                for _ in range(3):
                    messages.append({'role':'user','content':'Argue scoring'})
                    output=debate_agent.run_debate(messages,mode,self.config,{'1':'Alpha','2':'Beta'},evidence,state,data=self.data)
                    evidence,state=output[3:]
            self.assertIn(expected,messages[0]['content'])
            self.assertEqual(len(state['concessions']),1)
            self.assertEqual(len(state['topics']),1)


class DebateSessionTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        p=patch.object(app,'HISTORY_DIR',Path(self.folder.name));p.start();self.addCleanup(p.stop)
        p=patch.object(NBAData,'directory',return_value=[{'id':1,'name':'Alpha'},{'id':2,'name':'Beta'}]);p.start();self.addCleanup(p.stop)
        self.client=TestClient(app.app)
        self.config=DebateConfig(supported_player=1,opponent_player=2).model_dump()
    def test_config_lock_restore_and_failed_state_rollback(self):
        state={'topics':['Scoring'],'concessions':['Verified concession']}
        def run(messages,*args,**kwargs):
            messages.append({'role':'assistant','content':'Checked reply'})
            return 'Checked reply',[],{'claims':[],'cards':[],'review_status':'limited'}, {'ev_1':{'value':5}},state
        with patch.object(app,'run_debate',side_effect=run):
            r=self.client.post('/chat',json={'message':'First','mode':'debate','debate_config':self.config}).json()
            sid=r['session_id']
            saved=self.client.get('/sessions/'+sid).json()
            self.assertEqual(saved['mode'],'debate');self.assertEqual(saved['debate_config'],self.config)
            self.assertEqual(saved['player_names'],{'1':'Alpha','2':'Beta'})
            self.assertEqual(self.client.post('/chat',json={'message':'Next','session_id':sid,'mode':'rebuttal'}).status_code,409)
            changed=dict(self.config,phase='Playoffs')
            self.assertEqual(self.client.post('/chat',json={'message':'Next','session_id':sid,'debate_config':changed}).status_code,409)
        def fail(*args,**kwargs):
            args[0].append({'role':'assistant','content':'partial'})
            raise RuntimeError('Offline')
        with patch.object(app,'run_debate',side_effect=fail):
            failed=self.client.post('/chat',json={'message':'Next','session_id':sid}).json()
        self.assertTrue(failed['failed'])
        record=app.read_history(self.client.cookies['nba_browser'],sid)
        self.assertEqual(record['debate_state'],state)
        self.assertFalse(any(m.get('content')=='partial' for m in record['messages']))
        self.assertEqual(record['evidence'],{'ev_1':{'value':5}})
    def test_new_modes_require_valid_config(self):
        self.assertEqual(self.client.post('/chat',json={'message':'Hi','mode':'debate'}).status_code,400)
        self.assertEqual(self.client.post('/chat',json={'message':'Hi','mode':'assistant','debate_config':self.config}).status_code,400)
        bad=dict(self.config,supported_player=3)
        self.assertEqual(self.client.post('/chat',json={'message':'Hi','mode':'rebuttal','debate_config':bad}).status_code,400)
