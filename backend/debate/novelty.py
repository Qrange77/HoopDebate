"""Published fact usage and argument history, independent of retrieval snapshots."""
from backend.data.nba import stable_id
from backend.debate.tools import Audit, Claim

USAGE_VERSION = 2


def fact_keys(record, metric):
    """Both sides of a comparison consume the same atomic facts as single citations."""
    if record.get('kind') == 'comparison':
        return fact_keys(record['left'], metric) | fact_keys(record['right'], metric)
    scope = {k:v for k,v in record.get('scope', {}).items() if k != 'basis'}
    request, context = record.get('request', {}), record.get('context', {})
    identity = dict(player_id=record.get('player_id'), metric=metric, scope=scope)
    # Names, values, source timestamps and sampling explanations are not identity.
    for key in ('team_id', 'opponent_id'):
        value = record.get(key, context.get(key, request.get(key)))
        if value is not None:
            identity[key] = value
    if record.get('kind') == 'game':
        identity['game_id'] = record.get('nba_game_id') or record.get('game_id') or record.get('date')
    # Ranking eligibility changes a rank, not the player's underlying PTS/AST.
    if 'RANK' in metric:
        identity['min_games'] = request.get('min_games', record.get('min_games'))
    if request.get('dimension') == 'opponent_splits':
        identity['opponent_sample'] = {k:request[k] for k in ('top_n',) if k in request}
    return {stable_id(identity)}


def used_fact(context, record, metric):
    return bool(fact_keys(record, metric) & set(getattr(context, 'used_fact_keys', [])))


def duplicate_claims(context, claims):
    duplicates = []
    for claim in claims:
        record = context.evidence.get(claim.evidence_id)
        if record and used_fact(context, record, claim.metric):
            duplicates.append(dict(evidence_id=claim.evidence_id, metric=claim.metric,
                                   player_id=claim.player_id, scope=claim.scope.model_dump()))
    return duplicates


def publish_usage(state, evidence, response, claims, plan=None):
    keys = set()
    for claim in claims:
        keys.update(fact_keys(evidence[claim.evidence_id], claim.metric))
    state['used_fact_keys'] = sorted(set(state.get('used_fact_keys', [])) | keys)
    plan = plan or {}
    argument = dict(response=response, target_claim=plan.get('target_claim', ''),
                    discussion_dimension=plan.get('discussion_dimension', ''),
                    facts=[dict(metric=c.metric, player_id=c.player_id, scope=c.scope.model_dump()) for c in claims],
                    fact_keys=sorted(keys))
    argument['id'] = stable_id(dict(response=response, fact_keys=sorted(keys)))
    published = state.setdefault('published_arguments', [])
    if not any(a['id'] == argument['id'] for a in published):
        published.append(argument)


def migrate_usage(state, context, turns, prior_replies, canonical):
    """Rebuild only from final published citations; the caller owns rollback/save."""
    if state.get('evidence_usage_version') == USAGE_VERSION and not state.get('usage_migration_incomplete'):
        return
    state.setdefault('used_fact_keys', [])
    state.setdefault('published_arguments', [])
    incomplete = bool(state.get('used_evidence_keys') or prior_replies) and not turns
    if turns is None:
        incomplete = bool(state.get('used_evidence_keys') or prior_replies)
    else:
        for turn in turns:
            if turn.get('failed') or not turn.get('response'):
                continue
            result = turn.get('debate_result') or {}
            claims, cards = result.get('claims', []), result.get('cards', [])
            reviewed = result.get('review_status') == 'reviewed'
            shown_cards = []
            for card in cards:
                try:
                    if reviewed or canonical(card) in turn['response']:
                        shown_cards.append(card)
                except (ValueError, TypeError, KeyError):
                    incomplete = True
            recovered = []
            for raw in claims:
                try:
                    claim = Claim.model_validate(raw)
                    if not reviewed and not any(c.get('evidence_id') == claim.evidence_id
                            and c.get('title') == claim.metric and c.get('player_id') == claim.player_id for c in shown_cards):
                        continue
                    if not context.audit_argument(Audit(claims=[claim]))['valid']:
                        incomplete = True
                        continue
                    recovered.append(claim)
                except (ValueError, TypeError, KeyError):
                    incomplete = True
            # Keep prose even if the old records cannot reconstruct exact facts.
            publish_usage(state, context.evidence, turn['response'], recovered, result.get('argument_plan'))
            if (reviewed and len(recovered) != len(claims)) or (shown_cards and not recovered):
                incomplete = True
            if not result:
                incomplete = True
    state['evidence_usage_version'] = USAGE_VERSION
    state['usage_migration_incomplete'] = incomplete
    state.pop('used_evidence_keys', None)
