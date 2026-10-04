"""Scoped performance evidence for review follow-ups; never a dominance score."""
import re
from nba_api.stats.static import teams
from backend.data.nba import DataUnavailable, aggregate, number, provenance, season_label


def query_performance(tools, p):
    tools.allowed(p.player_id)
    season = season_label(p.season)
    sources, limitations, values = [], [], {}
    scope = dict(start=p.season, end=p.season, phase=p.phase, basis='per_game')
    record = dict(kind='performance_context', player_id=p.player_id,
                  player_name=tools.names.get(str(p.player_id), str(p.player_id)), scope=scope,
                  request=p.model_dump(), values=values, sources=sources, limitations=limitations)

    def fetch(kind, **params):
        entry = tools.data.fetch(kind, **params)
        sources.append(provenance(entry))
        return entry['data']

    def metric(value, label, unit, games, formula, inputs):
        return dict(value=value, label=label, unit=unit, games=games, complete=True,
                    covered_seasons=[season], formula=formula, inputs=inputs)

    if p.dimension == 'player_ranks':
        rows = fetch('league_players', season=season, season_type_all_star=p.phase,
                     per_mode_detailed='PerGame', measure_type_detailed_defense='Base',
                     league_id_nullable='00').get('LeagueDashPlayerStats', [])
        ids = [r.get('PLAYER_ID') for r in rows]
        if not rows or None in ids or len(set(ids)) != len(ids) or any(number(r.get('GP')) is None for r in rows):
            raise DataUnavailable('Player ranking rows are missing, duplicated or lack game counts.')
        eligible = [r for r in rows if number(r['GP']) >= p.min_games]
        player = next((r for r in eligible if r['PLAYER_ID'] == p.player_id), None)
        if player is None:
            raise DataUnavailable('Player is absent or below the selected minimum-games threshold.')
        for key in ('PTS', 'AST'):
            if any(number(r.get(key)) is None for r in eligible):
                raise DataUnavailable('Incomplete '+key+' ranking inputs.')
            value = number(player[key])
            inputs = dict(min_games=p.min_games, eligible_players=len(eligible), player_per_game=value)
            values[key] = metric(value, key+' per game', 'per game', player['GP'], 'NBA PerGame value', inputs)
            values[key+'_LEAGUE_RANK'] = metric(1+sum(number(r[key]) > value for r in eligible),
                key+' rank among returned players with at least '+str(p.min_games)+' games',
                'rank (1 = most)', player['GP'], '1 + eligible players with a strictly higher per-game value; ties share rank', inputs)
        limitations.extend([
            f'Ranks cover the returned NBA {season} {p.phase} player table with GP >= {p.min_games}; ties share rank. This is not official scoring/assist-title qualification.',
            'One season does not establish a career peak or historical rank. PTS and AST alone do not measure all offensive creation.',
        ])
        return tools.add(record)

    # NBA advanced team dashboards begin in 1996-97. Fail closed for older eras.
    if p.season < 1996:
        raise DataUnavailable('Advanced team ratings are unavailable here before 1996-97.')
    benchmark_phase = 'Regular Season' if p.dimension == 'opponent_splits' else p.phase
    rows = fetch('league', season=season, season_type_all_star=benchmark_phase,
                 per_mode_detailed='PerGame', measure_type_detailed_defense='Advanced',
                 league_id_nullable='00').get('LeagueDashTeamStats', [])
    expected = 16 if benchmark_phase == 'Playoffs' else (30 if p.season >= 2004 else 29)
    ids = [r.get('TEAM_ID') for r in rows]
    key = 'DEF_RATING' if p.dimension == 'opponent_splits' else 'OFF_RATING'
    if (len(rows) != expected or len(set(ids)) != expected or None in ids
            or any(number(r.get(key)) is None or number(r.get('GP')) is None or number(r['GP']) <= 0 for r in rows)):
        raise DataUnavailable('A complete team rating table could not be verified.')

    if p.dimension == 'team_offense':
        career = fetch('career', player_id=p.player_id, per_mode36='Totals', league_id_nullable='00')
        dataset = 'SeasonTotalsRegularSeason' if p.phase == 'Regular Season' else 'SeasonTotalsPostSeason'
        stints = {r['TEAM_ID'] for r in career.get(dataset, []) if r.get('SEASON_ID') == season
                  and r.get('TEAM_ID') and number(r.get('GP')) is not None and number(r['GP']) > 0}
        if not stints:
            raise DataUnavailable('No verified team stint in this season and phase.')
        if p.team_id is None and len(stints) > 1:
            return dict(status='needs_team', teams=sorted(stints), message='Choose one verified team stint for this traded season.')
        team_id = p.team_id or next(iter(stints))
        if team_id not in stints:
            raise ValueError('The requested team is not a verified player stint.')
        team = next((r for r in rows if r['TEAM_ID'] == team_id), None)
        if team is None:
            raise DataUnavailable('The player team is absent from the rating table.')
        rating = number(team[key])
        record['context'] = dict(team_id=team_id, team_name=team['TEAM_NAME'], focal_player_id=p.player_id)
        inputs = dict(team_id=team_id, team_name=team['TEAM_NAME'], teams_ranked=len(rows))
        values['TEAM_OFF_RATING'] = metric(rating, 'Team offensive rating', 'points per 100 possessions',
                                           team['GP'], 'NBA team OFF_RATING', inputs)
        values['TEAM_OFF_RANK'] = metric(1+sum(number(r[key]) > rating for r in rows), 'Team offensive rating rank',
            'rank (1 = best)', team['GP'], '1 + teams with strictly higher OFF_RATING; ties share rank', inputs)
        limitations.append('Full team-season offense, including games this player missed; not individual offensive rating or proof that the player caused the team result. Combine with separately cited individual-role evidence.')
    else:
        # Define opposition before examining the player's performance. All tied teams qualify.
        cutoff = sorted(number(r[key]) for r in rows)[p.top_n-1]
        selected = [r for r in rows if number(r[key]) <= cutoff]
        abbreviations = {r['id']:r['abbreviation'] for r in teams.get_teams()}
        lookup = {}
        for r in rows:
            abbr = r.get('TEAM_ABBREVIATION') or abbreviations.get(r['TEAM_ID'])
            if not abbr or abbr in lookup:
                raise DataUnavailable('Opponent abbreviations could not be verified.')
            lookup[abbr] = r['TEAM_ID']
        opponents = {r['TEAM_ID'] for r in selected}
        logs = fetch('games', player_id=p.player_id, season=season,
                     season_type_all_star=p.phase).get('PlayerGameLog', [])
        if not logs:
            raise DataUnavailable('No player game log returned for the selected season/phase.')
        games, seen = [], set()
        for row in logs:
            ident = row.get('Game_ID')
            match = re.fullmatch(r'[A-Z]{2,3} (?:vs\.|@) ([A-Z]{2,3})', row.get('MATCHUP', ''))
            if not ident or ident in seen or not match or match[1] not in lookup:
                raise DataUnavailable('Game IDs or opponent mapping are incomplete/duplicated; no opponent split published.')
            seen.add(ident)
            if lookup[match[1]] in opponents:
                games.append(dict(row, SEASON_ID=season, GP=1))
        if not games:
            raise DataUnavailable('No returned games against the defined top defenses; this is not zero production.')
        stats = aggregate(games, 'per_game')
        sample = dict(top_n=p.top_n, benchmark_phase='Regular Season', selection='lowest team DEF_RATING, including ties',
                      opponents=[dict(team_id=r['TEAM_ID'], team_name=r['TEAM_NAME'], def_rating=r[key]) for r in selected],
                      checked_games=len(logs), matched_games=len(games), game_ids=[r['Game_ID'] for r in games])
        for key in ('GP', 'PTS', 'AST', 'TS_PCT'):
            m = stats[key]
            if not m['complete'] or m['value'] is None:
                limitations.append(key+' unavailable for the opponent sample.')
                continue
            values['VS_TOP_DEF_'+key] = dict(m, covered_seasons=[season],
                label=key+f' against top-{p.top_n} regular-season defenses (ties included)', inputs=dict(m['inputs'], **sample))
        if not values:
            raise DataUnavailable('No complete opponent-split metrics.')
        limitations.extend([
            f'Opponent sample: top {p.top_n} by lowest {season} regular-season team DEF_RATING, including ties; {len(games)} matched games from {len(logs)} returned {p.phase} logs. Cite the game count and definition.',
            'Opponents use the fetched whole-season rating, not their rating on each game date. Top defense is one explicit proxy, not all elite competition. A sample average alone does not prove dominance or historic superiority.',
        ])
    limitations.append('Ratings/ranks describe the fetched season snapshot; an ongoing season is provisional. No historical or career-wide ranking is established.')
    return tools.add(record)
