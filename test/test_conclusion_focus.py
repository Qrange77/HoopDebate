"""Fixed evidence for takeaway relevance; numbers are fixtures, not live NBA verification."""
import json
import unittest
from unittest.mock import patch

from backend.activity import ActivityLog
from backend.debate import agent
from test.test_debate import assessment, response, review_response
from test.test_evidence_direction import CONFIG

QUESTION = 'SGA is way better than Luka'
SCREENSHOT = (
    "Bro really looked at a 63.6% true shooting clip for Shai compared to Luka's 61.7% in the 2023-24 regular season and called "
    "that way better. Never mind that Luka still held the advantages in per-game scoring, assists, and rebounding volume, or that "
    "Shai's superior shooting efficiency and lower turnover rate just reflect a distinct and highly effective style. Both of them are "
    "sitting at the pinnacle of guard play anyway, so maybe acknowledge Shai's efficiency edge within a balanced assessment "
    "instead of pretending volume doesn't exist. Turn the calculator off.")
DRIFT = (
    "In the 2023-24 regular season, Luka averaged 33.9 points and 9.8 assists to SGA's 30.1 and 6.2. "
    "SGA had the true shooting edge, 63.6% to 61.7%. So appreciate SGA's efficiency advantage within a balanced assessment.")
ANSWER = (
    "SGA's 63.6% true shooting beat Luka's 61.7% in the 2023-24 regular season; I concede that efficiency edge. "
    "But Luka's 33.9 points and 9.8 assists per game versus SGA's 30.1 and 6.2 give me a concrete scoring-and-assist "
    "counterpoint to 'way better'. Those tradeoffs do not establish Luka's overall superiority either.")
CONCESSION = (
    "In the 2023-24 regular season, SGA's 63.6% true shooting exceeded Luka's 61.7%; I concede that efficiency dimension. "
    "This metric alone cannot establish that SGA is way better overall, and I have not established an overall winner.")


def audit():
    return dict(cards=[dict(evidence_id='ev_focus_'+metric, title=metric, player='Luka',
        player_id=CONFIG.opponent_player, other_player='SGA', value=left, other_value=right, unit=unit,
        scope='2023-24 Regular Season', other_scope='2023-24 Regular Season')
        for metric,left,right,unit in [('PTS',33.9,30.1,'per game'),('AST',9.8,6.2,'per game'),('TS_PCT',61.7,63.6,'%')]],
        conversation=[dict(role='user',content=QUESTION)], argument_plan=dict(
            discussion_dimension='Overall player comparison', target_claim='Luka had higher scoring and assist averages in this sample.'))


class ConclusionFocusTests(unittest.TestCase):
    def test_accurate_numbers_cannot_override_a_drifting_takeaway(self):
        findings = assessment(conclusion_addresses_objection=False)
        with patch.object(agent,'complete',return_value=review_response(dict(status='pass',issues=[],
                argument_assessment=findings))) as model:
            review = agent.semantic_review(agent.Draft(response=DRIFT),audit(),QUESTION,'debate',CONFIG,None)
        self.assertEqual(review.status,'revise')
        self.assertTrue(review.argument_assessment.conclusion_supported)
        self.assertIn('takeaway',review.issues[0].reason.lower())
        model.assert_called_once()  # Existing reviewer; no extra model review.

    def test_conclusion_failure_is_revision_even_when_other_feedback_requests_evidence(self):
        with patch.object(agent,'complete',return_value=review_response(dict(status='needs_evidence',
                issues=[dict(clause='',reason='Missing comparison.',missing_evidence='Another season.')],
                argument_assessment=assessment(conclusion_addresses_objection=False)))):
            review = agent.semantic_review(agent.Draft(response=DRIFT),audit(),QUESTION,'debate',CONFIG,None)
        self.assertEqual(review.status,'revise')

    def test_honest_concession_and_counterpoint_can_both_pass(self):
        for text,strategy in [(CONCESSION,'concession'),(ANSWER,'qualified_answer')]:
            with self.subTest(strategy=strategy), patch.object(agent,'complete',return_value=review_response(dict(
                    status='pass',issues=[],argument_assessment=assessment(response_strategy=strategy,
                    conclusion_addresses_objection=True,advantage_example_count=0)))):
                review = agent.semantic_review(agent.Draft(response=text),audit(),QUESTION,'debate',CONFIG,None)
            self.assertEqual(review.status,'pass')

    def test_legacy_assessment_can_omit_field_but_live_boolean_is_strict(self):
        old = assessment()
        del old['conclusion_addresses_objection']
        self.assertIsNone(agent.ArgumentAssessment.model_validate(old).conclusion_addresses_objection)
        for value in [None,'true',1]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                agent.DebateArgumentAssessment.model_validate(dict(old,conclusion_addresses_objection=value))

    def test_roast_drift_gets_feedback_then_falls_back_to_approved_takeaway(self):
        rejection = review_response(dict(status='pass',issues=[],
            argument_assessment=assessment(conclusion_addresses_objection=False)))
        draft, evidence, logs = agent.Draft(response=ANSWER),audit(),ActivityLog()
        evidence['approved_assessment'] = assessment()
        with patch.object(agent,'complete',side_effect=[response(json.dumps(dict(response=DRIFT))),rejection,
                response(json.dumps(dict(response=DRIFT))),rejection,
                response(json.dumps(dict(response=DRIFT))),rejection]) as model:
            final,status = agent.style_approved_reply(draft,evidence,QUESTION,'debate',CONFIG,None,logs)
        self.assertEqual(status,'reasoned_fallback')
        self.assertEqual(final.response,ANSWER)
        repair = json.loads(model.call_args_list[2].args[0][-1]['content'])
        self.assertIn('takeaway',repair['repair_feedback']['issues'][0]['reason'].lower())
        self.assertEqual([a['status'] for a in evidence['style_attempts']],['revise','revise','revise'])
