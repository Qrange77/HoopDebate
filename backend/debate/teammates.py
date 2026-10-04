"""Model-selected co-star comparisons, grounded in each focal player's actual roster."""
from backend.data.nba import DataUnavailable
from backend.debate.context import query_context
from backend.debate.schemas import CompetitiveContext

TEAMMATE_METRICS = ['GP', 'MIN', 'PTS', 'AST', 'REB', 'TOV', 'STL', 'BLK', 'TS_PCT', 'EFG_PCT', 'FG3_PCT']


def compare_key_teammates(tools, p):
    selecting = p.left_teammate_id is not None
    sides = {}
    for side in ('left', 'right'):
        request = CompetitiveContext(player_id=getattr(p, side+'_player'), season=getattr(p, side+'_season'),
            phase=p.phase, team_id=getattr(p, side+'_team_id'), teammate_id=getattr(p, side+'_teammate_id'),
            dimensions=['teammate_support'], selection_reason=p.selection_reason)
        try:
            sides[side] = query_context(tools, request, enrich=selecting, candidates_only=not selecting)
        except (DataUnavailable, ValueError) as exc:
            sides[side] = dict(status='unavailable', error=str(exc))
    if any(r.get('kind') != 'competitive_context' for r in sides.values()):
        return dict(status='partial', focus='key_teammates', **sides,
                    limitations=['Verify both team stints and teammates before drawing a supporting-cast comparison. Missing data is not a weak teammate.'])
    if not selecting:
        return dict(status='select_teammates', focus='key_teammates',
            selection_reason=p.selection_reason,
            **{side:dict(id=r['id'], player_id=r['player_id'], player_name=r['player_name'],
                         scope=r['scope'], context=r['context'], candidates=r['teammate_candidates'],
                         sources=r['sources'], limitations=r['limitations']) for side,r in sides.items()},
            instruction='NEXT: complete this comparison by selecting one representative strongest co-star per side, excluding each focal player. Explain why each nominee fits better than the main alternatives by NAME, using the SAME criteria. Do not substitute two ordinary role players when the question asks about the strongest help. Re-call compare_competitive_context with both teammate IDs and teammate_selection_reason. Candidates are discovery data only, rounded to 2 decimals: GP is games, TS_PCT is %, other metrics are per game. Fetch the selected pair for exact citable statistics before drafting. No automatic ability ranking is provided.')

    mates = {side:r['teammates'][0] for side,r in sides.items()}
    stats = tools._comparison(mates['left']['stats'], mates['right']['stats'], TEAMMATE_METRICS)
    selection = dict(
        left_focal_player=p.left_player, right_focal_player=p.right_player,
        left_focal_name=sides['left']['player_name'], right_focal_name=sides['right']['player_name'],
        left_teammate_id=p.left_teammate_id, right_teammate_id=p.right_teammate_id,
        scope_reason=p.selection_reason, teammate_reason=p.teammate_selection_reason,
        left_candidates=sides['left']['teammate_candidates'], right_candidates=sides['right']['teammate_candidates'])
    limits = list(dict.fromkeys(stats['limitations'] + [note for r in sides.values() for note in r['limitations']] + [
        'This comparison concerns two model-selected teammates in specified team-season samples; the selections are not a verified strongest-player ranking or a full supporting-cast comparison.',
        'A stronger representative teammate can qualify how much individual credit follows from team success. It cannot establish that wins are fake, that a star was carried, or what would happen after swapping teammates.',
        'Use contemporary production, efficiency and availability in the selected sample. These statistics do not establish overall teammate superiority.',
    ]))
    return tools.add({**{k:v for k,v in stats.items() if k != 'id'}, 'focus':'key_teammates',
                      'selection':selection, 'limitations':limits})
