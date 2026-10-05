"""Server-owned, stance-independent team research queues and bounded execution."""
import copy
import re
from itertools import product
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from backend.debate.schemas import PlayerAwards, CompetitiveContext
from backend.debate.context import query_context, ROLE_METRICS, SUPPORT_METRICS
from backend.data.nba import DataUnavailable, number, provenance, season_label, stable_id, current_season


class ResearchIntent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    argument: Literal['championships', 'record', 'playoff_appearances', 'progression', 'support'] = Field(description='Team-context question to investigate: titles, team record, playoff appearances, progression or supporting cast. Planning alone does not fetch the queued evidence.')
    target_player: int | None = Field(default=None, gt=0, description='Optional focal NBA ID, restricted to the configured pair. Both players are still investigated; this does not filter the queue.')
    left_season: int | None = Field(default=None, ge=1946, le=2100, description='Optional season START year for the configured supported player; 2017 means 2017-18. Leave null if unspecified instead of inventing a year.')
    right_season: int | None = Field(default=None, ge=1946, le=2100, description='Optional season START year for the configured opponent; 2017 means 2017-18. Leave null if unspecified instead of inventing a year.')
    phase: Literal['Regular Season', 'Playoffs'] | None = Field(default=None, description='Optional Regular Season or Playoffs filter. Omit/null to let the selected investigation determine the relevant phases.')


class ResearchBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_id: str = Field(description='Exact plan_id returned by plan_team_context_research. Reuse it to execute successive server-ordered batches until complete.')


def _stints(entry, dataset):
    result = {}
    for row in entry.get('data', {}).get(dataset, []):
        if row.get('LEAGUE_ID', '00') != '00' or not number(row.get('GP')) or not number(row.get('TEAM_ID')):
            continue
        match = re.fullmatch(r'(\d{4})-\d{2}', str(row.get('SEASON_ID', '')))
        if match:
            result.setdefault(int(match[1]), set()).add(int(row['TEAM_ID']))
    return {y: sorted(teams) for y, teams in sorted(result.items())}


def plan_research(tools, intent):
    if intent.target_player is not None:
        tools.allowed(intent.target_player)
    if any(y is not None and y > current_season() for y in (intent.left_season, intent.right_season)):
        raise ValueError('Future seasons are unavailable.')
    plans = tools.research_state.setdefault('research_plans', {})
    request = intent.model_dump(exclude={'target_player'})
    ids = [tools.config.supported_player, tools.config.opponent_player]
    active = plans.get(tools.research_state.get('active_research'), {})
    if intent.left_season is None and intent.right_season is None and active.get('argument') == intent.argument and not active.get('selection_incomplete') and (intent.phase is None or active['request'].get('phase') == intent.phase):
        if active.get('parent_plan_id') and not any(u['status']=='pending' for u in active['queue']):
            active = plans[active['parent_plan_id']]
        tools.research_state['active_research'] = active['plan_id']
        return active
    plan_id = 'research_' + stable_id([ids, request])[3:]
    previous = plans.get(plan_id)
    if previous and not previous.get('selection_incomplete'):
        tools.research_state['active_research'] = plan_id
        return plans[plan_id]
    plan = dict(plan_id=plan_id, argument=intent.argument, request=request, queue=[], gaps=[], sources=[], careers={},
                pairing_rule='', selection_incomplete=False, selection_rule='Explicit scope first; otherwise chronological, then stable team ID. Independent of stance and results.')
    explicit = intent.left_season is not None or intent.right_season is not None
    # Keep the previous career queue when the user temporarily drills into a year.
    active = plans.get(tools.research_state.get('active_research'), {})
    if explicit and active.get('argument') == intent.argument:
        plan['parent_plan_id'] = active.get('parent_plan_id', active.get('plan_id'))
    for pid in ids:
        try:
            career = tools.data.fetch('career', player_id=pid, per_mode36='Totals', league_id_nullable='00')
            plan['careers'][str(pid)] = {'regular': _stints(career, 'SeasonTotalsRegularSeason'),
                                        'playoffs': _stints(career, 'SeasonTotalsPostSeason'),
                                        'team_names': {str(r.get('SEASON_ID'))+':'+str(r.get('TEAM_ID')):r.get('TEAM_ABBREVIATION') for key in ('SeasonTotalsRegularSeason','SeasonTotalsPostSeason') for r in career['data'].get(key, []) if r.get('TEAM_ABBREVIATION')}}
            plan['sources'].append(provenance(career, pid))
        except DataUnavailable as exc:
            plan['selection_incomplete'] = True
            plan['careers'][str(pid)] = {'regular': {}, 'playoffs': {}}
            plan['gaps'].append(f'{tools.names.get(str(pid), pid)}: {exc}')
    careers = [plan['careers'][str(pid)] for pid in ids]
    years = [sorted(set(c['regular']) | set(c['playoffs'])) for c in careers]
    titles = [set(), set()]
    if intent.argument == 'championships':
        for i, pid in enumerate(ids):
            try:
                record = tools.player_awards(PlayerAwards(player_id=pid, award='CHAMPIONSHIPS'))
                titles[i] = {int(s[:4]) for s in record['values']['CHAMPIONSHIPS']['covered_seasons']}
                plan['sources'].extend(record['sources'])
                if not titles[i]:
                    plan['gaps'].append(f'{tools.names.get(str(pid), pid)}: championship seasons unknown; missing awards do not mean zero.')
            except DataUnavailable as exc:
                plan['selection_incomplete'] = True
                plan['gaps'].append(str(exc))
    if explicit:
        pairs = [(intent.left_season, intent.right_season)]
        plan['pairing_rule'] = 'Explicitly selected seasons; a missing side remains unpaired.'
    elif set(years[0]) & set(years[1]) or not all(years):
        candidates = sorted(titles[0] | titles[1]) if intent.argument == 'championships' else sorted(set(years[0]) | set(years[1]))
        pairs = [(y, y) for y in candidates]
        plan['pairing_rule'] = 'Same calendar season; no substitution for an absent player.'
    else:
        stages = range(max(map(len, years), default=0))
        pairs = [(years[0][i] if i < len(years[0]) else None, years[1][i] if i < len(years[1]) else None) for i in stages]
        if intent.argument == 'championships':
            pairs = [(a, b) for a, b in pairs if a in titles[0] or b in titles[1]]
        plan['pairing_rule'] = 'Career stage: Nth season with verified NBA appearances, not age or peak. Missing stages stay unpaired.'
    phase = intent.phase or ('Playoffs' if intent.argument in ('championships', 'progression') else 'Regular Season')
    for a, b in pairs:
        phases = [phase]
        if phase == 'Playoffs' and any(y not in c['playoffs'] for y, c in zip((a, b), careers)):
            phases.append('Regular Season')
        for selected_phase in phases:
            key = 'playoffs' if selected_phase == 'Playoffs' else 'regular'
            teams = [c[key].get(y, [None]) for c, y in zip(careers, (a, b))]
            for t1, t2 in product(*teams):
                unit = dict(left_player=ids[0], right_player=ids[1], left_season=a, right_season=b,
                            left_team_id=t1, right_team_id=t2, phase=selected_phase,
                            supplement=selected_phase != phase,
                            title_sides=[side for side, y, ts in zip(('left', 'right'), (a, b), titles) if y in ts],
                            status='pending', evidence_ids=[], errors=[])
                unit['unit_id'] = stable_id(unit)
                for side, year, team, career in zip(('left','right'),(a,b),(t1,t2),careers):
                    unit[side+'_team_name'] = career.get('team_names',{}).get((season_label(year) if year else '')+':'+str(team))
                plan['queue'].append(unit)
    if not plan['queue']:
        plan['gaps'].append('No source-backed candidate seasons available for this argument.')
    if previous:
        done = {u['unit_id']:u for u in previous['queue'] if u['status'] != 'pending'}
        plan['queue'] = [done.get(u['unit_id'], u) for u in plan['queue']]
    plans[plan_id] = plan
    tools.research_state['active_research'] = plan_id
    return plan


