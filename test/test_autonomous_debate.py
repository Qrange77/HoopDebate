"""Behavioral contract for autonomous retrieval and the submit/review feedback loop."""
from test.test_debate import review_response
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import app
from backend.data.nba import NBAData, DataUnavailable
from backend.debate.agent import run_debate, semantic_review, Draft, evidence_catalog
from backend.debate.tools import DebateTools, DebateConfig, Query
from test.test_debate import response, submit, tool_call
from test.test_team_research import ResearchData
from test.test_activity import events

PASS=review_response({'status': 'pass', 'issues': []})

def feedback(status='needs_evidence', reason='The specific championship assertion needs a source.'):
    return response(json.dumps({'status':status,'issues':[{'clause':'Beta has a championship.','reason':reason,'missing_evidence':'A championship award record for Beta.'}]}))

class AutonomousDebateTests(unittest.TestCase):
    def setUp(self):
        self.config=DebateConfig(supported_player=1,opponent_player=2)
        self.data=ResearchData()
        self.names={'1':'Alpha','2':'Beta'}
    def run_chat(self, text='SC has more champions', **kwargs):
        return run_debate([{'role':'system','content':''},{'role':'user','content':text}], 'rebuttal',self.config,self.names,data=self.data,**kwargs)

    def test_keywords_do_not_execute_queries_or_force_honors(self):
        for text in ['SC has more champions','他冠军更多','Better record','More playoff appearances','Compare efficiency']:
            with self.subTest(text=text), patch.object(self.data,'fetch',side_effect=AssertionError('No query chosen')), patch('backend.debate.agent.complete',side_effect=[submit({'response':'Team success alone does not isolate individual credit.'}),PASS]):
                result=self.run_chat(text)
                self.assertEqual(result[2]['validation']['submissions'],1)
                self.assertIsNone(result[2]['research'])
                self.assertIsNone(result[2]['honors'])
                self.assertEqual([l['name'] for l in result[1]],['audit_argument','review_argument','submit_argument'])

    def test_review_feedback_allows_additional_honors_lookup_and_pass(self):
        index=0
        def model(messages,**kwargs):
            nonlocal index
            index+=1
            self.assertFalse('tools' in kwargs and 'response_format' in kwargs)
            if index==1:return submit({'response':'Beta has a championship.'})
            if index==2:return feedback()
            if index==3:
                feedback_result=json.loads(messages[-1]['content'])
                self.assertEqual(feedback_result['status'],'needs_evidence')
                self.assertEqual(feedback_result['submissions_remaining'],2)
                self.assertIn('player_awards',[t['function']['name'] for t in kwargs['tools']])
                return tool_call('player_awards',{'player_id':2,'award':'CHAMPIONSHIPS'})
            if index==4:
                facts=json.loads(messages[-1]['content'])['catalog']['facts']
                return submit({'response':'Beta has 6 verified championship seasons. That is team success; individual credit needs a separate argument.', 'fact_ids':[facts[0]['fact_id']]})
            return PASS
        seen=[]
        with patch('backend.debate.agent.complete',side_effect=model):result=self.run_chat(on_event=seen.append)
        self.assertEqual(result[2]['validation']['submissions'],2)
        self.assertEqual([a['semantic_status'] for a in result[2]['validation']['attempts']],['needs_evidence','pass'])
        self.assertEqual(result[2]['cards'][0]['title'],'CHAMPIONSHIPS')
        audits=[json.loads(l['result']) for l in result[1] if l['name']=='audit_argument']
        self.assertTrue(all(a['valid'] for a in audits))
        self.assertTrue(all(not any('prose' in k for k in a) for a in audits))
        self.assertIn('Retrieving additional evidence',[e['label'] for e in seen if e['type']=='phase'])

    def test_plan_only_then_directed_query_without_keywords_preserves_queue(self):
        outputs=[tool_call('plan_team_context_research',{'argument':'record'}),
                 tool_call('query_competitive_context',{'player_id':1,'season':1998,'phase':'Playoffs','selection_reason':'A middle-career sample to check role, not the whole career.'}),
                 submit({'response':'This sample cannot settle the entire career comparison.'}),PASS]
        with patch('backend.debate.agent.complete',side_effect=outputs):result=self.run_chat('Look into that claim.')
        coverage=result[2]['research']
        self.assertEqual(len(coverage['pending']),6)
        self.assertFalse(coverage['completed'])
        directed=coverage['directed_queries'][0]
        self.assertEqual(directed['request']['season'],1998)
        self.assertEqual(directed['request']['phase'],'Playoffs')
        self.assertEqual(directed['status'],'completed')
        team_reads=[p for k,p in self.data.requests if k=='team_players']
        self.assertTrue(all(p['season']=='1998-99' and p['season_type_all_star']=='Playoffs' for p in team_reads))

    def test_plan_without_batch_does_not_query_rosters(self):
        with patch('backend.debate.agent.complete',side_effect=[tool_call('plan_team_context_research',{'argument':'record'}),submit({'response':'The candidate seasons are ready; this does not establish who had more help.'}),PASS]):
            result=self.run_chat()
        self.assertTrue(result[2]['research']['pending'])
        self.assertFalse(any(k=='team_players' for k,p in self.data.requests))

    def test_three_submissions_are_a_hard_limit_and_rejected_text_is_not_published(self):
        with patch('backend.debate.agent.complete',side_effect=[submit({'response':'Beta has a championship.'}),feedback()]*3) as model:
            result=self.run_chat()
        self.assertEqual(model.call_count,6)
        self.assertEqual(result[2]['validation']['stop_reason'],'submission_limit')
        self.assertEqual(result[2]['validation']['submissions'],3)
        self.assertNotIn('Beta has a championship.',result[0])
        self.assertEqual(result[2]['cards'],[])

    def test_plain_text_cannot_bypass_submit_or_reset_round_limit(self):
        seen=[]
        def model(messages,**kwargs):
            seen.append(copy.deepcopy(messages))
            return response('Beta definitely won everything.')
        with patch('backend.debate.agent.complete',side_effect=model) as model_call:
            result=self.run_chat(max_rounds=3)
        self.assertEqual(model_call.call_count,3)
        self.assertIn('Submit via submit_argument',seen[1][-1]['content'])
        self.assertEqual(result[2]['validation']['submissions'],0)
        self.assertNotIn('definitely',result[0])
        self.assertFalse(self.data.requests)

    def test_numeric_failure_does_not_call_semantic_reviewer(self):
        tools=DebateTools(self.config,data=self.data,names=self.names)
        record=tools.query_evidence(Query(player_id=1))
        bad={'response':'Alpha scores 999 points.', 'claims':[dict(evidence_id=record['id'],player_id=1,scope=record['scope'],metric='PTS',value=999)]}
        with patch('backend.debate.agent.complete',return_value=submit(bad)), patch('backend.debate.agent.semantic_review',side_effect=AssertionError('Invalid fact cannot reach prose reviewer')):
            result=self.run_chat(evidence=tools.evidence)
        self.assertFalse(result[2]['validation']['numeric_valid'])
        self.assertEqual(result[2]['validation']['semantic_status'],'not_run')

    def test_reviewer_receives_exact_draft_cards_question_and_scope(self):
        for phrase in ['That championship hardware is a team résumé, not proof of individual isolation.',
                       'His usage proves he carried the entire offense.', 'This is a historic roster.', 'Mate played for Beta.']:
            with self.subTest(phrase=phrase), patch('backend.debate.agent.complete',return_value=feedback('revise')) as model:
                audit={'cards':[{'player':'Alpha','title':'USG_PCT','value':25}]}
                semantic_review(Draft(response=phrase),audit,'Who had more help?','rebuttal',self.config,{'pending':['1999']})
                args,kwargs=model.call_args
                payload=json.loads(args[0][1]['content'])
                self.assertEqual(payload['draft']['response'],phrase)
                self.assertEqual(payload['verified_cards'],audit['cards'])
                self.assertEqual(payload['research']['pending'],['1999'])
                self.assertNotIn('tools',kwargs)
                self.assertIn('Identify specific facts and general rhetoric by meaning',args[0][0]['content'])

    def test_fallback_can_use_model_selected_facts_when_rounds_end(self):
        with patch('backend.debate.agent.complete',return_value=tool_call('query_evidence',{'player_id':1})):
            result=self.run_chat(max_rounds=1)
        self.assertEqual(result[2]['review_status'],'limited')
        self.assertTrue(result[2]['cards'])
        self.assertIn('Verified evidence summary',result[0])
        self.assertEqual(result[2]['validation']['submissions'],0)

    def test_state_and_evidence_rollback_after_successful_lookup_then_provider_failure(self):
        state={'topics':['old'],'concessions':[]}
        evidence={}
        with patch('backend.debate.agent.complete',side_effect=[tool_call('plan_team_context_research',{'argument':'record'}),RuntimeError('429')]):
            with self.assertRaisesRegex(RuntimeError,'429'):self.run_chat(state=state,evidence=evidence)
        self.assertEqual(state,{'topics':['old'],'concessions':[]})
        self.assertEqual(evidence,{})

    def test_reviewer_provider_error_is_not_mistaken_for_a_bad_draft(self):
        with patch('backend.debate.agent.complete',side_effect=[submit({'response':'Team credit is not individual credit.'}),ValueError('Provider format failure')]):
            with self.assertRaisesRegex(ValueError,'Provider format failure'):self.run_chat()

    def test_stream_history_three_turns_continuation_and_failure_rollback(self):
        def model(messages,**kwargs):
            if 'response_format' in kwargs:return PASS
            # Only current turn's tool messages; prior working traces are not persisted.
            recent=messages[max(i for i,m in enumerate(messages) if m['role']=='user')+1:]
            tools=[m for m in recent if m['role']=='tool']
            if not tools:return tool_call('plan_team_context_research',{'argument':'record'})
            if len(tools)==1:return tool_call('research_team_context',{'plan_id':json.loads(tools[0]['content'])['plan_id']})
            return submit({'response':'The checked seasons are a limited sample; I am not claiming an overall supporting-cast advantage.'})
        with tempfile.TemporaryDirectory() as folder, patch.object(app,'HISTORY_DIR',Path(folder)), patch.object(app.NBAData,'directory',return_value=[{'id':1,'name':'Alpha'},{'id':2,'name':'Beta'}]), patch('backend.debate.tools.NBAData',return_value=self.data), patch('backend.debate.agent.complete',side_effect=model):
            client=TestClient(app.app)
            first=events(client.post('/chat/stream',json={'message':'Compare records','mode':'rebuttal','debate_config':self.config.model_dump()}).text)[-1]['data']
            sid=first['session_id'];original=copy.deepcopy(first['debate_result'])
            second=client.post('/chat',json={'message':'And the next seasons?','session_id':sid}).json()
            third=events(client.post('/chat/stream',json={'message':'Continue','session_id':sid}).text)[-1]['data']
            self.assertEqual([len(r['debate_result']['research']['completed']) for r in (first,second,third)],[2,4,6])
            self.assertEqual(client.get('/sessions/'+sid).json()['turns'][0]['debate_result'],original)
            path=next(Path(folder).glob('*/*.json'));before=json.loads(path.read_text())['debate_state']
            with patch('backend.debate.agent.complete',side_effect=RuntimeError('Provider unavailable')):
                failed=client.post('/chat',json={'message':'Continue','session_id':sid}).json()
            self.assertTrue(failed['failed'])
            self.assertEqual(json.loads(path.read_text())['debate_state'],before)

    def test_multiple_batches_in_one_turn_do_not_stop_after_two_units(self):
        tools=DebateTools(self.config,data=self.data)
        plan=tools.run('plan_team_context_research',{'argument':'record'})
        for expected in [2,4,6]:
            batch=tools.run('research_team_context',{'plan_id':plan['plan_id']})
            self.assertEqual(len(batch['batch']),2)
            self.assertEqual(len(batch['research']['completed']),expected)
        self.assertFalse(tools.run('research_team_context',{'plan_id':plan['plan_id']})['batch'])

    def test_directed_selection_requires_reason_and_preserves_requested_year_and_phase(self):
        tools=DebateTools(DebateConfig(supported_player=1,opponent_player=2,supported_season=1995,opponent_season=1995),data=self.data)
        self.assertIn('error',tools.run('query_competitive_context',{'player_id':1,'season':1998}))
        self.assertIn('error',tools.run('query_competitive_context',{'player_id':1,'season':2099,'selection_reason':'Future'}))
        result=tools.run('query_competitive_context',{'player_id':1,'season':1998,'phase':'Playoffs','selection_reason':'A later career stage to test the claim.'})
        self.assertEqual(result['scope']['start'],1998)
        self.assertEqual(result['scope']['phase'],'Playoffs')
        old=copy.deepcopy(tools.evidence)
        # Changing rationale should reuse the exact snapshot but preserve both selections.
        result2=tools.run('query_competitive_context',{'player_id':1,'season':1998,'phase':'Playoffs','selection_reason':'Revisit that sample on follow-up.'})
        self.assertEqual(result['id'],result2['id'])
        self.assertEqual(old,tools.evidence)
        self.assertEqual(len(tools.research_state['directed_queries']),2)

    def test_league_baseline_catalog_does_not_invent_a_player(self):
        tools=DebateTools(self.config,data=self.data)
        record=tools.add({'kind':'league','player_id':None,'scope':{'start':1995,'end':1995,'phase':'Regular Season','basis':'totals'},'values':{'TS_PCT':{'value':55}}})
        self.assertEqual(evidence_catalog(tools,[record])['facts'],[])

    def test_partial_directed_result_keeps_both_selection_and_missing_side(self):
        self.data.team_fail=20
        tools=DebateTools(self.config,data=self.data)
        result=tools.run('compare_competitive_context',{'focus':'team_context','left_player':1,'right_player':2,'left_season':1995,'right_season':1995,'selection_reason':'Compare the same season.'})
        self.assertEqual(result['status'],'partial')
        query=tools.research_state['directed_queries'][0]
        self.assertEqual(query['status'],'partial')
        self.assertTrue(query['evidence_ids'])
        self.assertTrue(any('right:' in e for e in query['errors']))


class NetworkBudgetTests(unittest.TestCase):
    def test_only_network_elapsed_counts_and_failed_request_is_not_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            data=NBAData(Path(folder)/'cache.db',budget=15)
            now=[100.0]
            timeouts=[]
            def live(kind,params,timeout):
                timeouts.append(timeout);now[0]+=10
                if params['player_id']==2:raise TimeoutError('Timeout')
                return {'resultSets':[1]}, {'rows':[1]}
            with patch('backend.data.nba.time.monotonic',side_effect=lambda:now[0]),patch.object(data,'_live',side_effect=live):
                now[0]+=500 # Model and review latency does not consume network budget.
                data.fetch('career',player_id=1)
                self.assertEqual(data.remaining_seconds,5)
                now[0]+=500
                with self.assertRaises(DataUnavailable):data.fetch('career',player_id=2)
                with self.assertRaises(DataUnavailable):data.fetch('career',player_id=2)
                with self.assertRaises(DataUnavailable):data.fetch('career',player_id=3)
                self.assertEqual(timeouts,[12,5])
                self.assertIsNotNone(data.fetch('career',player_id=1))
