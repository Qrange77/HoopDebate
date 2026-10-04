"""Basic roster lookup and multi-call harness behavior without network access."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import app
from backend.assistant import tools
from test.test_tools import sample


def reply(content=None, calls=None):
    message = SimpleNamespace(content=content, tool_calls=calls)
    message.model_dump = lambda: {'role': 'assistant', 'content': content, 'tool_calls': [
        {'id': c.id, 'type': 'function', 'function': vars(c.function)} for c in calls or []]}
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def call(name, args, ident):
    return SimpleNamespace(id=ident, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class CompositionTests(unittest.TestCase):
    def test_roster_filters_duplicates_and_missing(self):
        data = sample()
        squad = data['boxscore']['players'][0]
        squad['statistics'].append(copy.deepcopy(squad['statistics'][0]))
        with patch.object(tools, '_fetch', return_value=data):
            result = json.loads(tools.game_players(event_id='100'))
            self.assertEqual(len(result['players']), 1)
            self.assertFalse(result['complete'])
            self.assertEqual(result['missing_teams'], ['Beta Bears'])
            filtered = json.loads(tools.game_players(event_id='100', team_name='AAA'))
            self.assertTrue(filtered['complete'])
            self.assertEqual(set(filtered['players'][0]), {'player_id', 'player_name', 'team_name', 'did_not_play'})
            squad['statistics'][0]['athletes'][0]['didNotPlay'] = True
            self.assertTrue(json.loads(tools.game_players(event_id='100'))['players'][0]['did_not_play'])
            data['boxscore']['players'] = []
            self.assertEqual(json.loads(tools.game_players(event_id='100'))['players'], [])

    def test_multiple_calls_and_final_summary_after_limit(self):
        calls = [call('player_advanced_stats', {'player_name': name, 'event_id': '100', 'metric': 'efg_pct'}, str(i))
                 for i, name in enumerate(['Alex Adams', 'Other Player'])]
        messages = [{'role': 'system', 'content': app.SYSTEM_PROMPT}, {'role': 'user', 'content': 'Compare'}]
        with patch.object(app, 'MAX_TOOL_ROUNDS', 1), patch.object(app, 'run_tool', return_value='{"value": 60}'), patch.object(
                app.litellm, 'completion', side_effect=[reply(calls=calls), reply('Partial comparison')]) as complete:
            answer, logs = app.run_agent(messages)
        self.assertEqual(answer, 'Partial comparison')
        self.assertEqual(len(logs), 2)
        final_args = complete.call_args.kwargs
        self.assertEqual(final_args['tool_choice'], 'none')
        self.assertEqual(len([m for m in final_args['messages'] if m['role'] == 'tool']), 2)
        self.assertEqual(messages[-1]['content'], answer)

    def test_normal_answer_does_not_need_extra_summary(self):
        messages = []
        with patch.object(app.litellm, 'completion', return_value=reply('Done')) as complete:
            self.assertEqual(app.run_agent(messages), ('Done', []))
            complete.assert_called_once()
        self.assertNotIn('daily_player_leaders', tools.TOOL_MAP)
        self.assertIn('game_players', tools.TOOL_MAP)
