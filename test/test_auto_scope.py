from test.test_debate import review_response
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import app
from backend.debate.agent import prompt, run_debate
from backend.debate.tools import DebateConfig
from test.test_debate import FakeData, tool_call, submit, response


class AutoScopeTests(unittest.TestCase):
    def test_prompt_locks_roles_without_presenting_default_phase_as_user_choice(self):
        config = DebateConfig(supported_player=1, opponent_player=2, scope_mode='auto')
        text = prompt('debate', config, {'1':'Alpha','2':'Beta'}, {})
        self.assertIn('You defend NBA player 2', text)
        self.assertIn('Do not ask the user to choose a scope before starting', text)
        settings = text.split('Role and scope settings: ')[1].split('. Names:')[0]
        self.assertNotIn('phase', json.loads(settings))
        self.assertNotIn('supported_season', json.loads(settings))

    def test_auto_scope_allows_model_selected_playoff_comparison(self):
        config = DebateConfig(supported_player=1, opponent_player=2, scope_mode='auto')
        scope = dict(start=1995, end=1995, phase='Playoffs', basis='per_game')
        calls = [tool_call('compare_players',dict(left_player=1,right_player=2,left_scope=scope,right_scope=scope)),
                 submit(dict(response='This playoff sample is limited to the selected season.')),
                 review_response({'status': 'pass', 'issues': []})]
        with patch('backend.debate.agent.complete',side_effect=calls):
            result=run_debate([dict(role='system',content=''),dict(role='user',content='Make your case.')],
                'debate',config,{'1':'Alpha','2':'Beta'},data=FakeData())
        self.assertIn('Playoffs',result[2]['comparisons'][0]['left_scope'])
        self.assertIn('1995-96',result[2]['comparisons'][0]['right_scope'])

    def test_old_saved_config_can_continue_without_scope_mode_field(self):
        config=DebateConfig(supported_player=1,opponent_player=2).model_dump()
        with tempfile.TemporaryDirectory() as folder, patch.object(app,'HISTORY_DIR',Path(folder)), patch.object(app.NBAData,'directory',return_value=[dict(id=1,name='Alpha'),dict(id=2,name='Beta')]), patch.object(app,'run_debate',return_value=('Reply',[],None,{},{})):
            client=TestClient(app.app)
            first=client.post('/chat',json=dict(message='Hello',mode='debate',debate_config=config)).json()
            path=next(Path(folder).glob('*/*.json'))
            record=json.loads(path.read_text());record['debate_config'].pop('scope_mode')
            path.write_text(json.dumps(record))
            second=client.post('/chat',json=dict(message='Continue',session_id=first['session_id'],mode='debate',debate_config=config))
            self.assertEqual(second.status_code,200)
