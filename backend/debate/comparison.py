"""Two-sided statistical context, independent of the writer's citation selection."""

PANEL_METRICS = {
    'GP': ('Games played', 'basic'),
    'PTS': ('Points', 'basic'),
    'AST': ('Assists', 'basic'),
    'REB': ('Rebounds', 'basic'),
    'TOV': ('Turnovers', 'basic'),
    'TS_PCT': ('True shooting', 'efficiency'),
    'EFG_PCT': ('Effective field goal shooting', 'efficiency'),
    'FG3_PCT': ('Three-point shooting', 'efficiency'),
}


def paired_request(config, record):
    """Use locked comparison seasons when applicable, otherwise the same window."""
    focal = record['player_id']
    own_year = config.supported_season if focal == config.supported_player else config.opponent_season
    other_year = config.opponent_season if focal == config.supported_player else config.supported_season
    own_scope = dict(record['scope'])
    other_scope = dict(own_scope)
    if config.scope_mode != 'auto' and own_scope['start'] == own_year and own_scope['end'] == own_year:
        other_scope.update(start=other_year, end=other_year)
    scopes = [own_scope, other_scope] if focal == config.supported_player else [other_scope, own_scope]
    return dict(left_player=config.supported_player, right_player=config.opponent_player,
                left_scope=scopes[0], right_scope=scopes[1], metrics=list(PANEL_METRICS))


def comparison_summary(result, request, names, scope_text):
    """Include missing sides and unfavorable rows instead of selecting winning metrics."""
    sides = [result.get(side, {}) for side in ('left', 'right')]
    rows = []
    metrics_to_show = dict(PANEL_METRICS)
    if result.get('focus') == 'key_teammates':
        metrics_to_show.update(MIN=('Minutes', 'basic'), STL=('Steals', 'basic'), BLK=('Blocks', 'basic'))
    for key, (label, group) in metrics_to_show.items():
        metrics = [r.get('values', {}).get(key, {}) for r in sides]
        values = [m.get('value') for m in metrics]
        comparable = all(m.get('complete') and m.get('value') is not None for m in metrics)
        relation = ('higher' if values[0] > values[1] else 'lower' if values[0] < values[1] else 'equal') if comparable else None
        rows.append(dict(metric=key, label=label, group=group, left_value=values[0], right_value=values[1],
                         unit=next((m['unit'] for m in metrics if m.get('unit')), ''), comparable=bool(comparable),
                         left_complete=bool(metrics[0].get('complete')), right_complete=bool(metrics[1].get('complete')),
                         relation=relation, difference=values[0]-values[1] if comparable else None))
    sources = [s for r in sides for s in r.get('sources', [])]
    limitations = list(dict.fromkeys([note for r in sides for note in r.get('limitations', [])]
        + [r['error'] for r in sides if r.get('error')]
        + ['Same phase and statistical basis; career samples can cover different seasons and game counts.',
           'Higher is not always better: turnovers depend on role; game counts measure sample size. No overall winner is computed.',
           'TS% and eFG% are calculated from sourced totals; these efficiency measures are not comprehensive impact ratings.']))
    summary = dict(evidence_id=result.get('id'),
                left_player=sides[0].get('player_name') or names.get(str(request['left_player']), str(request['left_player'])),
                right_player=sides[1].get('player_name') or names.get(str(request['right_player']), str(request['right_player'])),
                left_scope=scope_text(request['left_scope']), right_scope=scope_text(request['right_scope']),
                rows=rows, sources=sources, limitations=limitations)

    if result.get('focus') == 'key_teammates':
        summary.update(kind='key_teammates', selection=result['selection'])
        summary['limitations'] = list(dict.fromkeys(summary['limitations'] + result['limitations']))
        for side,record in zip(('left','right'),sides):
            summary[side+'_scope'] += ' · ' + record['context']['team_name']
    return summary
