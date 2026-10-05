"""Bounded retries for transient model capacity errors, without replaying tools."""
import random
import time
import litellm


def completion_with_backoff(completion, **kwargs):
    # Own the retry budget here rather than nesting LiteLLM retry loops.
    kwargs['num_retries'] = 0
    for attempt in range(4):
        try:
            return completion(**kwargs)
        except litellm.RateLimitError:
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1) + random.uniform(0, 1))


def model_error_message(error):
    if isinstance(error, litellm.RateLimitError):
        return ('The AI service is temporarily busy or rate-limited (429). '
                'Please wait a minute before trying again. If this keeps happening, '
                'check the Google Cloud project’s model quota and service availability.')
    return f'Model call failed: {type(error).__name__}: {str(error)[:300]}'
