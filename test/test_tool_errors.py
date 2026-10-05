"""Offline regressions for actionable feedback and model recovery after failure."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests
import app
from backend.activity import ActivityLog
from backend.assistant import tools
from backend.assistant.research_tools import AssistantResearchTools, TOOLS
from backend.debate.agent import PLAN_TOOL, SUBMIT_TOOL, DebateArgumentPlan
from backend.debate.tools import DEBATE_TOOLS
from backend.tool_errors import parse_tool_arguments
from test.test_debate import FakeData, response, tool_call
from test.test_tools import sample


class ToolErrorTests(unittest.TestCase):
    def test_model_can_repair_json_without_losing_other_calls_in_batch(self):
        bad = tool_call('find_games', {}).tool_calls[0]
        bad.function.arguments = '{"date":'
        panel = tool_call('display_panel', {'panel': {'player': 'Alex Adams'}}).tool_calls[0]
        fixed = tool_call('find_games', {'date': '2026-01-15'})
        fixed.tool_calls[0].id = 'corrected_find_games'
        replies = [response(calls=[bad, panel]), fixed, response('No games found.')]
        messages = [{'role': 'user', 'content': 'Find games on January 15, 2026.'}]

        def completion(*args, **kwargs):
            if len(replies) == 2:
                feedback = [m for m in kwargs['messages'] if m['role'] == 'tool']
                self.assertEqual([m['tool_call_id'] for m in feedback], [bad.id, panel.id])
                self.assertIn('JSON object', json.loads(feedback[0]['content'])['next_action'])
                self.assertEqual(json.loads(feedback[1]['content'])['player'], 'Alex Adams')
            return SimpleNamespace(choices=[SimpleNamespace(message=replies.pop(0))])

        with patch.object(app, 'completion_with_backoff', side_effect=completion), \
             patch.object(tools, '_fetch', return_value={'events': []}) as fetch:
            answer, activity = app.run_agent(messages)
        self.assertEqual(answer, 'No games found.')
        fetch.assert_called_once()
        self.assertEqual([c['status'] for c in activity], ['failed', 'completed', 'completed'])
        self.assertEqual(sum(m['role'] == 'tool' for m in messages), 3)

    def test_non_objects_and_nonfinite_json_are_rejected(self):
        for raw in ('null', '[]', '42', '"name"', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', None):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, 'JSON'):
                parse_tool_arguments(raw)
        self.assertEqual(parse_tool_arguments('{"limit": 1}'), {'limit': 1})

    def test_invalid_arguments_are_actionable_and_do_not_fetch(self):
        cases = [({'limit': 101}, 'from 1 to 100'), ({'offset': -1}, 'greater than or equal to 0'),
                 ({'date': '2026-02-30'}, 'YYYY-MM-DD'), ({'date': '2026-1-15'}, 'YYYY-MM-DD'),
                 ({'limit': True}, 'int'), ({'extra': 1}, 'extra')]
        with patch.object(tools, '_fetch') as fetch:
            for args, expected in cases:
                with self.subTest(args=args):
                    result = json.loads(tools.run_tool('game_plays', args))
                    self.assertEqual(result['code'], 'invalid_arguments')
                    self.assertIn(expected, result['error'])
                    self.assertTrue(result['next_action'])
            result = json.loads(tools.run_tool('player_advanced_stats', {'player_name': 'Alex', 'metric': 'unknown'}))
            self.assertIn('ts_pct', result['error'])
            fetch.assert_not_called()

    def test_http_and_timeout_errors_omit_raw_exception_but_guide_recovery(self):
        for status, instruction in [(400, 'Verify'), (401, 'denied access'), (403, 'denied access'),
                                    (404, 'Verify'), (429, 'Stop immediate retries'), (503, 'at most once')]:
            resp = requests.Response()
            resp.status_code = status
            with self.subTest(status=status), patch.object(tools, '_fetch', side_effect=requests.HTTPError('PRIVATE BODY', response=resp)):
                result = json.loads(tools.game_info(event_id='100'))
                self.assertEqual(result['http_status'], status)
                self.assertIn(instruction, result['next_action'])
                self.assertNotIn('PRIVATE BODY', json.dumps(result))
        for error, code in [(requests.Timeout('PRIVATE HOST'), 'timeout'),
                            (requests.ConnectionError('PRIVATE HOST'), 'network_error')]:
            with patch.object(tools, '_fetch', side_effect=error):
                result = json.loads(tools.game_info(event_id='100'))
                self.assertEqual(result['code'], code)
                self.assertIn('at most once', result['next_action'])
                self.assertNotIn('PRIVATE HOST', json.dumps(result))

    def test_malformed_upstream_data_stays_inside_tool_boundary(self):
        data = sample()
        data['boxscore']['players'] = None
        cases = [(None, 'game_info', {}), ({'header': None}, 'game_info', {}),
                 ({'header': {'competitions': [None]}}, 'game_info', {}),
                 (data, 'player_info', {'player_name': 'Alex Adams'})]
        with patch.object(tools.logger, 'exception'):
            for payload, name, args in cases:
                with self.subTest(payload=payload), patch.object(tools, '_fetch', return_value=payload):
                    result = json.loads(tools.run_tool(name, {'event_id': '100', **args}))
                    self.assertEqual(result['status'], 'unavailable')
                    self.assertTrue(result['next_action'])
                    self.assertNotIn('Traceback', json.dumps(result))

    def test_non_json_and_non_object_http_responses_are_not_empty_successes(self):
        for value in ([], None, 'invalid_json'):
            resp = SimpleNamespace(raise_for_status=lambda: None)
            resp.json = (lambda: value)
            if value == 'invalid_json':
                def invalid():
                    raise ValueError('RAW RESPONSE')
                resp.json = invalid
            with self.subTest(value=value), patch.object(tools.requests, 'get', return_value=resp):
                result = json.loads(tools.find_games())
                self.assertIn('error', result)
                self.assertNotIn('RAW RESPONSE', json.dumps(result))

    def test_missing_schedule_is_not_reported_as_no_games(self):
        for payload in ({}, {'events': None}, {'events': {}}):
            with self.subTest(payload=payload), patch.object(tools, '_fetch', return_value=payload):
                result = json.loads(tools.find_games())
                self.assertIn('error', result)
                self.assertNotIn('games', result)
        with patch.object(tools, '_fetch', return_value={'events': []}):
            self.assertEqual(json.loads(tools.find_games())['games'], [])

    def test_missing_participants_do_not_return_empty_scores(self):
        for competitors in (None, [], [None], [{}, {}]):
            data = sample()
            data['header']['competitions'][0]['competitors'] = competitors
            with self.subTest(competitors=competitors), patch.object(tools, '_fetch', return_value=data):
                result = json.loads(tools.game_score(event_id='100'))
                self.assertIn('error', result)
                self.assertNotIn('scores', result)

    def test_lookup_failures_preserve_candidates_and_mark_activity_failed(self):
        with patch.object(tools, '_fetch', return_value=sample()):
            result = tools.player_game_stats('Alex Adams', event_id='100', stat='nonsense')
        parsed = json.loads(result)
        self.assertIn('points', parsed['available_stats'])
        self.assertIn('Choose', parsed['next_action'])
        self.assertEqual(parsed['message'], parsed['error'])
        activity = ActivityLog()
        activity.append({'name': 'player_game_stats', 'args': {}, 'result': result})
        self.assertEqual(activity[0]['status'], 'failed')

    def test_research_validation_lists_fields_without_dumping_inputs(self):
        research = AssistantResearchTools(data=FakeData())
        with patch.object(research.data, 'fetch') as fetch:
            result = research.run('compare_players', {'left_player': -1, 'right_player': -2,
                'metrics': ['bad'], 'left_scope': {'start': 2017}})
            fields = {item['field'] for item in result['fields']}
            self.assertTrue({'left_player', 'right_player', 'metrics.0', 'left_scope'} <= fields)
            self.assertEqual(result['code'], 'invalid_arguments')
            self.assertNotIn('errors.pydantic.dev', json.dumps(result))
            self.assertNotIn('input_value', json.dumps(result))
            self.assertIn('invalid_arguments', research.run('resolve_player', {'query': '   '})['code'])
            fetch.assert_not_called()

    def test_research_unexpected_failure_and_unavailable_data_are_distinct(self):
        research = AssistantResearchTools(data=FakeData())
        with patch.object(research.data, 'fetch', side_effect=AttributeError('PRIVATE PATH')), \
             patch('backend.debate.tools.logger.exception'):
            result = research.run('query_evidence', {'player_id': 1})
        self.assertEqual(result['code'], 'internal_error')
        self.assertNotIn('PRIVATE PATH', json.dumps(result))
        research.data.fail = True
        result = research.run('query_evidence', {'player_id': 1})
        self.assertEqual(result['code'], 'data_unavailable')
        self.assertIn('do not repeat', result['next_action'])

    def test_public_schemas_describe_nested_parameters_and_constrain_codes(self):
        def check(node, path):
            if isinstance(node, dict):
                for name, prop in node.get('properties', {}).items():
                    self.assertTrue(prop.get('description'), path + '.' + name)
                for key, value in node.items():
                    check(value, path + '.' + key)
            elif isinstance(node, list):
                for value in node:
                    check(value, path)
        for tool in TOOLS + DEBATE_TOOLS + [PLAN_TOOL, SUBMIT_TOOL]:
            check(tool['function']['parameters'], tool['function']['name'])
        check(DebateArgumentPlan.model_json_schema(), 'debate_plan')
        compare = next(t for t in TOOLS if t['function']['name'] == 'compare_players')
        self.assertIn('TS_PCT', compare['function']['parameters']['properties']['metrics']['items']['enum'])
        counter = next(t for t in TOOLS if t['function']['name'] == 'find_counterexamples')
        self.assertNotIn('GP', counter['function']['parameters']['properties']['metric']['enum'])


if __name__ == '__main__':
    unittest.main()
