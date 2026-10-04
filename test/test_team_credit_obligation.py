"""Regression: a championship follow-up cannot be downgraded to a count lookup."""
import json
import unittest
from unittest.mock import patch
from backend.activity import ActivityLog
from backend.debate import agent
from backend.debate.support import SupportResearch
from test.test_debate import assessment, response, review_response, tool_call, submit
from test.test_support_research import fixture

QUESTION = 'but sga has champion'
CONVERSATION = [dict(role='user',content='SGA is way better than Luka'),
    dict(role='assistant',content='The selected personal statistics show tradeoffs.'),
    dict(role='user',content=QUESTION)]
SCREENSHOT = ("Conceding that team success belongs to franchise accomplishments doesn't mean individual superiority is settled by a championship count alone. "
    "Team titles reflect roster depth, coaching, and organizational continuity rather than one player carrying the entire load in a head-to-head comparison. "
    "Attributing a collective team achievement directly to individual superiority overlooks how complementary pieces drive championship outcomes.")


def missed_plan():
    return agent.PlanReview(team_context_reason='Only check titles.', team_context_intent='count_only',
        support_scope='none', research_tools=[], guidance='Acknowledge the count.')


def support_finding(**kwargs):
    return assessment(**dict(dict(team_success_argument=True, required_support_scope='two_pairs'), **kwargs))


class TeamCreditObligationTests(unittest.TestCase):
    def setUp(self):
        self.data,self.config,self.args,self.pairs,self.plan = fixture()

    def test_structured_team_credit_overrides_contradictory_count_only(self):
        fields = missed_plan().model_dump()
        review = agent.LivePlanReview(**dict(fields,team_success_argument=True))
        self.assertEqual(review.team_context_intent,'individual_credit')
        self.assertEqual(review.support_scope,'two_pairs')
        self.assertEqual(review.research_tools,['compare_competitive_context'])

    def test_old_plan_can_load_but_live_signal_cannot_be_missing_or_coerced(self):
        fields = missed_plan().model_dump()
        del fields['team_success_argument']
        self.assertFalse(agent.PlanReview(**fields).team_success_argument)
        with self.assertRaises(ValueError):
            agent.LivePlanReview(**fields)
        for value in ['false',0,None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                agent.LivePlanReview(**dict(fields,team_success_argument=value))

    def test_general_comment_pass_cannot_waive_research(self):
        verdict = review_response(dict(status='pass',issues=[],argument_assessment=support_finding(
            required_support_scope='none',evidence_followthrough='not_needed')))
        with patch.object(agent,'complete',return_value=verdict) as model:
            review = agent.semantic_review(agent.Draft(response=SCREENSHOT),
                dict(cards=[],conversation=CONVERSATION),QUESTION,'debate',self.config,None)
        self.assertEqual(review.status,'needs_evidence')
        self.assertEqual(review.argument_assessment.required_support_scope,'two_pairs')
        self.assertEqual(review.argument_assessment.evidence_followthrough,'missing')
        model.assert_called_once()

    def test_late_review_creates_two_tasks_and_premature_resubmit_cannot_pass(self):
        outputs = [tool_call('plan_argument',self.plan),submit(dict(response=SCREENSHOT)),
            submit(dict(response='Championships belong to teams.')),
            tool_call('compare_competitive_context',self.args),
            tool_call('plan_argument',dict(self.plan,teammate_pairs=[p.model_dump() for p in self.pairs])),
            tool_call('compare_competitive_context',self.pairs[0].model_dump()),
            tool_call('compare_competitive_context',self.pairs[1].model_dump()),
            submit(dict(response='The selected teammates are only part of the picture; I have not established overall roster superiority.'))]
        verdicts = [agent.Review(status='pass',argument_assessment=agent.ArgumentAssessment(**support_finding())) for _ in range(2)]
        advice = agent.PlanReview(**dict(missed_plan().model_dump(),team_success_argument=True,
            team_context_intent='individual_credit',support_scope='two_pairs',research_tools=['compare_competitive_context']))
        with patch.object(agent,'complete',side_effect=outputs), patch.object(agent,'review_plan',side_effect=[missed_plan(),advice]), \
                patch.object(agent,'semantic_review',side_effect=verdicts) as reviewer:
            result = agent.run_debate([dict(role='system',content=''),*CONVERSATION],
                'debate',self.config,{},data=self.data)
        self.assertEqual(result[2]['review_status'],'reviewed')
        self.assertEqual([t['status'] for t in result[2]['support_research']['tasks']],['completed','completed'])
        self.assertEqual(len(result[2]['comparisons']),2)
        self.assertEqual(reviewer.call_count,2)
        self.assertEqual(result[2]['validation']['submissions'],2)
        recovered = next(c for c in result[1] if c['name']=='require_support_research')
        self.assertEqual(len(json.loads(recovered['result'])['support_research']['tasks']),2)
        submits = [json.loads(c['result']) for c in result[1] if c['name']=='submit_argument']
        self.assertEqual(submits[0]['status'],'needs_evidence')
        self.assertEqual(submits[1]['status'],'needs_evidence')
        self.assertEqual(submits[1]['submissions_remaining'],2)
        self.assertFalse(result[4]['used_fact_keys'])

    def test_unavailable_tasks_allow_explicit_limitation_and_no_invented_weakness(self):
        support = SupportResearch(self.config)
        support.register('two_pairs',[])
        support.expire()
        review = agent.Review(status='pass',argument_assessment=agent.ArgumentAssessment(**support_finding()))
        agent.enforce_support_research(review,dict(support_research=support.snapshot()),QUESTION)
        self.assertEqual(review.status,'pass')  # Meaning/limitations still require normal semantic review.

    def test_late_breadth_correction_cannot_select_more_teammates_after_result(self):
        support = SupportResearch(self.config)
        support.register('strongest_pair',[])
        support.tasks[0].update(status='completed',request=self.pairs[0].model_dump())
        support.require_from_review('two_pairs')
        self.assertEqual(support.tasks[1]['status'],'unavailable')
        self.assertIsNone(support.tasks[1]['request'])
        self.assertEqual(support.required,2)

    def test_roast_cannot_start_research_to_support_added_team_credit(self):
        draft = agent.Draft(response='I have not established overall superiority from these personal statistics.')
        audit,logs = dict(cards=[],conversation=CONVERSATION),ActivityLog()
        rejection = review_response(dict(status='pass',issues=[],argument_assessment=support_finding()))
        with patch.object(agent,'complete',side_effect=[response(json.dumps(dict(response=SCREENSHOT))),rejection,
                response(json.dumps(dict(response=SCREENSHOT))),rejection,
                response(json.dumps(dict(response=SCREENSHOT))),rejection]) as model:
            final,status = agent.style_approved_reply(draft,audit,QUESTION,'debate',self.config,None,logs)
        self.assertEqual(status,'reasoned_fallback')
        self.assertEqual(final,draft)
        self.assertTrue(all('tools' not in call.kwargs for call in model.call_args_list))
        self.assertEqual([a['status'] for a in audit['style_attempts']],['revise','revise','revise'])