def team_result_sample(record):
    """Derive postseason sample from outcomes, including older saved records."""
    if record['scope']['phase'] == 'Playoffs':
        values = record.get('values', {})
        wins = number(values.get('PO_WINS', {}).get('value'))
        losses = number(values.get('PO_LOSSES', {}).get('value'))
        return wins + losses if wins is not None and losses is not None and wins >= 0 and losses >= 0 else None
    return next((m.get('games') for m in record.get('values', {}).values() if m.get('games') is not None), None)


def _team_result(tools, unit, side):
    """Team results never count as individual qualifications from roster data alone."""
    pid, team, year = (unit[side + suffix] for suffix in ('_player', '_team_id', '_season'))
    entry = tools.data.fetch('team_history', team_id=team, league_id='00', per_mode_simple='Totals', season_type_all_star='Regular Season')
    if not isinstance(entry, dict) or not isinstance(entry.get('data'), dict):
        raise DataUnavailable('Team-season results unavailable.')
    rows = [r for r in entry['data'].get('TeamStats', []) if r.get('TEAM_ID') == team and r.get('YEAR') == season_label(year)]
    if len(rows) != 1:
        raise DataUnavailable('Matching team-season result unavailable.')
    row = rows[0]
    src = provenance(entry)
    src['url'] = entry['source_url'] + '?TeamID=' + str(team)
    wins, losses = number(row.get('PO_WINS')), number(row.get('PO_LOSSES'))
    sample = (wins + losses if wins is not None and losses is not None and wins >= 0 and losses >= 0 else None) if unit['phase'] == 'Playoffs' else number(row.get('GP'))
    values = {}
    for key, label in [('WINS', 'Team regular-season wins'), ('LOSSES', 'Team regular-season losses'), ('WIN_PCT', 'Team regular-season win percentage'), ('PO_WINS', 'Team playoff wins'), ('PO_LOSSES', 'Team playoff losses')]:
        if key.startswith('PO_') != (unit['phase'] == 'Playoffs'):
            continue
        value = number(row.get(key))
        if key == 'WIN_PCT' and value is not None:
            value *= 100
        values[key] = dict(value=value, label=label, complete=value is not None, unit='%' if key == 'WIN_PCT' else 'games',
                           games=sample, covered_seasons=[season_label(year)], formula='NBA team-season result', inputs={})
    record = tools.add(dict(kind='team_result', player_id=pid, player_name=tools.names.get(str(pid), str(pid)),
        context={'team_id': team, 'team_name': ' '.join(str(row.get(k) or '') for k in ('TEAM_CITY', 'TEAM_NAME')).strip() or str(team), 'focal_player_id': pid},
        scope=dict(start=year, end=year, phase=unit['phase'], basis='totals'), values=values, sources=[src],
        limitations=['Team results cover the whole season, not only this player’s games. Season stints do not establish end-of-season membership. Do not count team qualifications as individual appearances.',
                     'PO_WINS and PO_LOSSES are postseason team outcomes, not player participation or verified round labels.'],
        finals_result=row.get('NBA_FINALS_APPEARANCE')))
    return record


