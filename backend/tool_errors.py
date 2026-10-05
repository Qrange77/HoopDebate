"""Small, model-facing errors; tracebacks belong only in server logs."""
import json
import math

import requests


def tool_error(code, message, next_action, *, status='unavailable', **details):
    return {'status': status, 'code': code, 'error': message,
            'next_action': next_action, **details}


def parse_tool_arguments(raw):
    """Reject non-object JSON and non-finite numbers before tool dispatch."""
    def invalid_constant(value):
        raise ValueError('Non-finite JSON number.')

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('Non-finite JSON number.')
        return result

    try:
        args = json.loads(raw, parse_constant=invalid_constant, parse_float=finite_float)
    except (ValueError, TypeError):
        raise ValueError('Arguments must be a valid JSON object with double-quoted keys and finite numbers.') from None
    if not isinstance(args, dict):
        raise ValueError('Arguments must be a JSON object, not an array, scalar or null.')
    return args


def validation_error(exc, prefix='Tool argument validation failed', *, status='revise'):
    # Do not echo raw input, Pydantic URLs or exception context to the model.
    fields = [{'field': '.'.join(map(str, e['loc'])) or '$', 'message': e['msg']}
              for e in exc.errors(include_url=False, include_context=False, include_input=False)]
    return tool_error('invalid_arguments', prefix + '.',
                      'Correct the listed fields using the tool parameter schema, then call the tool again.',
                      status=status, fields=fields)


def request_error(exc, source):
    if isinstance(exc, requests.Timeout):
        return tool_error('timeout', f'{source} did not respond before the timeout.',
                          'Retry at most once; if it fails again, report unavailable data rather than guessing.')
    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else None
        if code in (401, 403):
            action = 'The data source denied access. Report unavailable data; changing player or game arguments will not fix access.'
        elif code in (400, 404, 422):
            action = 'Verify the identifiers and date/season with the lookup tools before retrying. Do not invent identifiers.'
        elif code == 429:
            action = 'The data source is rate-limiting requests. Stop immediate retries and report the temporary limitation.'
        elif code is not None and code >= 500:
            action = 'Retry at most once; if the source still fails, report the temporary outage.'
        else:
            action = 'Report the data source failure; do not infer a statistic from this error.'
        return tool_error('http_error', f'{source} returned HTTP {code or "error"}.', action,
                          http_status=code)
    return tool_error('network_error', f'Could not connect to {source}.',
                      'Retry at most once; if it fails again, report unavailable data.')
