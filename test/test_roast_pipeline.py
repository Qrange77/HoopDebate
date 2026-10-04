from test.test_debate import review_response
import json
import unittest
from unittest.mock import patch
from backend.debate.agent import run_debate, Review, ReviewIssue, unique_cards, canonical_fallback
from backend.debate.tools import DebateConfig
from test.test_debate import FakeData, response, submit


class RoastPipelineTests(unittest.TestCase):
    def run_chat(self, tone='reasoned'):
        return run_debate([{'role':'system','content':''},{'role':'user','content':'More rings proves he is better.'}],
                          'debate',DebateConfig(supported_player=1,opponent_player=2),{},data=FakeData(),reply_tone=tone)

    def test_unknown_citation_does_not_spend_content_review_attempt(self):
        with patch('backend.debate.agent.complete',side_effect=[
            submit({'response':'Bad reference','fact_ids':['fact_unknown']}),
            submit({'response':'Rings alone do not isolate individual credit.'}), review_response({'status': 'pass', 'issues': []})]):
            result=self.run_chat()
        self.assertEqual(result[2]['validation']['submissions'],1)
        self.assertEqual(result[2]['validation']['citation_repairs'],1)
        first=json.loads(next(c['result'] for c in result[1] if c['name']=='submit_argument'))
        self.assertEqual(first['submissions_remaining'],3)
        self.assertIn('available_facts',first)

    def test_invalid_references_still_have_a_hard_limit(self):
        with patch('backend.debate.agent.complete',return_value=submit({'response':'Bad reference','fact_ids':['missing']})) as model:
            result=self.run_chat()
        self.assertEqual(model.call_count,3)
        self.assertEqual(result[2]['validation']['submissions'],0)
        self.assertEqual(result[2]['validation']['stop_reason'],'citation_repair_limit')

    def test_repeated_reasoning_failure_gets_strategy_feedback(self):
        rejected=Review(status='revise',issues=[ReviewIssue(clause='answer',reason='Does not address the objection.')])
        with patch('backend.debate.agent.complete',return_value=submit({'response':'A general observation.'})), \
             patch('backend.debate.agent.semantic_review',side_effect=[rejected,rejected,Review(status='pass')]):
            result=self.run_chat()
        feedback=[json.loads(c['result']) for c in result[1] if c['name']=='submit_argument']
        self.assertEqual(feedback[0]['failure_type'],'argument')
        self.assertFalse(feedback[0]['repeated_failure'])
        self.assertTrue(feedback[1]['repeated_failure'])
        self.assertIn('change the evidence or reasoning strategy',feedback[1]['instruction'])

    def test_malformed_submission_uses_repair_budget_only(self):
        broken=submit({'response':'Broken arguments'})
        broken.tool_calls[0].function.arguments='{'
        with patch('backend.debate.agent.complete',side_effect=[broken,
            submit({'response':'Rings alone do not isolate individual credit.'}),review_response({'status': 'pass', 'issues': []})]):
            result=self.run_chat()
        self.assertEqual(result[2]['validation']['submissions'],1)
        self.assertEqual(result[2]['validation']['citation_repairs'],1)
        self.assertIsNone(result[2]['validation']['attempts'][0]['submission'])

    def test_style_only_runs_after_fact_argument_passes(self):
        base='Rings alone do not isolate individual credit.'
        styled='Apparently counting rings counts as analysis now. Team trophies alone do not isolate individual credit.'
        with patch('backend.debate.agent.complete',side_effect=[submit({'response':base}),response(json.dumps({'response':styled}))]) as model, \
             patch('backend.debate.agent.semantic_review',return_value=Review(status='pass')) as review:
            result=self.run_chat('roast')
        self.assertIn('Reply tone: REASONED',model.call_args_list[0].args[0][0]['content'])
        self.assertEqual(review.call_count,2)
        self.assertEqual(review.call_args_list[0].args[0].response,base)
        self.assertEqual(review.call_args_list[1].args[0].claims,review.call_args_list[0].args[0].claims)
        self.assertEqual(result[0],styled)
        self.assertEqual(review.call_args_list[1].args[0].response,styled)
        self.assertEqual(review.call_args_list[1].args[0].fact_ids,review.call_args_list[0].args[0].fact_ids)
        self.assertEqual(review.call_args_list[1].args[1]['approved_argument'],base)
        self.assertEqual(result[2]['style_status'],'applied')
        self.assertEqual(result[2]['validation']['submissions'],1)

    def test_missing_response_gets_field_feedback_without_evidence_dump(self):
        with patch('backend.debate.agent.complete',side_effect=[submit({'fact_ids':[]}),
            submit({'response':'Team titles alone cannot isolate individual credit.'})]), \
             patch('backend.debate.agent.semantic_review',return_value=Review(status='pass')):
            result=self.run_chat()
        feedback=json.loads(result[1][0]['result'])
        self.assertEqual(feedback['failure_type'],'format')
        self.assertEqual(feedback['field_errors'][0]['field'],'response')
        self.assertNotIn('available_facts',feedback)
        self.assertNotIn('evidence_options',feedback)
        self.assertEqual(feedback['submissions_remaining'],3)

    def test_repeated_missing_response_switches_to_schema_then_reviews(self):
        text='Team trophies alone do not isolate individual credit.'
        with patch('backend.debate.agent.complete',side_effect=[submit({'fact_ids':[]}),
            submit({'fact_ids':[]}),response(json.dumps({'response':text,'fact_ids':[]}))]) as model, \
             patch('backend.debate.agent.semantic_review',return_value=Review(status='pass')) as review:
            result=self.run_chat()
        self.assertNotIn('tools',model.call_args_list[2].kwargs)
        self.assertIn('response_format',model.call_args_list[2].kwargs)
        review.assert_called_once()
        self.assertEqual(review.call_args.args[0].response,text)
        self.assertEqual(result[0],text)
        self.assertEqual(result[2]['validation']['citation_repairs'],2)
        self.assertEqual(result[2]['validation']['submissions'],1)

    def test_structured_repair_cannot_bypass_review(self):
        rejected=Review(status='needs_evidence',issues=[ReviewIssue(clause='carried',reason='Unsupported workload.')])
        with patch('backend.debate.agent.complete',side_effect=[submit({'fact_ids':[]}),
            submit({'fact_ids':[]}),response('{"response":"He carried everybody."}'),
            submit({'response':'Team titles alone cannot isolate individual credit.'})]), \
             patch('backend.debate.agent.semantic_review',side_effect=[rejected,Review(status='pass')]) as review:
            result=self.run_chat()
        self.assertEqual(review.call_count,2)
        self.assertNotIn('carried',result[0])
        failed=json.loads(next(c['result'] for c in result[1] if c['name']=='submit_argument' and c['args'].get('response')=='He carried everybody.'))
        self.assertEqual(failed['failure_type'],'evidence')

    def test_invalid_structured_repair_is_bounded_and_classified(self):
        with patch('backend.debate.agent.complete',side_effect=[submit({'fact_ids':[]}),
            submit({'fact_ids':[]}),response('{"fact_ids":[]}')]) as model, \
             patch('backend.debate.agent.semantic_review') as review:
            result=self.run_chat()
        self.assertEqual(model.call_count,3)
        review.assert_not_called()
        self.assertEqual(result[2]['validation']['failure_type'],'format')
        self.assertEqual(result[2]['validation']['citation_repairs'],3)
        self.assertEqual(result[2]['validation']['stop_reason'],'citation_repair_limit')

    def test_rejected_roast_keeps_approved_reply_instead_of_evidence_dump(self):
        base='Rings alone do not isolate individual credit.'
        rejected=Review(status='needs_evidence',issues=[ReviewIssue(clause='carried',reason='Unsupported workload.')])
        with patch('backend.debate.agent.complete',side_effect=[submit({'response':base}),response('{"response":"He carried everybody. It proves everything."}')]), \
             patch('backend.debate.agent.semantic_review',side_effect=[Review(status='pass'),rejected]):
            result=self.run_chat('roast')
        self.assertEqual(result[0],base)
        self.assertEqual(result[2]['review_status'],'reviewed')
        self.assertEqual(result[2]['style_status'],'reasoned_fallback')

    def test_style_provider_failure_retains_verified_reply(self):
        with patch('backend.debate.agent.complete',side_effect=[submit({'response':'Limited logical concession.'}),RuntimeError('Timeout')]), \
             patch('backend.debate.agent.semantic_review',return_value=Review(status='pass')):
            result=self.run_chat('roast')
        self.assertEqual(result[0],'Limited logical concession.')
        self.assertEqual(result[2]['style_status'],'reasoned_fallback')

    def test_fallback_deduplicates_reverse_pairs_and_covered_single_facts(self):
        single=dict(player='A',scope='2017 playoffs',value=20,title='PTS',unit='per game')
        pair=dict(single,other_player='B',other_scope='2017 playoffs',other_value=15)
        reverse=dict(pair,player='B',value=15,other_player='A',other_value=20)
        later=dict(single,scope='2018 playoffs')
        cards=unique_cards([single,pair,reverse,later])
        self.assertEqual(len(cards),2)
        text=canonical_fallback({'cards':cards})
        self.assertIn('Reply not approved',text)
        self.assertEqual(text.count('2017 playoffs'),2)
        self.assertEqual(len(unique_cards([dict(single,date='JAN 1'),dict(single,date='JAN 2')])),2)
