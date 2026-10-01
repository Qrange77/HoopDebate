"""Session isolation and local persistence, without model or network calls."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import app


class SessionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = patch.object(app, 'HISTORY_DIR', Path(folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app.app)
        self.client.get('/sessions')
        self.contexts = []
        def agent(messages):
            self.contexts.append([m['content'] for m in messages if m['role'] == 'user'])
            messages.append({'role': 'assistant', 'content': 'Answer'})
            return 'Answer', [{'name': 'find_games', 'args': {}, 'result': '{}'}]
        patcher = patch.object(app, 'run_agent', side_effect=agent)
        self.agent = patcher.start()
        self.addCleanup(patcher.stop)

    def send(self, message, session_id=None):
        response = self.client.post('/chat', json={'message': message, 'session_id': session_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['session_id']

    def test_new_chats_and_restored_context(self):
        first = self.send('Magic')
        second = self.send('Lakers')
        self.assertNotEqual(first, second)
        self.assertEqual(self.contexts, [['Magic'], ['Lakers']])
        # A fresh client with the same browser cookie restores disk-backed history.
        self.client = TestClient(app.app, cookies=dict(self.client.cookies))
        self.send('Their score?', first)
        self.assertEqual(self.contexts[-1], ['Magic', 'Their score?'])
        record = self.client.get('/sessions/' + first).json()
        self.assertEqual(len(record['turns']), 2)
        self.assertEqual(record['turns'][0]['tool_calls'][0]['name'], 'find_games')
        self.assertEqual(len(list(app.HISTORY_DIR.glob('*/*.json'))), 2)
        self.assertEqual(len(self.client.get('/sessions').json()), 2)

    def test_browser_isolation_and_clear(self):
        first = self.send('Magic')
        other = TestClient(app.app)
        self.assertEqual(other.get('/sessions').json(), [])
        self.assertEqual(other.get('/sessions/' + first).status_code, 404)
        self.assertEqual(other.post('/chat', json={'message': 'hi', 'session_id': first}).status_code, 404)
        other.post('/clear', params={'session_id': first})
        self.assertEqual(self.client.get('/sessions/' + first).status_code, 200)
        second = self.send('Lakers')
        self.client.post('/clear', params={'session_id': first})
        self.assertEqual(self.client.get('/sessions/' + first).status_code, 404)
        self.assertEqual(self.client.get('/sessions/' + second).status_code, 200)

    def test_errors_do_not_pollute_context(self):
        first = self.send('Magic')
        def fail(messages):
            messages.append({'role': 'assistant', 'tool_calls': [{'id': 'unfinished'}]})
            raise RuntimeError('offline')
        self.agent.side_effect = fail
        self.send('Failed question', first)
        owner = self.client.cookies['nba_browser']
        record = app.read_history(owner, first)
        self.assertEqual(len(record['messages']), 3)
        self.assertIn('Model call failed', record['turns'][-1]['response'])
        self.assertEqual(self.client.post('/chat', json={'message': ' '}).status_code, 400)
        self.assertEqual(self.client.post('/chat', json={'message': 'hi', 'session_id': '../oops'}).status_code, 400)
