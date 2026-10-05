import unittest
from unittest.mock import Mock, patch
import litellm
from backend.model_calls import completion_with_backoff, model_error_message


class ModelRetryTests(unittest.TestCase):
    def limited(self):
        return litellm.RateLimitError(message='capacity', llm_provider='vertex_ai', model='test')

    @patch('backend.model_calls.random.uniform', return_value=0)
    @patch('backend.model_calls.time.sleep')
    def test_recovers_with_same_request(self, sleep, jitter):
        request = [{'role': 'user', 'content': 'Compare players'}]
        call = Mock(side_effect=[self.limited(), self.limited(), 'ok'])
        self.assertEqual(completion_with_backoff(call, messages=request), 'ok')
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4])
        for args in call.call_args_list:
            self.assertEqual(args.kwargs, {'messages': request, 'num_retries': 0})

    @patch('backend.model_calls.time.sleep')
    def test_exhaustion_is_bounded(self, sleep):
        call = Mock(side_effect=self.limited())
        with self.assertRaises(litellm.RateLimitError):
            completion_with_backoff(call)
        self.assertEqual(call.call_count, 4)
        self.assertEqual(sleep.call_count, 3)
        self.assertIn('(429)', model_error_message(self.limited()))

    @patch('backend.model_calls.time.sleep')
    def test_other_errors_are_not_retried(self, sleep):
        call = Mock(side_effect=ValueError('invalid request'))
        with self.assertRaises(ValueError):
            completion_with_backoff(call)
        self.assertEqual(call.call_count, 1)
        sleep.assert_not_called()
