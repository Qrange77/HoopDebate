from test.test_debate import review_response
import copy
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.data.nba import NBAData, DataUnavailable
from backend.debate.tools import DebateConfig, DebateTools, CompetitiveContext, CompareContext, Claim, Audit, Scope
from backend.debate.agent import run_debate, evidence_catalog
from test.test_debate import FakeData, entry, row, response, submit


class ContextData(FakeData):
    def __init__(self):
        super().__init__()
        self.career = {1:[row(team=10)], 2:[row(team=20)]}
        self.requests = []
        self.advanced_fail = False
        self.team_fail = None
        self.awards_fail = False
        self.rosters = {10:[dict(row(team=10,PTS=200,MIN=300),PLAYER_ID=1,PLAYER_NAME='Alpha'),
                            dict(row(team=10,PTS=300,MIN=310),PLAYER_ID=3,PLAYER_NAME='Mate'),
                            dict(row(team=10,PTS=100,MIN=200),PLAYER_ID=4,PLAYER_NAME='Other')],
                        20:[dict(row(team=20,PTS=250,MIN=320),PLAYER_ID=2,PLAYER_NAME='Beta'),
                            dict(row(team=20,PTS=150,MIN=200),PLAYER_ID=5,PLAYER_NAME='Rival mate')]}
    def fetch(self,kind,**params):
        self.requests.append((kind,params))
        if kind=='team_players':
            if self.team_fail==params['team_id']:raise DataUnavailable('Team request timed out')
            players=copy.deepcopy(self.rosters[params['team_id']])
            team=dict(TEAM_ID=params['team_id'],TEAM_NAME='Team '+str(params['team_id']),GP=10)
            for k in ['PTS','AST','FGA']:team[k]=sum(p[k] for p in players)
            if params['measure_type_detailed_defense']=='Advanced':
                if self.advanced_fail:raise DataUnavailable('Advanced unavailable')
                players=[dict(PLAYER_ID=r['PLAYER_ID'],GP=r['GP'],USG_PCT=.25) for r in players]
            result=entry({'TeamOverall':[team],'PlayersSeasonTotals':players})
            result['parameters']=params
            return result
        if kind=='awards' and params['player_id']>2:
            if self.awards_fail:raise DataUnavailable('Award budget reached')
            result=entry({'PlayerAwards':[{'PERSON_ID':params['player_id'],'DESCRIPTION':'All-NBA','ALL_NBA_TEAM_NUMBER':'1','SEASON':'1995-96'},
                                         {'PERSON_ID':params['player_id'],'DESCRIPTION':'NBA Most Valuable Player','SEASON':'1990-91'}]})
            result['parameters']=params
            return result
        return super().fetch(kind,**params)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.data=ContextData()
        self.config=DebateConfig(supported_player=1,opponent_player=2)
        self.tools=DebateTools(self.config,data=self.data,names={'1':'Alpha','2':'Beta'})

    def query(self,**kwargs):
        return self.tools.query_competitive_context(CompetitiveContext(selection_reason='Compare the same career season.',player_id=1,season=1995,**kwargs))

    def test_role_shares_ranks_usage_and_top_minutes_teammates(self):
        r=self.query()
        self.assertAlmostEqual(r['values']['PTS_SHARE']['value'],100/3)
        self.assertEqual(r['values']['PTS_TEAM_RANK']['value'],2)
        self.assertEqual(r['values']['USG_PCT']['value'],25)
        self.assertEqual(r['values']['TEAMMATE_PTS_PER_TEAM_GAME']['value'],40)
        self.assertEqual(r['teammates'][0]['stats']['player_name'],'Mate')
        self.assertNotIn('season_honors',r['teammates'][0])
        self.assertFalse(any(kind=='awards' for kind,_ in self.data.requests))
        self.assertIn('TeamID=10',r['sources'][0]['url'])

    def test_traded_season_needs_choice_and_uses_only_selected_team(self):
        self.data.career[1].append(row(team=30,PTS=999))
        result=self.query()
        self.assertEqual(result['status'],'needs_team')
        self.assertEqual(len(result['teams']),2)
        r=self.query(team_id=10)
        self.assertEqual(r['values']['PTS']['value'],20)
        self.assertTrue(any('Traded season' in note for note in r['limitations']))
        with self.assertRaises(ValueError):self.query(team_id=20)

    def test_teammate_citation_cannot_be_assigned_to_focal_player(self):
        r=self.query(phase='Playoffs')
        m=r['teammates'][0]['stats']
        c=Claim(evidence_id=m['id'],metric='PTS',player_id=3,scope=Scope(**m['scope']),value=30)
        result=self.tools.audit_argument(Audit(claims=[c]))
        self.assertTrue(result['valid'])
        self.assertEqual(result['cards'][0]['context']['team_id'],10)
        self.assertIn('Playoffs',result['cards'][0]['scope'])
        self.assertIn('Team 10',result['cards'][0]['scope'])
        for change in [{'player_id':1},{'scope':Scope(start=1995,end=1995)},{'value':31}]:
            self.assertFalse(self.tools.audit_argument(Audit(claims=[c.model_copy(update=change)]))['valid'])
        with self.assertRaises(ValueError):self.tools.query_competitive_context(CompetitiveContext(selection_reason='Compare the same career season.',player_id=3,season=1995))

    def test_partial_failures_preserve_basic_evidence(self):
        self.data.advanced_fail=True;self.data.awards_fail=True
        r=self.query()
        self.assertIsNone(r['values']['USG_PCT']['value'])
        self.assertIsNotNone(r['values']['PTS_SHARE']['value'])
        self.assertNotIn('season_honors',r['teammates'][0])
        self.assertTrue(any('Advanced unavailable' in n for n in r['limitations']))
        self.assertFalse(any(kind=='awards' for kind,_ in self.data.requests))

    def test_comparison_is_bidirectional_and_partial_side_is_not_zero(self):
        p=CompareContext(focus='team_context',selection_reason='Compare the same career season.',left_player=1,right_player=2,left_season=1995,right_season=1995)
        r=self.tools.compare_competitive_context(p)
        self.assertAlmostEqual(r['metrics']['PTS_SHARE']['right_value'],62.5)
        c=Claim(evidence_id=r['id'],metric='PTS_SHARE',player_id=2,scope=Scope(start=1995,end=1995),
                value=62.5,other_value=100/3,relation='higher')
        self.assertTrue(self.tools.audit_argument(Audit(claims=[c]))['valid'])
        self.data.team_fail=20
        fresh=DebateTools(self.config,data=self.data)
        partial=fresh.compare_competitive_context(p)
        self.assertEqual(partial['status'],'partial')
        self.assertEqual(partial['left']['kind'],'competitive_context')
        self.assertEqual(partial['right']['status'],'unavailable')

    def test_zero_denominator_missing_history_and_tied_ranks(self):
        for r in self.data.rosters[10]:r['FGA']=0;r['PTS']=100
        result=self.query()
        self.assertIsNone(result['values']['FGA_SHARE']['value'])
        self.assertEqual(result['values']['PTS_TEAM_RANK']['value'],1)
        with self.assertRaises(DataUnavailable):
            self.tools.query_competitive_context(CompetitiveContext(selection_reason='Compare the same career season.',player_id=1,season=1960))

    def test_restored_snapshot_and_three_turn_citations(self):
        r=self.query()
        claim=dict(evidence_id=r['id'],metric='PTS_TEAM_RANK',player_id=1,scope=r['scope'],value=2)
        draft=json.dumps({'response':'Alpha ranked 2 in team scoring by season total. Team trophies alone do not settle individual credit.', 'claims':[claim]})
        messages=[{'role':'system','content':''}]
        evidence,state=self.tools.evidence,None
        for mode in ['debate','rebuttal','rebuttal']:
            messages.append({'role':'user','content':'What was his role in 1995-96?'})
            with patch('backend.debate.agent.complete',side_effect=[submit(draft),review_response({'status': 'pass', 'issues': []})]):
                text,logs,result,evidence,state=run_debate(messages,mode,self.config,{'1':'Alpha','2':'Beta'},evidence=evidence,state=state,data=self.data)
            self.assertEqual(result['review_status'],'reviewed')
            self.assertEqual(result['cards'][0]['label'],'Team rank by total scoring')
        restored=DebateTools(self.config,evidence=evidence,data=self.data)
        with patch.object(self.data,'fetch',side_effect=AssertionError('Saved context should be reused')):
            self.assertEqual(restored.query_competitive_context(CompetitiveContext(selection_reason='Compare the same career season.',player_id=1,season=1995)),r)


    def test_shortlist_includes_scorer_with_fewer_minutes(self):
        self.data.rosters[10] += [dict(row(PTS=50,MIN=250),PLAYER_ID=6,PLAYER_NAME='Minutes'),
                                 dict(row(PTS=400,MIN=150),PLAYER_ID=7,PLAYER_NAME='Scorer')]
        r=self.query()
        ids=[m['stats']['player_id'] for m in r['teammates']]
        self.assertIn(7,ids)
        self.assertLessEqual(len(ids),5)


    def test_fact_ids_resolve_to_exact_server_claims_and_reject_unknown_ids(self):
        record=self.tools.compare_competitive_context(CompareContext(focus='team_context',selection_reason='Compare the same career season.',left_player=1,right_player=2,left_season=1995,right_season=1995))
        catalog=evidence_catalog(self.tools,[record])
        chosen=next(f for f in catalog['facts'] if 'Usage' in f['fact'])
        draft=json.dumps({'response':'Both have 25% usage in this team-season sample. That alone does not prove who carried the team.',
                          'fact_ids':[chosen['fact_id']]})
        messages=[{'role':'system','content':''},{'role':'user','content':'What about that?'}]
        with patch('backend.debate.agent.complete',side_effect=[submit(draft),review_response({'status': 'pass', 'issues': []})]):
            output=run_debate(messages,'rebuttal',self.config,{'1':'Alpha','2':'Beta'},evidence=self.tools.evidence,data=self.data)
        self.assertEqual(output[2]['review_status'],'reviewed')
        self.assertEqual(output[2]['claims'][0],self.tools.fact_claims[chosen['fact_id']].model_dump())
        with patch('backend.debate.agent.complete',return_value=response('{"response":"Made up fact","fact_ids":["invented"]}')):
            output=run_debate(messages,'rebuttal',self.config,{'1':'Alpha','2':'Beta'},evidence=self.tools.evidence,data=self.data)
        self.assertEqual(output[2]['claims'],[])
        self.assertEqual(output[2]['review_status'],'limited')


