"""Sourced single-season team support and player role; no causal strength score."""
from urllib.parse import urlencode
from backend.data.nba import DataUnavailable, aggregate, season_rows, season_label, number, provenance

ROLE_METRICS = ['PTS_SHARE', 'AST_SHARE', 'FGA_SHARE', 'PTS_TEAM_RANK', 'AST_TEAM_RANK', 'FGA_TEAM_RANK', 'USG_PCT']
SUPPORT_METRICS = ['TEAMMATE_PTS_PER_TEAM_GAME']
LIMITS = [
    'Team-season records do not establish minutes or games played together. No claim about shared-court support or injury causes is supported.',
    'Team shares use the full selected team-season totals, including games the player missed. Ranks use season totals, not ability rankings.',
    'Teammate production and usage describe roles and support; they do not prove who caused wins, carried the team, or would win after a hypothetical swap.',
]


def source(entry):
    result = provenance(entry)
    params = entry['parameters']
    query = {'TeamID':params['team_id'], 'Season':params['season'],
             'SeasonType':params['season_type_all_star'], 'MeasureType':params['measure_type_detailed_defense'], 'PerMode':'Totals'}
    result['url'] = entry['source_url'] + '?' + urlencode(query)
    return result


def query_context(tools, p, enrich=True, candidates_only=False):
    tools.allowed(p.player_id)
    saved = next((r for r in reversed(list(tools.evidence.values()))
                  if r.get('kind') == 'competitive_context' and {k:v for k,v in r.get('request',{}).items() if k != 'selection_reason'} == p.model_dump(exclude={'selection_reason'})), None)
    if saved and (not candidates_only or 'teammate_candidates' in saved) and (not enrich or saved.get('enriched', True)):
        return saved
    career = tools.data.fetch('career', player_id=p.player_id, per_mode36='Totals', league_id_nullable='00')
    dataset = 'SeasonTotalsRegularSeason' if p.phase == 'Regular Season' else 'SeasonTotalsPostSeason'
    teams = {int(r['TEAM_ID']): r.get('TEAM_ABBREVIATION', str(r['TEAM_ID']))
             for r in career['data'].get(dataset, [])
             if str(r.get('SEASON_ID')) == season_label(p.season) and number(r.get('TEAM_ID'))
             and number(r.get('GP')) and r['TEAM_ID'] != 0}
    if not teams:
        raise DataUnavailable('No verified team stint for this player, season and phase.')
    if p.team_id is None and len(teams) != 1:
        return {'status':'needs_team', 'teams':[{'team_id':i,'name':n} for i,n in teams.items()],
                'message':'This player played for multiple teams in this season. Select a team; do not merge stints.'}
    team_id = p.team_id or next(iter(teams))
    if team_id not in teams:
        raise ValueError('The requested team is not a verified stint for this player, season and phase.')
    params = dict(team_id=team_id, season=season_label(p.season), season_type_all_star=p.phase,
                  per_mode_detailed='Totals', measure_type_detailed_defense='Base')
    entry = tools.data.fetch('team_players', **params)
    if not isinstance(entry, dict) or not isinstance(entry.get('data'), dict):
        raise DataUnavailable('Team player dataset unavailable.')
    overall = entry['data'].get('TeamOverall', [])
    rows = entry['data'].get('PlayersSeasonTotals', [])
    if len(overall) != 1 or overall[0].get('TEAM_ID') != team_id:
        raise DataUnavailable('A matching team total could not be verified.')
    team = overall[0]
    roster = {}
    for r in rows:
        ident = r.get('PLAYER_ID')
        if not ident or number(r.get('GP')) is None or number(r.get('GP')) <= 0:
            continue
        if ident in roster and roster[ident] != r:
            raise DataUnavailable('Conflicting duplicate player rows in team dataset.')
        roster[ident] = r
    if p.player_id not in roster or not number(team.get('GP')):
        raise DataUnavailable('Player participation or team sample is unavailable.')
    player = roster[p.player_id]
    scope = dict(start=p.season, end=p.season, phase=p.phase, basis='per_game')
    team_name = team.get('TEAM_NAME') or teams[team_id]
    metadata = dict(team_id=team_id, team_name=team_name, focal_player_id=p.player_id)
    sources = [source(entry), provenance(career, p.player_id)]
    limits = list(LIMITS)
    if len(teams) > 1:
        limits.append('Traded season: only this team stint is included. Teammate totals cover the whole team-season, not just the overlap with the focal player.')

    def stat_record(r):
        normalized = season_rows([dict(r, TEAM_ID=team_id, SEASON_ID=season_label(p.season))], p.season, p.season)
        all_values = aggregate(normalized)
        values = {k:all_values[k] for k in ['GP','MIN','PTS','REB','AST','FGA','TOV','STL','BLK','TS_PCT','EFG_PCT','FG3_PCT']}
        return dict(kind='team_player', player_id=r['PLAYER_ID'], player_name=r['PLAYER_NAME'],
                    scope=scope, context=metadata, values=values, sources=list(sources), limitations=list(limits))

    record = stat_record(player)
    record.update(kind='competitive_context', request=p.model_dump(exclude={'selection_reason'}), teammates=[], team_games=team['GP'])
    # Full roster candidates, sorted by ID rather than an invented ability score.
    record['teammate_candidates'] = [
        dict(player_id=r['PLAYER_ID'], player_name=r['PLAYER_NAME'],
             metrics={key:round(m['value'], 2) if m['value'] is not None else None
                      for key,m in stat_record(r)['values'].items()
                      if key in ('GP','MIN','PTS','REB','AST','STL','BLK','TS_PCT')})
        for ident,r in sorted(roster.items()) if ident != p.player_id
    ]
    if candidates_only:
        record['enriched'] = False
        limits.append('All non-focal roster candidates are listed by player ID, not ability. The model must explain its representative teammate selections; no automatic strongest-player verdict is provided.')
        return tools.add(record)
    values = record['values']
    def metric(value, label, unit, formula, inputs, games=player['GP']):
        return dict(value=value, label=label, unit=unit, formula=formula, inputs=inputs, games=games,
                    covered_seasons=[season_label(p.season)] if value is not None else [], complete=value is not None)

    if 'individual_role' in p.dimensions:
        for key, label in [('PTS','scoring'),('AST','assists'),('FGA','shot attempts')]:
            numerator, denominator = number(player.get(key)), number(team.get(key))
            valid = numerator is not None and denominator is not None and denominator > 0 and 0 <= numerator <= denominator
            values[key+'_SHARE'] = metric(100*numerator/denominator if valid else None,
                'Share of team '+label, '% of team total', '100 * player total / team total',
                {'player_total':numerator,'team_total':denominator,'team_games':team['GP']})
            totals = [number(r.get(key)) for r in roster.values()]
            rank = 1+sum(x > numerator for x in totals) if numerator is not None and all(x is not None for x in totals) else None
            values[key+'_TEAM_RANK'] = metric(rank, 'Team rank by total '+label, 'rank (1 = most)',
                '1 + number of teammates with strictly higher season total; ties share rank', {'player_total':numerator})
        try:
            if not enrich:
                raise DataUnavailable('Advanced statistics have not been requested yet.')
            advanced = tools.data.fetch('team_players', **dict(params, measure_type_detailed_defense='Advanced'))
            usage_row = next((r for r in advanced['data'].get('PlayersSeasonTotals', []) if r.get('PLAYER_ID') == p.player_id), {})
            usage = number(usage_row.get('USG_PCT'))
            advanced_team = advanced['data'].get('TeamOverall', [])
            matching = (len(advanced_team) == 1 and advanced_team[0].get('TEAM_ID') == team_id
                        and usage_row.get('GP') == player.get('GP'))
            usage = 100*usage if matching and usage is not None and 0 <= usage <= 1 else None
            sources.append(source(advanced))
        except DataUnavailable as exc:
            usage = None
            limits.append(str(exc))
        values['USG_PCT'] = metric(usage, 'Usage rate', '%', '100 * NBA USG_PCT (source-defined estimate)', {})

    if 'teammate_support' in p.dimensions:
        a, b = number(team.get('PTS')), number(player.get('PTS'))
        support = (a-b)/team['GP'] if a is not None and b is not None and a >= b else None
        values['TEAMMATE_PTS_PER_TEAM_GAME'] = metric(support, 'Other players’ scoring per team game',
            'points per team game', '(team PTS - player PTS) / team GP',
            {'team_pts':a,'player_pts':b,'team_gp':team['GP']}, games=team['GP'])
        candidates = [r for ident,r in roster.items() if ident != p.player_id]
        if p.teammate_id is not None:
            if p.teammate_id == p.player_id or p.teammate_id not in roster:
                raise ValueError('Selected teammate must be a different player on this verified team-season roster.')
            selected = [roster[p.teammate_id]]
            selection_limit = 'One teammate explicitly selected by the model from the verified roster; this selection does not establish who is objectively strongest.'
        else:
            selected = []
            selection_limit = 'Teammates shown combine the top three by total minutes and top two by total points, excluding the focal player (at most five). This is not an ability ranking or a complete star count.'
        if p.teammate_id is None and any(number(r.get('MIN')) is None for r in candidates):
            limits.append('Teammate minute totals incomplete; no top-minute selection made.')
            candidates = []
        if p.teammate_id is None:
            selected = sorted(candidates, key=lambda r:(-r['MIN'],r['PLAYER_ID']))[:3]
        # Include scoring contributors who missed games; minutes alone can omit a co-star.
        if p.teammate_id is None and all(number(r.get('PTS')) is not None for r in candidates):
            for scorer in sorted(candidates, key=lambda r:(-r['PTS'],r['PLAYER_ID']))[:2]:
                if scorer not in selected:
                    selected.append(scorer)
        limits.append(selection_limit)
        for r in selected:
            mate = stat_record(r)
            mate['limitations'].append(limits[-1])
            mate = tools.add(mate)
            record['teammates'].append({'stats':mate})
    record['enriched'] = enrich
    record['sources'] = sources
    record['limitations'] = limits
    return tools.add(record)
