from test.test_debate import review_response
import asyncio
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from threading import Event
from unittest.mock import patch
from fastapi import Response
from fastapi.testclient import TestClient
import app
from backend.activity import ActivityLog
from backend.debate.agent import run_debate
from backend.debate.tools import DebateConfig
from test.test_debate import FakeData, response, submit


def events(text):
    return [json.loads(line[5:]) for line in text.splitlines() if line.startswith('data:')]


class ActivityTests(unittest.TestCase):
    def test_start_precedes_execution_and_error_keeps_same_id(self):
        seen=[]
        logs=ActivityLog(seen.append)
        logs.start('lookup',{'player':1})
        self.assertEqual(seen[0]['type'],'tool_start')
        self.assertEqual(len(logs),0)
        logs.append({'name':'lookup','args':{'player':1},'result':'{"error":"Unavailable"}'})
        self.assertEqual(seen[-1]['call']['id'],seen[0]['call']['id'])
        self.assertEqual(seen[-1]['call']['status'],'failed')
        logs.start('review',{})
        logs.fail_pending('Interrupted')
        self.assertEqual(logs[-1]['status'],'failed')
        self.assertEqual(logs.pending,[])

    def test_submission_audit_and_review_are_observable(self):
        seen=[]
        def model(*args,**kwargs):
            if kwargs.get('response_format',{}).get('type')=='json_schema':
                return review_response({'status': 'pass', 'issues': []})
            return submit({"response":"Which season should we discuss?","claims":[]})
        with patch('backend.debate.agent.complete',side_effect=model):
            result=run_debate([{'role':'system','content':''}], 'debate',DebateConfig(supported_player=1,opponent_player=2),
                              {'1':'Alpha','2':'Beta'},data=FakeData(),on_event=seen.append)
        started=[e['call']['name'] for e in seen if e['type']=='tool_start']
        self.assertEqual(started,['submit_argument','audit_argument','review_argument'])
        self.assertEqual(len([e for e in seen if e['type']=='tool_end']),3)
        self.assertEqual(result[2]['review_status'],'reviewed')

    def test_stream_cookie_history_and_failure_preserve_completed_work(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app,'HISTORY_DIR',Path(folder)):
            client=TestClient(app.app)
            def runner(messages,on_event):
                log=ActivityLog(on_event)
                log.start('lookup',{})
                log.append({'name':'lookup','args':{},'result':'{"value":1}'})
                log.start('next_lookup',{})
                raise RuntimeError('Provider rate limited')
            with patch.object(app,'run_agent',side_effect=runner):
                result=client.post('/chat/stream',json={'message':'hello'})
            self.assertIn('nba_browser',client.cookies)
            data=events(result.text)
            self.assertEqual(data[-1]['type'],'done')
            final=data[-1]['data']
            self.assertTrue(final['failed'])
            self.assertEqual([c['status'] for c in final['tool_calls']],['completed','failed'])
            restored=client.get('/sessions/'+final['session_id']).json()
            self.assertEqual(restored['turns'][0]['tool_calls'],final['tool_calls'])
            invalid=client.post('/chat/stream',json={'message':' '})
            self.assertEqual(events(invalid.text)[-1]['status'],400)
            other=TestClient(app.app)
            forbidden=other.post('/chat/stream',json={'message':'hello','session_id':final['session_id']})
            self.assertEqual(events(forbidden.text)[-1]['status'],404)


class LiveOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_start_arrives_while_tool_is_still_blocked(self):
        release=Event()
        finished=Event()
        def runner(messages,on_event):
            logs=ActivityLog(on_event)
            logs.start('slow_lookup',{})
            if not release.wait(3):raise RuntimeError('Test did not receive live start')
            finished.set()
            logs.append({'name':'slow_lookup','args':{},'result':'ready'})
            return 'Done',logs
        with tempfile.TemporaryDirectory() as folder, patch.object(app,'HISTORY_DIR',Path(folder)), patch.object(app,'run_agent',side_effect=runner):
            stream=app.chat_stream(app.ChatRequest(message='test'),Response(),str(uuid.uuid4()))
            collected=[]
            try:
                async for chunk in stream.body_iterator:
                    event=events(chunk)[0]
                    collected.append(event['type'])
                    if event['type']=='tool_start':
                        self.assertFalse(finished.is_set())
                        release.set()
            finally:
                release.set()
            self.assertEqual(collected[-1],'done')
            self.assertLess(collected.index('tool_start'),collected.index('tool_end'))
            self.assertTrue(finished.is_set())