class CacheCapacityTests(unittest.TestCase):
    def test_old_database_migration_preserves_cached_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cache.db'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE responses (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
                db.execute('INSERT INTO responses VALUES (?,?)',('old','{}'))
            with NBAData(path)._connection() as db:
                self.assertEqual(db.execute("SELECT body FROM responses WHERE key='old'").fetchone()[0],'{}')
                self.assertIn('last_access',[r[1] for r in db.execute('PRAGMA table_info(responses)')])

    def test_lru_eviction_reclaims_disk_and_keeps_recent_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cache.db'
            limit=64*1024
            payload=({'resultSets':['a'*7000]}, {'rows':['b'*7000]})
            def request(player):
                return NBAData(path,max_cache_bytes=limit).fetch('career',player_id=player)
            with patch.object(NBAData,'_live',return_value=payload):
                for ident in [1,2,3]:request(ident)
                request(1)
                request(4)
            with closing(sqlite3.connect(path)) as db, db:
                keys=[json.loads(r[0])[1]['player_id'] for r in db.execute('SELECT key FROM responses')]
            self.assertIn(1,keys);self.assertIn(4,keys);self.assertNotIn(2,keys)
            self.assertLessEqual(path.stat().st_size,limit)

    def test_oversized_response_is_not_persisted_or_discarded_this_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cache.db'
            data=NBAData(path,max_cache_bytes=32*1024)
            with patch.object(NBAData,'_live',return_value=({'resultSets':[1]}, {'big':'x'*50000})) as live:
                first=data.fetch('career',player_id=1)
                self.assertEqual(data.fetch('career',player_id=1),first)
                live.assert_called_once()
            with closing(sqlite3.connect(path)) as db, db:self.assertEqual(db.execute('SELECT count(*) FROM responses').fetchone()[0],0)
            self.assertLessEqual(path.stat().st_size,32*1024)
