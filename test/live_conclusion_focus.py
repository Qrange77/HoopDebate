"""Opt-in: uv run python -m test.live_conclusion_focus. Fixed data, no server restart."""
import json
import sys
from unittest.mock import patch
from backend.debate import agent
from backend.debate.tools import DebateConfig
from test.test_conclusion_focus import audit,QUESTION,SCREENSHOT,DRIFT,ANSWER,CONCESSION,CONFIG


def run(only=None):
    original = agent.complete
    def live(messages, **kwargs):
        return original(messages, timeout=35, **dict({'num_retries':0},**kwargs))
    swapped = audit()
    for card in swapped['cards']:
        card['player'],card['other_player'] = card['other_player'],card['player']
        card['value'],card['other_value'] = card['other_value'],card['value']
        card['player_id'] = CONFIG.supported_player
    swap_text = (
        "In the 2023-24 regular season, Luka averaged 33.9 points and 9.8 assists to SGA's 30.1 and 6.2; "
        "I concede that scoring-and-assist advantage. But SGA's 63.6% true shooting versus Luka's 61.7% "
        "gives me a concrete efficiency counterpoint to 'way better'. This does not establish SGA as the overall winner either.")
    swap_question = 'Luka is way better than SGA'
    swapped['conversation'] = [dict(role='user',content=swap_question)]
    swapped['argument_plan']['target_claim'] = 'SGA had higher true shooting in this sample.'
    cases = [('screenshot',SCREENSHOT,audit(),QUESTION,CONFIG,False),
             ('accurate_but_drifting',DRIFT,audit(),QUESTION,CONFIG,False),
             ('clear_counterpoint',ANSWER,audit(),QUESTION,CONFIG,True),
             ('honest_concession',CONCESSION,audit(),QUESTION,CONFIG,True),
             ('swapped_roles',swap_text,swapped,swap_question,
              DebateConfig(supported_player=CONFIG.opponent_player,opponent_player=CONFIG.supported_player),True)]
    if only:
        cases = [case for case in cases if case[0] in only]
        if not cases:
            raise ValueError('No matching case names.')
    passed = True
    with patch.object(agent,'complete',side_effect=live):
        for name,text,context,question,config,expected in cases:
            try:
                review = agent.semantic_review(agent.Draft(response=text),context,question,'debate',config,None)
                finding = review.argument_assessment
                ok = finding is not None and finding.conclusion_addresses_objection is expected
                ok &= (review.status=='pass') is expected
                result = dict(case=name,ok=ok,review=review.model_dump())
            except Exception as exc:
                result = dict(case=name,ok=False,error=type(exc).__name__)
            passed &= result['ok']
            print(json.dumps(result),flush=True)
    return passed


if __name__=='__main__':
    raise SystemExit(0 if run(sys.argv[1:]) else 1)
