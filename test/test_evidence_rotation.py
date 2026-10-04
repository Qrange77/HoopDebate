"""Only final cited facts are consumed; independent same-season dimensions stay usable."""
import copy
import json
import unittest
from unittest.mock import patch

from backend.debate.agent import Review, ReviewIssue, evidence_catalog, run_debate, agent_tools, canonical
from backend.debate.novelty import fact_keys, migrate_usage, used_fact
from backend.debate.tools import DebateConfig, DebateTools, Query, Scope, Claim
from test.test_debate import FakeData, submit, tool_call


class EvidenceRotationTests(unittest.TestCase):
    def setUp(self):
        self.config = DebateConfig(supported_player=1, opponent_player=2)
        self.data = FakeData()
        self.context = DebateTools(self.config, data=self.data)
        self.record = self.context.query_evidence(Query(player_id=1, scope=Scope(start=1995, end=1995)))
        self.claim = dict(evidence_id=self.record['id'], player_id=1,
                          scope=self.record['scope'], metric='PTS', value=20)

    def run_turn(self, state=None, evidence=None, metric='PTS', outputs=None, review=None, **kwargs):
        claim = dict(self.claim, metric=metric, value=self.record['values'][metric]['value'])
        model = {'side_effect':outputs} if outputs is not None else {'return_value':submit({
            'response':'A single scoped example.', 'claims':[claim]})}
        with patch('backend.debate.agent.complete', **model), \
             patch('backend.debate.agent.semantic_review', return_value=review or Review(status='pass')) as reviewer:
            result = run_debate([{'role':'system','content':''}, {'role':'user','content':'Compare.'}],
                'debate', self.config, {}, data=self.data,
                evidence=evidence or self.context.evidence, state=state, max_rounds=8, **kwargs)
        return result, reviewer

    def test_duplicate_facts_have_separate_budget_and_explicit_exhaustion(self):
        first, _ = self.run_turn()
        before = copy.deepcopy(first[4])
        second, reviewer = self.run_turn(first[4], first[3])
        reviewer.assert_not_called()
        self.assertEqual(second[2]['review_status'], 'limited')
        self.assertFalse(second[2]['cards'])
        validation = second[2]['validation']
        self.assertEqual(validation['submissions'], 0)
        self.assertEqual(validation['citation_repairs'], 0)
        self.assertEqual(validation['novelty_repairs'], 3)
        self.assertEqual(validation['stop_reason'], 'novelty_repair_limit')
        self.assertEqual(validation['reason_code'], 'duplicate_evidence')
        self.assertIn('new example', second[0])
        for attempt in validation['attempts']:
            self.assertIsNone(attempt['submission'])
            self.assertEqual(attempt['semantic_status'], 'not_run')
            self.assertIn('PTS', attempt['issues'][0]['reason'])
        self.assertEqual(first[4], before)

    def test_same_season_unused_steals_reaches_review(self):
        first, _ = self.run_turn()
        second, reviewer = self.run_turn(first[4], first[3], metric='STL')
        reviewer.assert_called_once()
        self.assertEqual(second[2]['review_status'], 'reviewed')
        self.assertEqual(second[2]['validation']['novelty_repairs'], 0)
        self.assertEqual(len(second[4]['published_arguments']), 2)
        self.assertTrue(fact_keys(self.record, 'PTS') <= set(second[4]['used_fact_keys']))
        self.assertTrue(fact_keys(self.record, 'STL') <= set(second[4]['used_fact_keys']))
        self.assertFalse(fact_keys(self.record, 'AST') & set(second[4]['used_fact_keys']))
        # Both players and unselected metrics appeared in the table, but only PTS/STL were consumed.
        self.assertTrue(first[2]['comparisons'])

    def test_unused_metric_repeating_old_argument_is_still_semantically_rejected(self):
        first, _ = self.run_turn()
        rejected = Review(status='revise', issues=[ReviewIssue(clause='', reason='Repeats the previously published argument.')])
        second, reviewer = self.run_turn(first[4], first[3], metric='STL', review=rejected)
        self.assertEqual(reviewer.call_count, 3)
        self.assertEqual(second[2]['validation']['submissions'], 3)
        self.assertEqual(second[2]['validation']['novelty_repairs'], 0)
        self.assertEqual(second[4]['used_fact_keys'], first[4]['used_fact_keys'])
        self.assertEqual(reviewer.call_args.args[1]['published_arguments'], first[4]['published_arguments'])

    def test_catalog_and_schema_filter_used_metrics_not_entire_records(self):
        self.context.used_fact_keys = list(fact_keys(self.record, 'PTS'))
        self.context.mode = 'debate'
        facts = evidence_catalog(self.context, [self.record])['facts']
        self.assertNotIn('PTS', [f['metric'] for f in facts])
        self.assertIn('STL', [f['metric'] for f in facts])
        self.assertIn('BLK', [f['metric'] for f in facts])
        enum = agent_tools(self.context)[-1]['function']['parameters']['properties']['fact_ids']['items']['enum']
        self.assertEqual(set(enum), {f['fact_id'] for f in facts})
        # Used IDs remain resolvable for precise duplicate diagnostics, not as available citations.
        self.assertTrue(any(c.metric == 'PTS' for c in self.context.fact_claims.values()))

    def test_refetch_comparison_order_single_citation_and_totals_cannot_reset_usage(self):
        changed = dict(self.record, id='new', player_name='Different display name', sources=[], values={}, selection_reason='new wording')
        changed['scope'] = dict(self.record['scope'], basis='totals')
        self.assertEqual(fact_keys(changed, 'PTS'), fact_keys(self.record, 'PTS'))
        other = self.context.query_evidence(Query(player_id=2, scope=Scope(start=1995, end=1995)))
        pair = dict(kind='comparison', left=self.record, right=other)
        reverse = dict(kind='comparison', left=other, right=changed)
        self.assertEqual(fact_keys(pair, 'PTS'), fact_keys(reverse, 'PTS'))
        self.context.used_fact_keys = list(fact_keys(pair, 'PTS'))
        self.assertTrue(used_fact(self.context, changed, 'PTS'))
        self.assertFalse(used_fact(self.context, changed, 'AST'))
        fresh = dict(self.record, scope=dict(self.record['scope'], start=1996, end=1996))
        self.assertNotEqual(fact_keys(fresh, 'PTS'), fact_keys(self.record, 'PTS'))

    def test_game_team_and_opponent_sample_are_part_of_identity(self):
        for a, b in [(dict(kind='game', nba_game_id='a'), dict(kind='game', nba_game_id='b')),
                     (dict(context={'team_id':1}), dict(context={'team_id':2})),
                     (dict(request={'dimension':'opponent_splits','top_n':5}),
                      dict(request={'dimension':'opponent_splits','top_n':10}))]:
            self.assertNotEqual(fact_keys(dict(self.record, **a), 'PTS'), fact_keys(dict(self.record, **b), 'PTS'))
        renamed = dict(self.record, context={'team_id':1, 'team_name':'Old'})
        self.assertEqual(fact_keys(renamed, 'PTS'), fact_keys(dict(renamed, context={'team_id':1,'team_name':'New'}), 'PTS'))

    def test_duplicate_then_no_new_evidence_acknowledgment_passes(self):
        first, _ = self.run_turn()
        outputs = [submit({'response':'Repeated', 'claims':[self.claim]}),
                   submit({'response':'The earlier comparison addressed this. I have no new evidence to add.'})]
        second, reviewer = self.run_turn(first[4], first[3], outputs=outputs)
        reviewer.assert_called_once()
        self.assertEqual(second[2]['validation']['submissions'], 1)
        self.assertEqual(second[2]['validation']['novelty_repairs'], 1)
        self.assertEqual(second[2]['review_status'], 'reviewed')
        self.assertEqual(first[4]['used_fact_keys'], second[4]['used_fact_keys'])
        feedback = json.loads(next(c['result'] for c in second[1] if c['name']=='submit_argument'))
        self.assertTrue(feedback['available_facts'])
        self.assertIn('STL', [f['metric'] for f in feedback['available_facts']])

    def test_review_and_submission_share_attempt_identity(self):
        result, _ = self.run_turn()
        review = next(c for c in result[1] if c['name']=='review_argument')
        submission = next(c for c in result[1] if c['name']=='submit_argument')
        self.assertEqual(review['attempt_id'], submission['attempt_id'])
        self.assertEqual(review['attempt_id'], result[2]['validation']['attempts'][0]['attempt_id'])
        self.assertEqual(json.loads(review['result'])['attempt_id'], json.loads(submission['result'])['attempt_id'])

    def test_ev_identifier_in_fact_ids_is_not_guessed_and_returns_valid_options(self):
        outputs = [submit({'response':'Wrong kind of ID', 'fact_ids':[self.record['id']]}),
                   submit({'response':'I cannot establish that conclusion.'})]
        result, _ = self.run_turn(outputs=outputs)
        failure = json.loads(next(c['result'] for c in result[1] if c['name']=='submit_argument'))
        self.assertEqual(failure['failure_type'], 'citation')
        self.assertTrue(all(f['fact_id'].startswith('fact_') for f in failure['available_facts']))
        self.assertEqual(result[2]['validation']['citation_repairs'], 1)

    def test_old_session_migration_preserves_used_metrics_and_is_idempotent(self):
        first, _ = self.run_turn()
        turn = dict(response=first[0], debate_result=first[2], failed=False)
        state = {'used_evidence_keys':['old_record_hash']}
        context = DebateTools(self.config, evidence=first[3], data=self.data)
        migrate_usage(state, context, [turn], [], canonical)
        self.assertNotIn('used_evidence_keys', state)
        self.assertEqual(state['used_fact_keys'], first[4]['used_fact_keys'])
        self.assertFalse(state['usage_migration_incomplete'])
        before = copy.deepcopy(state)
        migrate_usage(state, context, [turn], [], canonical)
        self.assertEqual(state, before)
        second, reviewer = self.run_turn({'used_evidence_keys':['old_record_hash']}, first[3], metric='STL', history_turns=[turn])
        reviewer.assert_called_once()
        self.assertEqual(second[2]['review_status'], 'reviewed')

    def test_failed_drafts_and_unshown_limited_cards_are_not_migrated(self):
        first, _ = self.run_turn()
        context = DebateTools(self.config, evidence=first[3], data=self.data)
        failed = dict(response=first[0], debate_result=first[2], failed=True)
        hidden = dict(response='No approved reply.', debate_result=dict(first[2], review_status='limited'))
        state = {}
        migrate_usage(state, context, [failed, hidden], [], canonical)
        self.assertFalse(state['used_fact_keys'])
        displayed = dict(hidden, response=canonical(first[2]['cards'][0]))
        state = {}
        migrate_usage(state, context, [displayed], [], canonical)
        self.assertTrue(state['used_fact_keys'])

    def test_missing_old_records_keep_prose_without_blocking_whole_sample(self):
        state = {'used_evidence_keys':['old_record_hash']}
        turn = dict(response='Earlier scoring example.', debate_result={
            'review_status':'reviewed', 'claims':[dict(self.claim, evidence_id='missing')]})
        migrate_usage(state, self.context, [turn], [], canonical)
        self.assertTrue(state['usage_migration_incomplete'])
        self.assertFalse(state['used_fact_keys'])
        self.assertEqual(state['published_arguments'][0]['response'], turn['response'])
        self.assertTrue(evidence_catalog(self.context, [self.record])['facts'])

    def test_provider_failure_does_not_mutate_original_migration_state(self):
        first, _ = self.run_turn()
        state = {'used_evidence_keys':['old_hash']}
        original = copy.deepcopy(state)
        with patch('backend.debate.agent.complete', side_effect=RuntimeError('provider failed')):
            with self.assertRaises(RuntimeError):
                run_debate([{'role':'system','content':''},{'role':'user','content':'Again?'}],
                    'debate', self.config, {}, evidence=first[3], state=state, data=self.data,
                    history_turns=[dict(response=first[0], debate_result=first[2])])
        self.assertEqual(state, original)
