"""Opt-in fixed-record checks: uv run python -m test.live_team_credit_obligation.
No server restart, session mutation, or live NBA data requests.
"""
import json
from unittest.mock import patch
from backend.debate import agent
from backend.debate.support import SupportResearch
from backend.debate.tools import DebateTools
from test.test_evidence_direction import CONFIG, PLAN
from test.test_team_credit_obligation import QUESTION, CONVERSATION, SCREENSHOT


def run():
    original = agent.complete
    def live(messages,**kwargs):
        return original(messages,timeout=35,**dict({'num_retries':0},**kwargs))
    context = DebateTools(CONFIG)
    context.mode,context.conversation = 'debate',CONVERSATION
    plan = dict(PLAN,target_claim='A championship establishes SGA as individually better than Luka.',
        objection='The championship is offered as a response to the player comparison.',
        approach='Acknowledge the title and say championships are collective.',research_tools=[])
    cases = [(QUESTION,'two_pairs'),
        ('Just tell me how many championships SGA has. I only want the count, not a player comparison.','none'),
        ('Compare only the strongest helper each player had in the 2024 playoffs.','strongest_pair')]
    passed = True
    with patch.object(agent,'complete',side_effect=live):
        for question,expected in cases:
            context.conversation = [*CONVERSATION[:-1],dict(role='user',content=question)]
            try:
                review = agent.review_plan(plan,question,context)
                ok = review.support_scope==expected
                if expected=='two_pairs':
                    ok &= review.team_success_argument and review.team_context_intent=='individual_credit'
                result = dict(case='planning',question=question,ok=ok,review=review.model_dump())
            except Exception as exc:
                result = dict(case='planning',question=question,ok=False,error=type(exc).__name__)
            passed &= result['ok']
            print(json.dumps(result),flush=True)
        fixed_card = dict(evidence_id='ev_fixed_title',title='NBA Champion',player='Shai Gilgeous-Alexander',
            player_id=CONFIG.supported_player,value=1,unit='verified award records',scope='career',covered_seasons=['2024-25'])
        unavailable = SupportResearch(CONFIG)
        unavailable.register('two_pairs',[])
        unavailable.expire()
        limited = ('The supplied award record credits SGA with one NBA championship. Both planned teammate comparisons '
            'were unavailable within the research budget, so I cannot establish who had stronger support or use this evidence to settle overall individual superiority.')
        for name,text,tasks,expected in [('screenshot_missed_plan',SCREENSHOT,{},'needs_evidence'),
                ('unavailable_honest_limit',limited,unavailable.snapshot(),'pass')]:
            audit = dict(cards=[fixed_card],conversation=CONVERSATION,support_research=tasks,
                argument_plan=dict(plan,team_context_intent='count_only',support_scope='none',research_tools=[]))
            try:
                review = agent.semantic_review(agent.Draft(response=text),audit,QUESTION,'debate',CONFIG,None)
                finding = review.argument_assessment
                ok = review.status==expected and finding is not None and finding.required_support_scope=='two_pairs'
                result = dict(case=name,ok=ok,review=review.model_dump())
            except Exception as exc:
                result = dict(case=name,ok=False,error=type(exc).__name__)
            passed &= result['ok']
            print(json.dumps(result),flush=True)
    return passed


if __name__=='__main__':
    raise SystemExit(0 if run() else 1)
