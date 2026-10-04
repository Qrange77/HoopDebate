"""Opt-in real-model checks with synthetic fixtures; no app server or sessions changed.
Run: uv run python -m test.live_support_roast
"""
import json
from unittest.mock import patch
from backend.debate import agent
from backend.activity import ActivityLog
from backend.debate.tools import DebateTools
from test.test_support_research import fixture
from test.test_debate import response


def run():
    original = agent.complete
    def live(messages, **kwargs):
        return original(messages, timeout=35, **dict({'num_retries':0}, **kwargs))
    passed = True
    data, config, args, pairs, plan = fixture()
    context = DebateTools(config, data=data)
    context.mode, context.conversation = 'debate', []
    with patch.object(agent, 'complete', side_effect=live):
        for question, expected in [
            ('How many championships does Alpha have? Just the count.', 'none'),
            ('Alpha has more championships than Beta, so Alpha is the better player.', 'two_pairs'),
            ('Compare only the single strongest teammate each player had.', 'strongest_pair'),
        ]:
            proposed = dict(plan, objection=question, approach='Choose research appropriate to this question.',
                            target_claim='Determine what the requested evidence establishes.')
            context.conversation = [{'role':'user','content':question}]
            review = agent.review_plan(proposed, question, context)
            ok = review.support_scope == expected
            passed &= ok
            print(json.dumps(dict(case='classification', question=question, ok=ok, review=review.model_dump())), flush=True)
        question = 'Alpha has more championships, so Alpha is clearly better than Beta. Consider the 1995-96 playoffs supporting context.'
        text, logs, result, evidence, state = agent.run_debate(
            [{'role':'system','content':''},{'role':'user','content':question}],
            'debate', config, {'1':'Alpha','2':'Beta'}, data=data, max_rounds=20)
        research = result['support_research']
        ok = result['review_status']=='reviewed' and research['required_pairs']==2 and all(t['status']=='completed' for t in research['tasks'])
        pair_comparisons = [c for c in result['comparisons'] if c.get('kind')=='key_teammates']
        ok = ok and len(pair_comparisons)==2
        ok = ok and {t['evidence_id'] for t in research['tasks']} <= {c['evidence_id'] for c in pair_comparisons}
        passed &= ok
        print(json.dumps(dict(case='two_pairs', ok=ok, response=text, research=research, comparisons=pair_comparisons,
            validation=result['validation'], calls=[dict(name=c['name'],args=c['args'],status=c['status'],result=c.get('result')) for c in logs])), flush=True)

    # Inject the known bad first rewrite, then use the REAL reviewer and repair writer.
    draft = agent.Draft(response='In this selected postseason sample, Alpha played 10 games and Beta played 8. More appearances alone do not establish which individual performed better.')
    audit = dict(cards=[dict(evidence_id='ev_fixed_games', title='GP', player='Alpha', value=10,
        other_player='Beta', other_value=8, unit='games', scope='1995-96 Playoffs', other_scope='1995-96 Playoffs')],
        conversation=[dict(role='user',content='Alpha played more playoff games.')],
        approved_assessment=dict(response_strategy='qualified_answer', evidence_direction='inconclusive'))
    injected = False
    def with_bad_first(messages, **kwargs):
        nonlocal injected
        if kwargs.get('response_format',{}).get('json_schema',{}).get('name')=='styled_reply' and not injected:
            injected = True
            return response(json.dumps({'response':'Alpha played 10 games to Beta’s 8. Sitting on the bench or just showing up more often is not the same as running the show.'}))
        return live(messages, **kwargs)
    logs = ActivityLog()
    with patch.object(agent,'complete',side_effect=with_bad_first):
        final, status = agent.style_approved_reply(draft,audit,'Alpha played more playoff games.','debate',config,None,logs)
    attempts = audit['style_attempts']
    ok = 2 <= len(attempts) <= agent.MAX_STYLE_ATTEMPTS and attempts[0]['status'] in ('revise','needs_evidence') and all(a['status'] in ('pass','revise','needs_evidence') for a in attempts[1:])
    ok &= status=='applied' or final.response==draft.response
    passed &= ok
    print(json.dumps(dict(case='roast_repair',ok=ok,response=final.response,style_status=status,style_attempts=attempts)),flush=True)
    return passed


if __name__=='__main__':
    raise SystemExit(0 if run() else 1)
