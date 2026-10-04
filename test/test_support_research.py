"""Precommit research breadth and retain every pair regardless of results."""
import copy
import json
import unittest
from unittest.mock import patch

from backend.debate.agent import run_debate, review_plan, PlanReview, LivePlanReview, Review
from backend.debate.support import SupportResearch, TeammatePair
from backend.debate.tools import DebateTools, DebateConfig
from test.test_competitive_context import ContextData
from test.test_debate import row, tool_call, submit, response


def fixture():
    data = ContextData()
    data.rosters[20][1]['PTS'] = 400  # Defended Beta's first co-star has more scoring support.
    data.rosters[20].append(dict(row(team=20, PTS=50, MIN=180), PLAYER_ID=6, PLAYER_NAME='Second Beta mate'))
    config = DebateConfig(supported_player=1, opponent_player=2)
    args = dict(left_player=1, right_player=2, left_season=1995, right_season=1995,
                phase='Playoffs', selection_reason='A shared postseason for the team-credit question.')
    pairs = [TeammatePair(**args, left_teammate_id=3, right_teammate_id=5,
                         teammate_selection_reason='Mate and Rival mate are the primary co-stars by production and role; compare Other and Second Beta mate separately.'),
             TeammatePair(**args, left_teammate_id=4, right_teammate_id=6,
                         teammate_selection_reason='Other and Second Beta mate are the next important non-focal contributors, using the same criteria as the first pair.')]
    plan = dict(discussion_dimension='Supporting talent', target_claim='Beta had less supporting help in this postseason sample.',
                objection='Team achievements are being used as proof of individual superiority.',
                approach='Compare important co-stars on both sides without assuming a favorable direction.',
                evidence_needed=['Two representative support pairs in the same postseason.'],
                scope_reason='A shared postseason sample, not a whole-career explanation.', research_tools=['compare_competitive_context'])
    return data, config, args, pairs, plan


