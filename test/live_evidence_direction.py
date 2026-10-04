"""Opt-in real-model checks with fixed reviewer evidence; starts no app server.

Run: uv run python -m test.live_evidence_direction
The numbers are fixtures, not a verification of historical NBA statistics.
"""
import json
from functools import partial
from unittest.mock import patch

from backend.debate import agent
from backend.activity import ActivityLog
from backend.debate.tools import DebateConfig
from test.test_evidence_direction import (
    CONFIG, LUKA, SHAI, CARD, CONCESSION, BAD_REPLY, fixture_audit,
)


def run():
    cases = []
    screenshot = ("Bro really typed all that just to reduce team success to one guy while ignoring the actual talent sharing the floor. "
        "In the 2024 postseason, Shai had Jalen Williams putting up 18.7 points per game, while Luka ran right alongside Kyrie Irving adding 22.1. "
        "So that's your whole case? Evaluating players purely by win milestones while completely dodging the high-level co-stars right next to them is wild. Get better material.")
    cases.append(('screenshot_strawman', screenshot, fixture_audit(), CONFIG, False, None))
    cases.append(('opposing_evidence_as_victory', BAD_REPLY, fixture_audit(), CONFIG, False, None))
    cases.append(('honest_concession', CONCESSION, fixture_audit(), CONFIG, True, 'opposes'))
    reversed_audit = fixture_audit()
    card = reversed_audit['cards'][0]
    for key in ('player', 'value', 'scope', 'context'):
        card[key], card['other_'+key] = card['other_'+key], card[key]
    card['player_id'] = 1631114
    cases.append(('reversed_comparison', CONCESSION, reversed_audit, CONFIG, True, 'opposes'))
    other = fixture_audit()
    other['argument_plan']['target_claim'] = 'Shai received less scoring support from his selected co-star than Luka in the 2024 postseason.'
    text = ('For selected co-star scoring in the 2024 postseason, Shai had less support: Jalen Williams averaged 18.7 points per game versus Kyrie Irving at 22.1. '
            'That is one limited supporting-context point in Shai’s favor; it does not establish which entire roster was stronger or prove Shai better overall.')
    cases.append(('swapped_defended_player', text, other,
                  DebateConfig(supported_player=LUKA, opponent_player=SHAI), True, 'supports'))
    mixed = fixture_audit()
    mixed['cards'].append(dict(CARD, title='AST', value=4.4, other_value=5.1))
    mixed['argument_plan']['target_claim'] = 'Luka had a stronger overall supporting roster in the 2024 postseason.'
    text = ('In this selected 2024 postseason comparison, Kyrie scored more than Jalen Williams, 22.1 versus 18.7 points per game, '
            'but had fewer assists, 4.4 versus 5.1. This mixed two-co-star sample does not establish which overall supporting roster was stronger.')
    cases.append(('mixed_dimensions', text, mixed, CONFIG, True, 'inconclusive'))
    actual = fixture_audit()
    actual['conversation'][0]['content'] = 'SGA is better solely because his team wins more.'
    text = ('You are treating team wins as sufficient proof of individual superiority. In this selected 2024 postseason comparison, '
            'Kyrie scored 22.1 per game to Jalen Williams’s 18.7, so I cannot argue Luka had less co-star scoring support. '
            'That local concession does not make wins alone sufficient to settle which player is better or establish overall roster strength.')
    cases.append(('actual_user_win_claim', text, actual, CONFIG, True, 'opposes'))

    outputs, approved = [], None
    # Bound provider calls while exercising the actual prompts, schema and guards.
    live_complete = partial(agent.complete, timeout=35, num_retries=0)
    with patch.object(agent, 'complete', side_effect=live_complete):
        for name, text, audit, config, should_pass, direction in cases:
            try:
                review = agent.semantic_review(agent.Draft(response=text), audit,
                    'What about teammates?', 'debate', config, None)
                data = review.model_dump()
                ok = (review.status == 'pass') == should_pass
                if direction:
                    ok = ok and review.argument_assessment is not None and review.argument_assessment.evidence_direction == direction
                outputs.append(dict(case=name, ok=ok, review=data))
                if name == 'honest_concession' and review.status == 'pass':
                    approved = dict(audit, approved_assessment=data['argument_assessment'])
                print(json.dumps(outputs[-1]), flush=True)
            except Exception as exc:
                outputs.append(dict(case=name, ok=False, error=type(exc).__name__))
                print(json.dumps(outputs[-1]), flush=True)
        if approved:
            logs = ActivityLog()
            draft, status = agent.style_approved_reply(agent.Draft(response=CONCESSION), approved,
                'What about teammates?', 'debate', CONFIG, None, logs)
            entry = next((x for x in reversed(logs) if x['name']=='review_style' and x.get('result')), None)
            # A provider failure fallback is safe, but is not a completed live validation.
            checked_review = json.loads(entry['result']) if entry else {}
            checked = checked_review.get('status') in ('pass', 'revise', 'needs_evidence')
            if status == 'applied':
                findings = checked_review.get('argument_assessment') or {}
                checked = checked and findings.get('conclusion_supported') is True and findings.get('objection_faithful') is True
                checked = checked and findings.get('evidence_direction') == 'opposes'
            else:
                checked = checked and draft.response == CONCESSION
            outputs.append(dict(case='roast_preserves_concession', ok=checked,
                style_status=status, response=draft.response, review=entry.get('result') if entry else None))
            print(json.dumps(outputs[-1]), flush=True)
    return all(x['ok'] for x in outputs) and len(outputs) == 8


if __name__ == '__main__':
    raise SystemExit(0 if run() else 1)
