"""Opt-in two-turn real-model exercise using deterministic, synthetic NBA fixtures.

Run: uv run python -m test.live_evidence_rotation
No app server or session files are started or modified. Fixture statistics are
not claims about actual player performance.
"""
import json
from functools import partial
from unittest.mock import patch

from backend.debate import agent
from backend.debate.tools import DebateConfig
from test.test_debate import FakeData, row


def run():
    data = FakeData()
    data.career = {
        1:[row('2023-24', GP=10, PTS=300, AST=60, REB=50, STL=20, BLK=10, MIN=350)],
        2:[row('2023-24', GP=10, PTS=340, AST=100, REB=90, STL=10, BLK=5, MIN=360)],
    }
    config = DebateConfig(supported_player=1, opponent_player=2,
                          supported_season=2023, opponent_season=2023)
    messages = [{'role':'system', 'content':''}]
    evidence, state, history = {}, {}, []
    questions = [
        'Alpha is way better than Beta. Compare only their scoring and assists in the 2023-24 regular season.',
        'Alpha is still way better than Beta. What about their steals and blocks in that same regular season?',
    ]
    complete = partial(agent.complete, timeout=35, num_retries=0)
    passed = True
    with patch.object(agent, 'complete', side_effect=complete):
        for index, question in enumerate(questions):
            messages.append({'role':'user', 'content':question})
            prior_used = set(state.get('used_fact_keys', []))
            try:
                text, logs, result, evidence, state = agent.run_debate(
                    messages, 'debate', config, {'1':'Alpha','2':'Beta'}, evidence=evidence,
                    state=state, data=data, history_turns=history, max_rounds=12)
            except Exception as exc:
                print(json.dumps(dict(turn=index+1, ok=False, error=type(exc).__name__)), flush=True)
                return False
            claims = result['claims']
            metrics = {c['metric'] for c in claims}
            wanted = {'PTS','AST'} if index == 0 else {'STL','BLK'}
            ok = (result['review_status'] == 'reviewed' and bool(metrics & wanted)
                  and (index == 0 or not metrics & {'PTS','AST'})
                  and result['validation']['novelty_repairs'] == 0)
            passed = passed and ok
            print(json.dumps(dict(turn=index+1, ok=ok, response=text,
                metrics=sorted(metrics), new_fact_count=len(set(state['used_fact_keys'])-prior_used),
                validation=result['validation'], published_arguments=state['published_arguments'])), flush=True)
            history.append(dict(message=question, response=text, debate_result=result, failed=False))
    return passed


if __name__ == '__main__':
    raise SystemExit(0 if run() else 1)
