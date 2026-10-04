"""Direction, context fidelity and publication guards for debate replies."""
import copy
import json
import unittest
from unittest.mock import patch

from backend.activity import ActivityLog
from backend.debate.agent import (
    ArgumentPlan, DebateArgumentPlan, Draft, Review, conversation_text,
    review_plan, semantic_review, style_approved_reply, run_debate,
)
from backend.debate.tools import DebateConfig, DebateTools
from test.test_debate import FakeData, assessment, response, review_response, submit, tool_call

LUKA, SHAI = 1629029, 1628983
CONFIG = DebateConfig(supported_player=SHAI, opponent_player=LUKA)
CONVERSATION = [
    {'role':'user', 'content':'SGA is way better than Luka'},
    {'role':'assistant', 'content':'Let us look at a concrete comparison.'},
    {'role':'user', 'content':'What about teammates?'},
]
# Fixed reviewer inputs taken from the screenshot, not a live NBA data claim.
CARD = dict(evidence_id='ev_fixture_pair', title='PTS', player='Kyrie Irving',
    player_id=202681, value=22.1, unit='per game', scope='2023-24 · Playoffs · per game',
    other_player='Jalen Williams', other_value=18.7, other_scope='2023-24 · Playoffs · per game',
    context={'focal_player_id':LUKA, 'team_name':'Dallas Mavericks', 'team_id':1610612742},
    other_context={'focal_player_id':SHAI, 'team_name':'Oklahoma City Thunder', 'team_id':1610612760},
    limitations=['Selected co-stars and scoring only; does not establish overall roster strength.'])
TARGET = 'Luka received less scoring support from his selected co-star in the 2024 postseason than Shai did.'
PLAN = dict(discussion_dimension='Teammate support', target_claim=TARGET,
    objection='What supporting help did each player have?',
    approach='Compare the selected co-stars without assuming a winner.',
    evidence_needed=['Their scoring in the same postseason.'],
    scope_reason='A shared postseason sample, not a career conclusion.', research_tools=[])
CONCESSION = ("In the 2024 postseason sample, Kyrie scored 22.1 points per game versus Jalen Williams's 18.7. "
    "That gives Luka more scoring support from this selected co-star, so I cannot use it to argue he had less help. "
    "Those scoring figures alone do not establish which entire roster was stronger or which focal player was better.")
BAD_REPLY = ("In the 2024 postseason, Kyrie scored 22.1 points per game and Jalen Williams 18.7. "
    "So Luka had weaker help and was clearly better. You only count wins and ignore teammates. Get better material.")


def fixture_audit():
    return dict(cards=[copy.deepcopy(CARD)], argument_plan=copy.deepcopy(PLAN),
                conversation=copy.deepcopy(CONVERSATION), prior_replies=[CONVERSATION[1]['content']])


def direction_assessment(**overrides):
    return assessment(**dict(dict(target_claim=TARGET, evidence_direction='opposes',
        evidence_ids=[CARD['evidence_id']], response_strategy='concession',
        evidence_followthrough='complete', reason='The selected Luka co-star scored more; the reply concedes that local result.'), **overrides))