class SupportResearchTests(unittest.TestCase):
    def setUp(self):
        self.data, self.config, self.args, self.pairs, self.plan = fixture()
        self.context = DebateTools(self.config, data=self.data)
        self.tracker = SupportResearch(self.config)
        self.tracker.register('two_pairs', [])

    def discover(self):
        p, _ = self.tracker.before_query(self.args)
        result = self.context.run('compare_competitive_context', p.model_dump())
        self.tracker.observe(p, result)
        return result

    def test_live_classification_cannot_drop_team_credit_investigation(self):
        common = dict(research_tools=[], guidance='Investigate.', team_context_reason='Team outcomes are used as individual credit.', team_success_argument=False)
        self.assertEqual(LivePlanReview(**common, team_context_intent='individual_credit', support_scope='none').support_scope,'two_pairs')
        with self.assertRaises(ValueError):
            LivePlanReview(**common, team_context_intent='count_only', support_scope='two_pairs')
        for scope in ('strongest_pair','two_pairs'):
            advice = LivePlanReview(**common, team_context_intent='individual_credit', support_scope=scope)
            self.assertEqual(advice.research_tools, ['compare_competitive_context'])
        self.assertFalse(LivePlanReview(**common, team_context_intent='count_only', support_scope='none').research_tools)

    def test_single_pair_requires_user_scope_not_writers_narrower_plan(self):
        verdict = dict(research_tools=['compare_competitive_context'], guidance='Compare co-stars.',
            team_context_reason='Team credit.', team_context_intent='individual_credit', support_scope='strongest_pair', team_success_argument=True)
        for quote, question, expected in [('', 'Titles prove superiority.', 'two_pairs'),
                ('Compare only the strongest helper.', 'Titles prove superiority.', 'two_pairs'),
                ('Compare only the strongest helper.', 'Compare only the strongest helper.', 'strongest_pair')]:
            with self.subTest(quote=quote, question=question), patch('backend.debate.agent.complete',
                    return_value=response(json.dumps(dict(verdict, single_pair_user_quote=quote)))):
                self.assertEqual(review_plan(self.plan, question, self.context).support_scope, expected)

    def test_both_pairs_must_be_registered_before_first_exact_query(self):
        self.assertEqual(self.tracker.snapshot()['next_action']['tool'], 'compare_competitive_context')
        self.discover()
        self.assertEqual(self.tracker.snapshot()['next_action']['tool'], 'plan_argument')
        with self.assertRaises(ValueError):
            self.tracker.before_query(self.pairs[0].model_dump())
        with self.assertRaises(ValueError):
            self.tracker.register('two_pairs', self.pairs[:1])
        self.tracker.register('two_pairs', self.pairs)
        p, _ = self.tracker.before_query(self.pairs[0].model_dump())
        first = self.context.run('compare_competitive_context', p.model_dump())
        self.tracker.observe(p, first)
        self.assertEqual([t['status'] for t in self.tracker.tasks], ['completed','pending'])
        self.assertTrue(self.tracker.pending())
        self.assertEqual(self.tracker.snapshot()['next_action']['args']['left_teammate_id'], 4)
        self.tracker.register('none', [])
        self.assertEqual(self.tracker.required, 2)
        with self.assertRaises(ValueError):
            self.tracker.register('two_pairs', list(reversed(self.pairs)))

    def test_roster_membership_focal_ids_and_distinct_teammates_are_checked(self):
        self.discover()
        for pairs in [[self.pairs[0],self.pairs[0]],
                      [self.pairs[0],self.pairs[1].model_copy(update={'right_teammate_id':999})],
                      [self.pairs[0],self.pairs[1].model_copy(update={'left_teammate_id':2})]]:
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                self.tracker.register('two_pairs', pairs)
        self.assertTrue(all(t['request'] is None for t in self.tracker.tasks))

    def test_failed_pair_is_unavailable_and_not_queried_again(self):
        self.discover()
        self.tracker.register('two_pairs', self.pairs)
        p, _ = self.tracker.before_query(self.pairs[0].model_dump())
        self.tracker.observe(p, {'status':'partial','error':'Data unavailable'})
        _, cached = self.tracker.before_query(self.pairs[0].model_dump())
        self.assertEqual(cached['status'], 'partial')
        self.assertEqual(self.tracker.tasks[0]['status'], 'unavailable')
        self.assertTrue(self.tracker.pending())

    def test_strongest_pair_completes_one_and_cannot_expand_after_result(self):
        self.tracker = SupportResearch(self.config)
        self.tracker.register('strongest_pair', [])
        self.discover()
        self.tracker.register('strongest_pair', self.pairs[:1])
        p, _ = self.tracker.before_query(self.pairs[0].model_dump())
        self.tracker.observe(p, self.context.run('compare_competitive_context', p.model_dump()))
        self.assertFalse(self.tracker.pending())
        with self.assertRaises(ValueError):
            self.tracker.register('two_pairs', self.pairs)

    def test_insufficient_roster_leaves_explicit_gap_not_a_weak_teammate(self):
        self.data.rosters[20].pop()
        self.discover()
        self.assertEqual(self.tracker.tasks[1]['status'], 'unavailable')
        self.tracker.register('two_pairs', self.pairs[:1])
        self.assertEqual(self.tracker.required, 2)

    def test_pipeline_keeps_second_pair_pending_and_both_comparisons_visible(self):
        outputs = [tool_call('plan_argument', self.plan), tool_call('compare_competitive_context', self.args),
            tool_call('plan_argument', dict(self.plan, teammate_pairs=[p.model_dump() for p in self.pairs])),
            tool_call('compare_competitive_context', self.pairs[0].model_dump()),
            submit({'response':'Premature whole-support conclusion.'}),
            tool_call('compare_competitive_context', self.pairs[1].model_dump()),
            submit({'response':'This selected supporting sample does not establish whole-roster superiority.'})]
        advice = PlanReview(research_tools=['compare_competitive_context'], guidance='Complete both pairs.',
                            team_context_reason='Team-credit inference.', team_context_intent='individual_credit', support_scope='two_pairs')
        with patch('backend.debate.agent.complete', side_effect=outputs), \
             patch('backend.debate.agent.review_plan', return_value=advice), \
             patch('backend.debate.agent.semantic_review', return_value=Review(status='pass')) as reviewer:
            result = run_debate([{'role':'system','content':''},{'role':'user','content':'Alpha has more championships so is better.'}],
                'debate', self.config, {}, data=self.data)
        self.assertEqual(reviewer.call_count, 1)
        self.assertEqual([t['status'] for t in result[2]['support_research']['tasks']], ['completed','completed'])
        self.assertEqual(len(result[2]['comparisons']), 2)
        self.assertFalse(result[4]['used_fact_keys'])  # Researched but uncited.
        feedback = [json.loads(c['result']) for c in result[1] if c['name']=='submit_argument']
        self.assertEqual(feedback[0]['status'], 'needs_evidence')
        self.assertEqual(feedback[0]['submissions_remaining'], 3)
        self.assertEqual(result[2]['validation']['submissions'], 1)
        audit = reviewer.call_args.args[1]
        self.assertEqual(len(audit['comparisons']), 2)

    def test_budget_exhaustion_reports_partial_coverage_without_asserting_whole_roster(self):
        with patch('backend.debate.agent.complete', side_effect=[tool_call('plan_argument', self.plan)]), \
             patch('backend.debate.agent.review_plan', return_value=PlanReview(research_tools=[], guidance='Two pairs.',
                   team_context_reason='Team-credit.', team_context_intent='individual_credit', support_scope='two_pairs')):
            result = run_debate([{'role':'system','content':''},{'role':'user','content':'Team wins prove superiority.'}],
                'debate', self.config, {}, data=self.data, max_rounds=1)
        self.assertIn('0 of 2', result[0])
        self.assertTrue(all(t['status']=='unavailable' for t in result[2]['support_research']['tasks']))

    def test_rejected_planning_cannot_be_bypassed_by_submitting(self):
        with patch('backend.debate.agent.complete', side_effect=[tool_call('plan_argument', self.plan),
                   submit({'response':'Titles alone are inconclusive.'})]) as model, \
             patch('backend.debate.agent.review_plan', side_effect=ValueError('Inconsistent support scope')), \
             patch('backend.debate.agent.semantic_review') as reviewer:
            result = run_debate([{'role':'system','content':''},{'role':'user','content':'Titles prove superiority.'}],
                'debate', self.config, {}, data=self.data, max_rounds=2)
        for call in model.call_args_list:
            self.assertEqual(call.kwargs['tool_choice']['function']['name'], 'plan_argument')
        reviewer.assert_not_called()
        self.assertEqual(result[2]['review_status'], 'limited')
        self.assertEqual(result[2]['validation']['submissions'], 0)
        self.assertIn('Repair the rejected plan_argument', result[1][-1]['result'])
