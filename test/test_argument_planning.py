"""Public plans guide model-selected research without executing or certifying it."""
from test.test_debate import review_response, assessment
import json
import unittest
from unittest.mock import patch

from backend.debate.agent import Draft, Review, PlanReview, run_debate, semantic_review
from backend.debate.tools import DebateConfig
from test.test_debate import FakeData, response, submit, tool_call


class ArgumentPlanningTests(unittest.TestCase):
    def setUp(self):
        reviewer = patch('backend.debate.agent.review_plan', side_effect=lambda plan, question, context: PlanReview(research_tools=plan['research_tools'], guidance='Investigate the selected question.'))
        self.plan_reviewer = reviewer.start()
        self.addCleanup(reviewer.stop)
        self.config = DebateConfig(supported_player=1, opponent_player=2, scope_mode='auto')
        self.data = FakeData()
        self.plan = dict(team_context_reason='', team_context_intent='unrelated', support_scope='none', teammate_pairs=[], replacement_reason='', discussion_dimension='Teammate support', target_claim='The defended player had less supporting talent in the selected season.', objection='Team success is being used to settle individual superiority.',
                         approach='Investigate whether supporting talent qualifies individual credit.',
                         evidence_needed=['Compare representative strong teammates on both sides.'],
                         scope_reason='Choose a relevant shared postseason, not an entire-career verdict.', research_tools=[])

    def checked(self, plan):
        return dict(plan, guidance='Investigate the selected question.', team_success_argument=False)

    def run_chat(self):
        return run_debate([{'role':'system','content':''}, {'role':'user','content':'One has more rings, therefore he is better.'}],
                          'debate', self.config, {'1':'Alpha','2':'Beta'}, data=self.data)

    def test_plan_is_visible_but_does_not_fetch_or_become_evidence(self):
        with patch.object(self.data, 'fetch', side_effect=AssertionError('A plan must not fetch data')), \
             patch('backend.debate.agent.complete', side_effect=[
                 tool_call('plan_argument', self.plan),
                 submit({'response':'I have not established who had stronger help; team success alone does not isolate individual credit.'}),
                 review_response({'status': 'pass', 'issues': []})]) as model:
            result = self.run_chat()
        self.assertEqual(result[2]['argument_plan'], self.checked(self.plan))
        self.assertFalse(result[2]['cards'])
        self.assertIsNone(result[2]['research'])
        self.assertEqual(result[1][0]['name'], 'plan_argument')
        self.assertEqual(result[1][0]['status'], 'completed')
        review = json.loads(model.call_args_list[-1].args[0][-1]['content'])
        self.assertEqual(review['argument_plan'], self.checked(self.plan))
        self.assertFalse(review['verified_cards'])
        self.assertEqual(result[2]['validation']['submissions'], 1)

    def test_revised_plan_reaches_reviewer_without_erasing_prior_activity(self):
        changed = dict(self.plan, approach='The available evidence is insufficient; limit the conclusion.')
        needs = review_response({'status':'needs_evidence','issues':[{
            'clause':'Beta had weaker help.', 'reason':'No supporting-player comparison.',
            'missing_evidence':'Both sides in a compatible sample.'}]})
        with patch('backend.debate.agent.complete', side_effect=[
                tool_call('plan_argument', self.plan), submit({'response':'Beta had weaker help.'}), needs,
                tool_call('plan_argument', changed),
                submit({'response':'I cannot establish which player had stronger help from the available evidence.'}),
                review_response({'status': 'pass', 'issues': []})]) as model:
            result = self.run_chat()
        self.assertEqual(result[2]['argument_plan'], self.checked(changed))
        self.assertEqual([x['args'] for x in result[1] if x['name']=='plan_argument'], [self.plan, changed])
        self.assertEqual([x['semantic_status'] for x in result[2]['validation']['attempts']], ['needs_evidence','pass'])
        reviews = [json.loads(c.args[0][-1]['content']) for c in model.call_args_list if 'response_format' in c.kwargs]
        self.assertEqual([r['argument_plan'] for r in reviews], [self.checked(self.plan), self.checked(changed)])
        self.assertFalse(reviews[0]['prior_review_attempts'])
        self.assertEqual(reviews[1]['prior_review_attempts'][0]['semantic_status'], 'needs_evidence')
        self.assertEqual(reviews[1]['research_activity'][-1]['args'], changed)

    def test_invalid_plan_preserves_last_valid_plan_and_submission_budget(self):
        with patch('backend.debate.agent.complete', side_effect=[
                tool_call('plan_argument', self.plan), tool_call('plan_argument', {'objection':'Incomplete'}),
                tool_call('plan_argument', self.plan),
                submit({'response':'The current evidence does not settle individual credit.'}),
                review_response({'status': 'pass', 'issues': []})]):
            result = self.run_chat()
        self.assertEqual(result[2]['argument_plan'], self.checked(self.plan))
        self.assertEqual(result[2]['validation']['submissions'], 1)
        self.assertEqual(result[1][1]['status'], 'failed')

    def test_review_receives_plan_and_counterevidence_separately_from_cited_cards(self):
        audit = {'cards':[], 'comparisons':[{'kind':'key_teammates','left_player':'Mate A','right_player':'Mate B'}],
                 'argument_plan':self.plan}
        with patch('backend.debate.agent.complete', return_value=review_response({'status': 'pass', 'issues': []})) as model:
            semantic_review(Draft(response='Evidence is insufficient.'), audit, 'Who had more help?', 'debate', self.config, None)
        payload = json.loads(model.call_args.args[0][-1]['content'])
        self.assertEqual(payload['argument_plan'], self.plan)
        self.assertEqual(payload['statistical_comparisons'], audit['comparisons'])
        self.assertFalse(payload['verified_cards'])
        schema = model.call_args.kwargs['response_format']['json_schema']['schema']
        self.assertIn('argument_assessment', schema['required'])

    def test_pass_cannot_override_evasive_or_unfinished_argument_assessment(self):
        for relevance, followthrough in [('evasive','complete'), ('addresses_objection','missing')]:
            with self.subTest(relevance=relevance, followthrough=followthrough), self.assertRaises(ValueError):
                Review.model_validate(dict(status='pass', checks=[], issues=[], argument_assessment=dict(
                    relevance=relevance, evidence_followthrough=followthrough, reason='The chosen comparison remains unverified.')))

    def test_limited_concession_can_pass_without_implying_a_support_advantage(self):
        review = Review.model_validate(dict(status='pass', checks=[], issues=[], argument_assessment=dict(
            relevance='limited_concession', evidence_followthrough='not_needed', reason='Explicit uncertainty; no empirical advantage asserted.')))
        self.assertEqual(review.status, 'pass')

    def test_changing_plan_does_not_erase_unattempted_model_selected_research(self):
        planned = dict(self.plan, research_tools=['compare_players'])
        with patch('backend.debate.agent.complete', side_effect=[
                tool_call('plan_argument', planned), submit({'response':'Skip the research.'}),
                tool_call('plan_argument', self.plan), submit({'response':'Try skipping again.'}),
                tool_call('compare_players', {'left_player':1,'right_player':2}),
                submit({'response':'The available sample does not settle overall individual credit.'}),
                review_response({'status': 'pass', 'issues': []})]):
            result = self.run_chat()
        submissions = [c for c in result[1] if c['name']=='submit_argument']
        self.assertEqual([c['status'] for c in submissions], ['needs_evidence','needs_evidence','completed'])
        self.assertEqual(result[2]['validation']['submissions'], 1)
        self.assertEqual(len([c for c in result[1] if c['name']=='review_argument']), 1)

    def test_roster_discovery_does_not_complete_selected_teammate_investigation(self):
        from test.test_competitive_context import ContextData
        self.data = ContextData()
        args = dict(left_player=1,right_player=2,left_season=1995,right_season=1995,
                    selection_reason='Compare the same shared season.')
        with patch('backend.debate.agent.complete', side_effect=[
                tool_call('plan_argument', dict(self.plan, research_tools=['compare_competitive_context'])),
                tool_call('compare_competitive_context', args), submit({'response':'The roster alone proves it.'}),
                tool_call('compare_competitive_context', dict(args,left_teammate_id=3,right_teammate_id=5,
                    teammate_selection_reason='Compare the leading secondary scorers using the same criteria.')),
                submit({'response':'This selected sample does not establish a career-wide supporting-cast advantage.'}),
                review_response({'status': 'pass', 'issues': []})]):
            result = self.run_chat()
        submits = [c for c in result[1] if c['name']=='submit_argument']
        self.assertEqual(submits[0]['status'],'needs_evidence')
        self.assertEqual(submits[1]['status'],'completed')
        self.assertEqual(result[2]['validation']['submissions'], 1)
        self.assertEqual(result[2]['comparisons'][0]['kind'],'key_teammates')

    def test_unavailable_lookup_allows_honest_uncertainty_instead_of_looping(self):
        self.data.fail = True
        with patch('backend.debate.agent.complete', side_effect=[
                tool_call('plan_argument', dict(self.plan, research_tools=['compare_players'])),
                tool_call('compare_players', {'left_player':1,'right_player':2}),
                submit({'response':'The comparison is unavailable, so I cannot establish an advantage.'}),
                review_response({'status': 'pass', 'issues': []})]):
            result = self.run_chat()
        self.assertEqual(result[2]['validation']['submissions'], 1)
        self.assertEqual(result[2]['review_status'],'reviewed')
        self.assertFalse(result[2]['cards'])

    def test_plan_advisor_failure_does_not_commit_the_turn(self):
        self.plan_reviewer.side_effect = RuntimeError('Provider unavailable')
        with patch('backend.debate.agent.complete', return_value=tool_call('plan_argument', self.plan)):
            with self.assertRaisesRegex(RuntimeError, 'Provider unavailable'):
                self.run_chat()

    def test_focused_check_overrides_initial_approval_of_mixed_award_and_load_clause(self):
        clause = 'Alpha carried elite offensive loads, earning an MVP.'
        initial = dict(user_attributions=[], argument_assessment=assessment(), status='pass', issues=[], checks=[dict(clause=clause,evidence_ids=['ev_award'],assessment='supported')])
        critique = dict(issues=[dict(clause='Alpha carried elite offensive loads',
                                    reason='MVP counts establish an award, not workload.',
                                    missing_evidence='Workload evidence for Alpha in the discussed scope.')])
        with patch('backend.debate.agent.complete', side_effect=[response(json.dumps(initial)), response(json.dumps(critique))]) as model:
            review = semantic_review(Draft(response=clause), {'cards':[{'evidence_id':'ev_award','title':'MVP','value':1,'player':'Alpha','unit':'awards','scope':'career'}]},
                                     'Who contributed more?', 'debate', self.config, None)
        self.assertEqual(review.status, 'needs_evidence')
        self.assertEqual(review.issues[0].clause, 'Alpha carried elite offensive loads')
        self.assertEqual(review.checks[0].assessment, 'unsupported')
        payload = json.loads(model.call_args.args[0][-1]['content'])
        self.assertEqual(payload['draft'], clause)
        self.assertNotIn('status', payload)  # The check is not anchored on earlier approval.

    def test_rejected_draft_does_not_spend_an_extra_inference_check(self):
        initial = dict(user_attributions=[], argument_assessment=assessment(), status='needs_evidence',checks=[],issues=[dict(clause='Alpha had stronger help.',reason='No comparison.')])
        with patch('backend.debate.agent.complete', return_value=response(json.dumps(initial))) as model:
            review = semantic_review(Draft(response='Alpha had stronger help.'), {'cards':[]},
                                     'Who had stronger help?', 'debate', self.config, None)
        self.assertEqual(review.status, 'needs_evidence')
        model.assert_called_once()
