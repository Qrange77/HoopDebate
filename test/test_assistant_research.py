"""Shared lookups are callable in Assistant, without exposing Debate actions."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import app
from backend.assistant.research_tools import AssistantResearchTools, TOOLS, SHARED_NAMES
from backend.debate.tools import DebateTools, DebateConfig
from test.test_debate import FakeData, row, tool_call, response


class AssistantResearchTests(unittest.TestCase):
    def test_permissions_and_player_changes(self):
        data = FakeData()
        data.career[3] = [row(PTS=300)]
        tools = AssistantResearchTools(data=data)
        for player in (1, 2, 3):
            result = tools.run('query_evidence', {'player_id': player})
            self.assertNotIn('error', result)
            self.assertEqual(result['player_id'], player)
        comparison = tools.run('compare_players', {'left_player': 1, 'right_player': 3})
        self.assertEqual(comparison['kind'], 'comparison')
        self.assertNotIn('error', tools.run('player_awards', {'player_id': 3}))
        debate = DebateTools(DebateConfig(supported_player=1, opponent_player=2), data=data)
        self.assertIn('error', debate.run('query_evidence', {'player_id': 3}))
        exposed = {t['function']['name'] for t in TOOLS}
        self.assertTrue(SHARED_NAMES <= exposed)
        for name in ('audit_argument', 'plan_argument', 'submit_argument',
                     'plan_team_context_research', 'research_team_context'):
            self.assertNotIn(name, exposed)
            self.assertIn('error', json.loads(tools.dispatch(name, {})))

    def test_validation_and_isolation(self):
        a, b = AssistantResearchTools(data=FakeData()), AssistantResearchTools(data=FakeData())
        self.assertIn('error', a.run('query_evidence', {'player_id': -1}))
        self.assertIn('error', a.run('compare_players', {'left_player': 1, 'right_player': 1}))
        a.run('query_evidence', {'player_id': 1})
        self.assertTrue(a.evidence)
        self.assertFalse(b.evidence)

    def test_assistant_loop_dispatch_and_activity(self):
        calls = [tool_call('compare_players', {'left_player': 1, 'right_player': 2}), response('Compared.')]
        def completion(**kwargs):
            self.assertIn('compare_players', {t['function']['name'] for t in kwargs['tools']})
            return SimpleNamespace(choices=[SimpleNamespace(message=calls.pop(0))])
        with patch.object(app, 'AssistantResearchTools', return_value=AssistantResearchTools(data=FakeData())), \
             patch.object(app.litellm, 'completion', side_effect=completion):
            messages = [{'role': 'user', 'content': 'Compare these players.'}]
            answer, activity = app.run_agent(messages)
        self.assertEqual(answer, 'Compared.')
        self.assertEqual(activity[0]['name'], 'compare_players')
        self.assertEqual(json.loads(activity[0]['result'])['kind'], 'comparison')
        self.assertEqual(messages[-2]['role'], 'tool')
