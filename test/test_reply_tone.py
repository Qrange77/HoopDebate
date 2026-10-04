import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import app
from backend.debate.agent import prompt
from backend.debate.tools import DebateConfig
from test.test_activity import events


class ReplyToneTests(unittest.TestCase):
    def test_tones_change_voice_but_keep_roles_and_evidence_rules(self):
        config = DebateConfig(supported_player=1, opponent_player=2)
        for tone, marker in [('reasoned','REASONED'), ('roast','FULL ROAST')]:
            text = prompt('debate', config, {}, {}, tone)
            self.assertIn('Reply tone: '+marker, text)
            self.assertIn('You defend NBA player 2', text)
            self.assertIn('identical evidence, citation and uncertainty requirements', text)
            self.assertIn('overrides the tone of earlier conversation turns', text)

    def test_existing_conversation_can_switch_tone_and_restore_it(self):
        config = DebateConfig(supported_player=1, opponent_player=2).model_dump()
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'HISTORY_DIR', Path(folder)), \
             patch.object(app.NBAData, 'directory', return_value=[{'id':1,'name':'Alpha'},{'id':2,'name':'Beta'}]), \
             patch.object(app, 'run_debate', return_value=('Reply',[],None,{},{})) as model:
            client = TestClient(app.app)
            first = client.post('/chat',json=dict(message='Start',mode='debate',debate_config=config)).json()
            self.assertEqual(model.call_args.kwargs['reply_tone'],'reasoned')
            sid = first['session_id']
            streamed = client.post('/chat/stream',json=dict(message='Again',session_id=sid,reply_tone='roast'))
            self.assertEqual(events(streamed.text)[-1]['data']['failed'],False)
            self.assertEqual(model.call_args.kwargs['reply_tone'],'roast')
            record = client.get('/sessions/'+sid).json()
            self.assertEqual(record['reply_tone'],'roast')
            self.assertEqual([t['reply_tone'] for t in record['turns']],['reasoned','roast'])
            client.post('/chat',json=dict(message='Keep it',session_id=sid))
            self.assertEqual(model.call_args.kwargs['reply_tone'],'roast')
            client.post('/chat',json=dict(message='Calm now',session_id=sid,reply_tone='reasoned'))
            self.assertEqual(model.call_args.kwargs['reply_tone'],'reasoned')
            self.assertEqual(client.post('/chat',json=dict(message='Invalid',session_id=sid,reply_tone='unknown')).status_code,422)
            self.assertEqual(client.post('/chat',json=dict(message='Change sides',session_id=sid,debate_config=dict(config,supported_player=3))).status_code,409)
            path = next(Path(folder).glob('*/*.json'))
            old = json.loads(path.read_text()); old.pop('reply_tone')
            path.write_text(json.dumps(old))
            self.assertEqual(client.get('/sessions/'+sid).json()['reply_tone'],'reasoned')
