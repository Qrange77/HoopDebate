import json
import unittest
from unittest.mock import patch
from backend.activity import ActivityLog
from backend.debate.agent import Draft, Review, ReviewIssue, style_approved_reply
from backend.debate.tools import DebateConfig
from test.test_debate import response


class RoastRepairTests(unittest.TestCase):
    def run_style(self, replies, reviews):
        draft = Draft(response='Game counts alone do not establish individual superiority.')
        audit, logs = {'cards':[], 'approved_assessment':{'response_strategy':'qualified_answer'}}, ActivityLog()
        with patch('backend.debate.agent.complete', side_effect=replies) as model, \
             patch('backend.debate.agent.semantic_review', side_effect=reviews) as reviewer:
            result = style_approved_reply(draft, audit, 'He played more playoff games.', 'debate',
                                          DebateConfig(supported_player=1, opponent_player=2), None, logs)
        return result, audit, logs, model, reviewer

    def rejection(self):
        return Review(status='needs_evidence', issues=[ReviewIssue(clause='Sitting on the bench',
            reason='Game count does not establish a bench role.', missing_evidence='Playing-time evidence.')])

    def test_feedback_repairs_once_against_original_baseline(self):
        result, audit, logs, model, reviewer = self.run_style(
            [response('{"response":"Sitting on the bench is not greatness."}'),
             response('{"response":"Counting appearances is no substitute for comparing individual play."}')],
            [self.rejection(), Review(status='pass')])
        self.assertEqual(result[1], 'applied')
        self.assertEqual(len(audit['style_attempts']), 2)
        payload = json.loads(model.call_args_list[1].args[0][-1]['content'])
        self.assertEqual(payload['approved_argument'], 'Game counts alone do not establish individual superiority.')
        self.assertEqual(payload['repair_feedback']['issues'][0]['clause'], 'Sitting on the bench')
        self.assertIn('bench', payload['repair_feedback']['failed_version'])
        for call in model.call_args_list:
            self.assertNotIn('tools', call.kwargs)
            self.assertEqual(call.kwargs['num_retries'], 0)
        self.assertEqual(reviewer.call_count, 2)
        for index in range(0,4,2):
            self.assertEqual(logs[index]['attempt_id'], logs[index+1]['attempt_id'])

    def test_first_pass_has_no_retry_and_third_failure_keeps_original(self):
        good = response('{"response":"Counts alone do not prove individual superiority."}')
        result, audit, _, model, _ = self.run_style([good], [Review(status='pass')])
        self.assertEqual(result[1], 'applied')
        model.assert_called_once()
        result, audit, _, model, _ = self.run_style([good, good, good], [self.rejection(),self.rejection(),self.rejection()])
        self.assertEqual(result[1], 'reasoned_fallback')
        self.assertEqual(result[0].response, 'Game counts alone do not establish individual superiority.')
        self.assertEqual(model.call_count, 3)
        self.assertEqual(len(audit['style_attempts']), 3)

    def test_bad_format_consumes_attempt_and_can_be_repaired(self):
        result, audit, _, model, reviewer = self.run_style(
            [response('{}'), response('{"response":"Counts alone do not prove individual superiority."}')], [Review(status='pass')])
        self.assertEqual(result[1], 'applied')
        self.assertEqual(audit['style_attempts'][0]['status'], 'format_error')
        self.assertEqual(model.call_count, 2)
        reviewer.assert_called_once()

    def test_third_attempt_uses_second_failure_feedback_and_original_baseline(self):
        second_issue = Review(status='revise', issues=[ReviewIssue(clause='You ignored everything',
            reason='The user did not ignore the data.')])
        replies = [response(json.dumps(dict(response=text))) for text in
                   ['Sitting on the bench.', 'You ignored everything.', 'Counts alone cannot settle individual superiority.']]
        result,audit,logs,model,reviewer = self.run_style(replies,[self.rejection(),second_issue,Review(status='pass')])
        self.assertEqual(result[1],'applied')
        self.assertEqual(model.call_count,3)
        self.assertEqual(reviewer.call_count,3)
        payload = json.loads(model.call_args_list[2].args[0][-1]['content'])
        self.assertEqual(payload['repair_feedback']['failed_version'],'You ignored everything.')
        self.assertEqual(payload['repair_feedback']['issues'][0]['reason'],'The user did not ignore the data.')
        self.assertEqual(payload['approved_argument'],'Game counts alone do not establish individual superiority.')
        self.assertEqual(logs[4]['args']['repair_feedback'],payload['repair_feedback'])
        self.assertEqual(logs[4]['attempt_id'],logs[5]['attempt_id'])
        self.assertEqual([a['status'] for a in audit['style_attempts']],['needs_evidence','revise','pass'])

    def test_provider_error_falls_back_without_retry(self):
        result, audit, _, model, reviewer = self.run_style([RuntimeError('offline')], [])
        self.assertEqual(result[1], 'reasoned_fallback')
        self.assertEqual(audit['style_attempts'][0]['status'], 'provider_error')
        model.assert_called_once()
        reviewer.assert_not_called()

    def test_review_service_error_also_falls_back_without_second_rewrite(self):
        result, audit, logs, model, reviewer = self.run_style(
            [response('{"response":"Counts alone prove nothing about superiority."}')], [RuntimeError('offline')])
        self.assertEqual(result[1], 'reasoned_fallback')
        self.assertEqual(result[0].response, 'Game counts alone do not establish individual superiority.')
        self.assertEqual(audit['style_attempts'][0]['status'], 'provider_error')
        self.assertEqual(logs[-1]['status'], 'failed')
        model.assert_called_once()
        reviewer.assert_called_once()