def research_batch(tools, params):
    plan = tools.research_state.get('research_plans', {}).get(params.plan_id)
    if not plan:
        raise ValueError('Unknown server research plan.')
    batch = []
    for unit in plan['queue']:
        if unit['status'] != 'pending':
            continue
        if len(batch) >= 2 or getattr(tools.data, 'remaining_seconds', 60) <= 0:
            break
        sides, requests = {}, {}
        # Fetch BOTH base rosters before optional advanced statistics and awards.
        for side in ('left', 'right'):
            if unit[side+'_season'] is None or unit[side+'_team_id'] is None:
                unit['errors'].append(f'{side}: no verified player appearances/team stint in this season and phase; not proof of nonqualification or injury.')
                continue
            p = CompetitiveContext(player_id=unit[side+'_player'], season=unit[side+'_season'], team_id=unit[side+'_team_id'], phase=unit['phase'], selection_reason=plan['selection_rule'])
            try:
                sides[side] = query_context(tools, p, enrich=False)
                requests[side] = p
            except DataUnavailable as exc:
                unit['errors'].append(f'{side}: {exc}')
        for side in requests:
            try:
                result = _team_result(tools, unit, side)
                unit['evidence_ids'].append(result['id'])
                if side in unit['title_sides'] and unit['phase'] == 'Playoffs' and result['finals_result'] != 'LEAGUE CHAMPION':
                    sides.pop(side, None)
                    unit['errors'].append(f'{side}: this stint is not a verified championship team.')
            except DataUnavailable as exc:
                unit['errors'].append(f'{side} team outcome: {exc}')
                if side in unit['title_sides']:
                    sides.pop(side, None)
        # Warm both advanced records before teammate-award lookups consume budget.
        for side in sides:
            p = requests[side]
            try:
                tools.data.fetch('team_players', team_id=p.team_id, season=season_label(p.season),
                                 season_type_all_star=p.phase, per_mode_detailed='Totals', measure_type_detailed_defense='Advanced')
            except DataUnavailable:
                pass
        for side in list(sides):
            try:
                sides[side] = query_context(tools, requests[side], enrich=True)
            except DataUnavailable as exc:
                unit['errors'].append(f'{side} optional detail: {exc}')
            unit[side+'_team_name'] = sides[side]['context']['team_name']
            unit['evidence_ids'].append(sides[side]['id'])
        if len(sides) == 2:
            comparison = tools._comparison(sides['left'], sides['right'], ROLE_METRICS + SUPPORT_METRICS)
            unit['evidence_ids'].append(comparison['id'])
            unit['status'] = 'completed'
        else:
            unit['status'] = 'unavailable'
        # Preserve single-side records, but never create a comparison with missing data.
        batch.append(copy.deepcopy(unit))
    return {'plan_id': params.plan_id, 'batch': batch, 'research': research_summary(tools.research_state, params.plan_id)}


def research_summary(state, plan_id=None):
    plan = state.get('research_plans', {}).get(plan_id or state.get('active_research'))
    if not plan:
        return None
    def scope(u):
        return dict(unit_id=u['unit_id'], left_player=u['left_player'], right_player=u['right_player'],
                    left_season=season_label(u['left_season']) if u['left_season'] else None,
                    right_season=season_label(u['right_season']) if u['right_season'] else None,
                    left_team_id=u['left_team_id'], right_team_id=u['right_team_id'],
                    left_team_name=u.get('left_team_name'), right_team_name=u.get('right_team_name'), phase=u['phase'],
                    supplement=u['supplement'], errors=u['errors'], evidence_ids=u['evidence_ids'])
    return dict(plan_id=plan['plan_id'], argument=plan['argument'], pairing_rule=plan['pairing_rule'],
                selection_rule=plan['selection_rule'], completed=[scope(u) for u in plan['queue'] if u['status']=='completed'],
                pending=[scope(u) for u in plan['queue'] if u['status']=='pending'],
                unavailable=[scope(u) for u in plan['queue'] if u['status']=='unavailable'],
                gaps=plan['gaps'], sources=plan['sources'], parent_plan_id=plan.get('parent_plan_id'))