class EvidenceDirectionTests(unittest.TestCase):
    def review(self, findings=None, text=CONCESSION, audit=None, config=CONFIG, **payload):
        verdict = dict(status='pass', issues=[], user_attributions=[], argument_assessment=findings or direction_assessment())
        verdict.update(payload)
        with patch('backend.debate.agent.complete', return_value=response(json.dumps(verdict))) as model:
            result = semantic_review(Draft(response=text), audit or fixture_audit(),
                                     'What about teammates?', 'debate', config, None)
        return result, model

    def test_opposing_evidence_with_honest_concession_passes_without_more_research(self):
        review, model = self.review()
        self.assertEqual(review.status, 'pass')
        self.assertEqual(review.argument_assessment.evidence_direction, 'opposes')
        self.assertEqual(review.argument_assessment.advantage_example_count, 0)
        model.assert_called_once()

    def test_overall_pass_cannot_override_bad_direction_conclusion_or_strawman(self):
        for flags in [dict(conclusion_supported=False), dict(objection_faithful=False),
                      dict(advantage_example_count=2)]:
            with self.subTest(flags=flags):
                review, model = self.review(direction_assessment(**flags), text=BAD_REPLY)
                self.assertEqual(review.status, 'revise')
                self.assertTrue(review.issues)
                model.assert_called_once()  # No additional inference call after rejection.

    def test_references_must_be_verified_and_direction_needs_citations(self):
        for ids in [['ev_not_cited'], []]:
            with self.subTest(ids=ids):
                review, _ = self.review(direction_assessment(evidence_ids=ids))
                self.assertEqual(review.status, 'revise')

    def test_reviewer_gets_explicit_citation_catalog_separate_from_pair_background(self):
        audit = fixture_audit()
        audit['support_research'] = {'tasks':[{'evidence_id':'ev_uncited_parent'}]}
        _, model = self.review(audit=audit)
        payload = json.loads(model.call_args.args[0][-1]['content'])
        self.assertEqual(payload['verified_evidence_ids'], [CARD['evidence_id']])
        self.assertNotIn('ev_uncited_parent', payload['verified_evidence_ids'])
        verdict, _ = self.review(direction_assessment(evidence_ids=['ev_uncited_parent']), audit=audit)
        self.assertEqual(verdict.status, 'revise')

    def test_opposing_finding_cannot_be_packaged_as_a_winning_counterargument(self):
        review, _ = self.review(direction_assessment(response_strategy='counterargument'), text=BAD_REPLY)
        self.assertEqual(review.status, 'revise')

    def test_user_accusation_needs_an_actual_user_quote(self):
        for quote, supported in [('', False), ('You only count wins.', True),
                                 (CONVERSATION[1]['content'], True), ('What about teammates?', False)]:
            with self.subTest(quote=quote):
                review, _ = self.review(user_attributions=[dict(clause='You only count wins.',
                                                              user_quote=quote, supported=supported)])
                self.assertEqual(review.status, 'revise')
                self.assertFalse(review.argument_assessment.objection_faithful)

    def test_styling_cannot_add_attribution_missing_from_approved_baseline(self):
        audit = fixture_audit()
        audit['approved_argument'] = CONCESSION
        audit['approved_user_attributions'] = []
        verdict, _ = self.review(audit=audit, user_attributions=[dict(
            clause='You think the teammate numbers settle everything.',
            user_quote='What about teammates?', supported=True)])
        self.assertEqual(verdict.status, 'revise')
        self.assertTrue(any('absent from the approved argument' in issue.reason for issue in verdict.issues))

    def test_grounded_user_accusation_is_allowed(self):
        audit = fixture_audit()
        quote = 'SGA is better solely because his team wins more.'
        audit['conversation'][0]['content'] = quote
        review, _ = self.review(audit=audit, user_attributions=[dict(
            clause='You treat team wins as proof of individual superiority.', user_quote=quote, supported=True)])
        self.assertEqual(review.status, 'pass')

    def test_inconclusive_mixed_or_missing_evidence_can_pass(self):
        for ids in [[], [CARD['evidence_id']]]:
            with self.subTest(ids=ids):
                review, _ = self.review(direction_assessment(evidence_direction='inconclusive',
                    evidence_ids=ids, response_strategy='qualified_answer',
                    reason='This selected scoring sample does not settle overall roster quality.'))
                self.assertEqual(review.status, 'pass')

    def test_two_metrics_for_one_example_do_not_trigger_count_guard(self):
        audit = fixture_audit()
        audit['cards'].append(dict(CARD, title='AST', value=5.1, other_value=4.4))
        review, _ = self.review(direction_assessment(advantage_example_count=1), audit=audit)
        self.assertEqual(review.status, 'pass')

    def test_actual_evidence_gap_preserves_needs_evidence(self):
        review, _ = self.review(direction_assessment(conclusion_supported=False,
            evidence_direction='inconclusive', evidence_ids=[]), status='needs_evidence',
            issues=[dict(clause='Better overall supporting cast', reason='Scoring alone is insufficient.',
                         missing_evidence='Comparable supporting-cast efficiency and availability in the same postseason.')])
        self.assertEqual(review.status, 'needs_evidence')

    def test_no_missing_evidence_field_does_not_send_argument_failure_to_research(self):
        review, _ = self.review(direction_assessment(conclusion_supported=False), status='needs_evidence',
            issues=[dict(clause='Luka had weaker help', reason='The cited comparison points the other way.')])
        self.assertEqual(review.status, 'revise')

    def test_new_live_review_requires_every_field_but_old_reviews_still_parse(self):
        self.assertEqual(Review.model_validate({'status':'pass', 'issues':[]}).status, 'pass')
        for field in direction_assessment():
            findings = direction_assessment()
            del findings[field]
            with self.subTest(field=field):
                review, _ = self.review(findings)
                self.assertEqual(review.status, 'revise')
        with patch('backend.debate.agent.complete', return_value=response('{"status":"pass","issues":[]}')):
            review = semantic_review(Draft(response=CONCESSION), fixture_audit(), 'Teammates?', 'debate', CONFIG, None)
        self.assertEqual(review.status, 'revise')

    def test_role_mapping_and_comparison_order_are_explicit(self):
        for config in [CONFIG, DebateConfig(supported_player=LUKA, opponent_player=SHAI)]:
            audit = fixture_audit()
            for reverse in [False, True]:
                if reverse:
                    c = audit['cards'][0]
                    c['player'], c['other_player'] = c['other_player'], c['player']
                    c['value'], c['other_value'] = c['other_value'], c['value']
                    c['context'], c['other_context'] = c['other_context'], c['context']
                _, model = self.review(audit=audit, config=config)
                payload = json.loads(model.call_args.args[0][-1]['content'])
                self.assertEqual(payload['defended_player_id'], config.opponent_player)
                self.assertEqual(payload['verified_cards'], audit['cards'])
                self.assertEqual(payload['argument_plan']['target_claim'], TARGET)

    def test_planner_and_reviewer_receive_ordered_dialogue_not_tool_messages(self):
        messages = [dict(role='system', content='System rules'), *CONVERSATION,
                    dict(role='assistant', content='Internal', tool_calls=[{'id':'x'}]),
                    dict(role='tool', content='Tool data')]
        dialogue = conversation_text(messages)
        self.assertEqual(dialogue, CONVERSATION)
        context = DebateTools(CONFIG, data=FakeData())
        context.mode, context.conversation = 'debate', dialogue
        with patch('backend.debate.agent.complete', return_value=response(json.dumps(
                dict(research_tools=[], guidance='A scoped concession needs no additional research.',
                     team_context_reason='No new empirical support claim.', team_context_intent='unrelated', support_scope='none', team_success_argument=False)))) as model:
            review_plan(PLAN, 'What about teammates?', context)
        self.assertEqual(json.loads(model.call_args.args[0][-1]['content'])['conversation'], dialogue)
        _, model = self.review()
        self.assertEqual(json.loads(model.call_args.args[0][-1]['content'])['conversation'], dialogue)

    def test_legacy_plan_loads_but_new_debate_plan_requires_dimension_and_claim(self):
        legacy = {k:v for k,v in PLAN.items() if k not in ('discussion_dimension', 'target_claim')}
        self.assertEqual(ArgumentPlan.model_validate(legacy).discussion_dimension, '')
        with self.assertRaises(ValueError):
            DebateArgumentPlan.model_validate(legacy)

    def test_zero_advantage_is_published_and_assessment_saved_without_consuming_evidence(self):
        state = {'topics':['Earlier topic'], 'concessions':['Earlier concession']}
        original = copy.deepcopy(state)
        with patch('backend.debate.agent.complete', side_effect=[
                submit({'response':'This comparison does not establish overall roster superiority.'}), review_response()]):
            result = run_debate([{'role':'system', 'content':''}] + copy.deepcopy(CONVERSATION), 'debate', CONFIG, {}, state=state, data=FakeData())
        self.assertEqual(result[2]['review_status'], 'reviewed')
        self.assertEqual(result[2]['validation']['attempts'][0]['argument_assessment'], assessment())
        self.assertFalse(result[4]['used_fact_keys'])
        self.assertEqual(state, original)
        review_log = next(x for x in result[1] if x['name']=='review_argument')
        self.assertEqual(json.loads(review_log['result'])['argument_assessment'], assessment())

    def test_roast_receives_baseline_and_rejects_concession_as_victory(self):
        audit = fixture_audit()
        audit['approved_assessment'] = direction_assessment()
        with patch('backend.debate.agent.complete', side_effect=[response(json.dumps({'response':BAD_REPLY})),
                review_response(dict(status='pass', issues=[], argument_assessment=direction_assessment(
                    conclusion_supported=False, objection_faithful=False)))]) as model:
            draft, status = style_approved_reply(Draft(response=CONCESSION), audit, 'What about teammates?',
                                                 'debate', CONFIG, None, ActivityLog())
        self.assertEqual(draft.response, CONCESSION)
        self.assertEqual(status, 'reasoned_fallback')
        payload = json.loads(model.call_args_list[0].args[0][-1]['content'])
        self.assertEqual(payload['approved_assessment'], direction_assessment())
        self.assertEqual(payload['conversation'], CONVERSATION)
