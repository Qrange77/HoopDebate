"""Structured debate tools. All claims refer to server-owned evidence snapshots."""
import json
import operator
from backend.data.nba import NBAData, DataUnavailable, aggregate, season_rows, season_label, provenance, stable_id
from backend.data.honors import AWARDS, DEFAULT_AWARDS, normalize_awards
from backend.debate.research import ResearchIntent, ResearchBatch, plan_research, research_batch, team_result_sample
from backend.debate.context import query_context, ROLE_METRICS, SUPPORT_METRICS
from backend.debate.performance import query_performance
from backend.debate.comparison import PANEL_METRICS
from backend.debate.teammates import compare_key_teammates
# Re-export parameter types for existing callers importing them from tools.
from backend.debate.schemas import (
    Phase, Basis, METRICS, Params, DebateConfig, Scope, Resolve, Query, Compare,
    Counterexample, PlayerAwards, CompareAwards, Edges, Claim, ContextDimension,
    CompetitiveContext, CompareContext, PerformanceContext, Audit,
)

MODELS = {'plan_team_context_research':ResearchIntent, 'research_team_context':ResearchBatch, 'resolve_player': Resolve, 'query_evidence': Query, 'compare_players': Compare,
          'find_counterexamples': Counterexample, 'find_comparative_edges': Edges, 'audit_argument': Audit,
          'player_awards': PlayerAwards, 'compare_awards': CompareAwards,
          'query_competitive_context':CompetitiveContext, 'compare_competitive_context':CompareContext,
          'query_performance_context':PerformanceContext}
DESCRIPTIONS = {
    'query_performance_context':'Fill performance evidence gaps for ONE selected season/phase. player_ranks returns PTS/AST per game and ranks among returned players meeting min_games (not official scoring titles); team_offense returns verified team OFF_RATING and league rank (1996+); opponent_splits returns player GP/PTS/AST/TS% against top_n regular-season defenses including ties (1996+). Requires selection_reason. Cite scope, eligibility/opponent definition and sample size. Pair team offense with query_competitive_context individual_role for primary-engine claims. These data do not establish historical superiority or causation.',
    'plan_team_context_research':'Plan a source-backed investigation of team outcomes and supporting casts for the two LOCKED players. This only creates a queue; it does not run the investigation. No season is required. Use championships, record, playoff_appearances, progression or support. Left means configured supported player, right means opponent. Do not invent years; leave unspecified seasons null.',
    'research_team_context':'Execute the next server-ordered batch of a research plan, at most two units per CALL; additional calls share the turn network and tool-round budgets. Cannot skip to favorable seasons. Returns coverage and source evidence.',
    'query_competitive_context':'Look up ONE explicitly identified season: team-scoped teammates (top three by total minutes plus top two by total points, at most five), player team shares/ranks and usage. Requires a valid season and selection_reason. You may select a season the user did not name; explain why and limit conclusions to that sample. Traded seasons may require team_id. Set teammate_id to inspect one specific verified non-focal teammate instead of the broad shortlist. Returned teammate evidence is citable with that teammate player_id. Not a causal team-strength score.',
    'compare_competitive_context':'Compare representative strong teammates of the TWO LOCKED players. Default focus=key_teammates: first omit teammate IDs to discover both verified rosters with season stats (no automatic strongest-player choice); then choose one non-focal teammate per side via left_teammate_id/right_teammate_id and explain BOTH choices in teammate_selection_reason. Returns their head-to-head statistics with citable evidence. You decide whether this angle is relevant, which seasons/phase to check and how to interpret it; preserve unfavorable evidence. A stronger co-star can challenge assigning all team success to one individual, never prove wins fake or teammates caused outcomes. Use focus=team_context for the older broad role/support comparison.',
    'resolve_player': 'Resolve NBA player names and aliases, independently of a game. Returns candidates if ambiguous.',
    'query_evidence': 'Fetch sourced NBA stats, awards, season game logs, or league shooting baseline. Games/league require one explicit season. NBA IDs only.',
    'compare_players': 'Metrics must use exact codes: PTS, REB, AST, STL, BLK, TOV, MIN, GP, TS_PCT, EFG_PCT, FG_PCT, FG3_PCT, FT_PCT, REL_TS, FGM, FGA, FG3M, FG3A, FTM, FTA, OREB, DREB. Compare two players with compatible scopes. Returns both sides and missing coverage, never an overall winner.',
    'find_counterexamples': 'Find games satisfying an explicit numeric predicate in one season. No match is not proof of career absence.',
    'find_comparative_edges': 'Inspect a fixed metric set. Returns advantages, disadvantages and ties for the LEFT player; no cherry-picked overall score.',
    'audit_argument': 'Check numeric claims against server evidence. Include the EXACT metric value, scope, player and comparison direction returned by tools.',
    'player_awards': 'Look up verified NBA honors and winning seasons: championships, MVP, Finals MVP, DPOY, All-NBA (including First/Second/Third Teams), All-Defensive, All-Star, ROY, Sixth Man, MIP. Use award=all or a code. Missing is unknown, not zero. Source: NBA PlayerAwards.',
    'compare_awards': 'Compare both players\' sourced NBA honors with winning seasons. Default codes: CHAMPIONSHIPS, MVP, FINALS_MVP, DPOY, ALL_NBA, ALL_DEFENSIVE, ALL_STAR. Awards are total counts across the selected award seasons; no regular-season/playoff split. Never confuse All-Star MVP with MVP.',
}
DEBATE_TOOLS = [{'type': 'function', 'function': {'name': name, 'description': DESCRIPTIONS[name],
                 'parameters': model.model_json_schema()}} for name, model in MODELS.items()]


def relation(a, b):
    return 'higher' if a > b else 'lower' if a < b else 'equal'


def scope_text(scope):
    window = 'career' if scope['start'] is None else season_label(scope['start']) + (f" through {season_label(scope['end'])}" if scope['end'] != scope['start'] else '')
    return f"{window} · {scope['phase']} · {scope['basis'].replace('_', ' ')}"

def evidence_scope_text(record):
    suffix = ' · ' + record['context']['team_name'] if record.get('context') else ''
    if record.get('award_categories'):
        return scope_text(record['scope']).split(' · ')[0] + ' · honors by award season' + suffix
    return scope_text(record['scope']) + suffix


class DebateTools:
    def __init__(self, config, evidence=None, data=None, names=None, research_state=None):
        self.config = config
        self.data = data or NBAData()
        self.evidence = dict(evidence or {})
        self.names = names or {}
        self.fact_claims = {}
        self.research_state = research_state if research_state is not None else {}

    def allowed(self, ident):
        if ident not in (self.config.supported_player, self.config.opponent_player):
            raise ValueError('This debate is limited to the two selected NBA players.')

    def add(self, record):
        record['id'] = stable_id(record)
        self.evidence[record['id']] = record
        return record

    def run(self, name, args):
        try:
            if name not in MODELS:
                raise ValueError('Unknown debate tool.')
            obj = MODELS[name].model_validate(args)
            result = getattr(self, name)(obj)
            if name in ('query_competitive_context', 'compare_competitive_context', 'query_performance_context'):
                self.record_directed_query(name, obj, result)
            return result
        except (ValueError, DataUnavailable, KeyError, TypeError) as exc:
            result = {'error': str(exc)[:700], 'status': 'unavailable'}
            if name in ('query_competitive_context','compare_competitive_context','query_performance_context') and 'obj' in locals():
                self.record_directed_query(name, obj, result)
            return result

    def record_directed_query(self, name, params, result):
        records = [result] if result.get('id') else [r for key in ('left','right') if isinstance(r := result.get(key), dict) and r.get('id')]
        sources = list({json.dumps(s,sort_keys=True):s for r in records for s in r.get('sources',[])}.values())
        item = {'tool':name, 'request':params.model_dump(), 'selection_reason':params.selection_reason,
                'status':'completed' if result.get('id') else result.get('status','unavailable'),
                'evidence_ids':[r['id'] for r in records], 'sources':sources,
                'errors':list(result.get('limitations', [])), 'limitations':list(result.get('limitations', []))}
        if result.get('error') or result.get('message'):
            item['errors'].append(result.get('error') or result['message'])
        for side in ('left','right'):
            if isinstance(result.get(side), dict) and result[side].get('error'):
                item['errors'].append(side + ': ' + result[side]['error'])
        item['id'] = stable_id(item)
        records = self.research_state.setdefault('directed_queries', [])
        if not any(r['id']==item['id'] for r in records):
            records.append(item)

    def plan_team_context_research(self, p):
        return plan_research(self, p)

    def research_team_context(self, p):
        return research_batch(self, p)

    def resolve_player(self, p):
        return self.data.resolve(p.query)

    def query_competitive_context(self, p):
        return query_context(self, p)

    def query_performance_context(self, p):
        return query_performance(self, p)

    def compare_competitive_context(self, p):
        self.allowed(p.left_player)
        self.allowed(p.right_player)
        if p.focus == 'key_teammates':
            return compare_key_teammates(self, p)
        sides = {}
        for side in ('left','right'):
            try:
                sides[side] = self.query_competitive_context(CompetitiveContext(
                    player_id=getattr(p,side+'_player'), season=getattr(p,side+'_season'),
                    team_id=getattr(p,side+'_team_id'), phase=p.phase, dimensions=p.dimensions, selection_reason=p.selection_reason))
            except DataUnavailable as exc:
                sides[side] = {'status':'unavailable','error':str(exc)}
        if any(r.get('kind') != 'competitive_context' for r in sides.values()):
            return {'status':'partial', **sides, 'limitations':['No comparison is supported until both sides have matching context records.']}
        keys = (ROLE_METRICS if 'individual_role' in p.dimensions else []) + (SUPPORT_METRICS if 'teammate_support' in p.dimensions else [])
        result = self._comparison(sides['left'], sides['right'], keys)
        return result

    def player_awards(self, p):
        self.allowed(p.player_id)
        entry = self.data.fetch('awards', player_id=p.player_id)
        rows = entry['data'].get('PlayerAwards', [])
        if not rows:
            raise DataUnavailable('No verifiable award records returned; this does not establish zero awards.')
        scope = p.scope.model_copy(update={'basis':'totals', 'phase':'Regular Season'})
        values = normalize_awards(rows, p.player_id, scope.start, scope.end)
        if p.award != 'all':
            values = {p.award: values[p.award]}
        source = provenance(entry, p.player_id)
        source['url'] = entry['source_url'] + f'?PlayerID={p.player_id}'
        return self.add({'kind':'awards', 'award_categories':True, 'player_id':p.player_id,
                         'player_name':self.names.get(str(p.player_id), str(p.player_id)), 'scope':scope.model_dump(),
                         'values':values, 'sources':[source],
                         'limitations':['Counts reflect distinct NBA source records. Missing categories are unknown, not zero; award availability differs by era.',
                                        'Championships are team achievements; Finals MVP is a separate individual award.',
                                        'Honors use award seasons, not regular-season/playoff box-score splits.']})

    def compare_awards(self, p):
        left = self.player_awards(PlayerAwards(player_id=p.left_player, scope=p.left_scope))
        right = self.player_awards(PlayerAwards(player_id=p.right_player, scope=p.right_scope))
        return self._comparison(left, right, p.awards)

    def honors_summary(self):
        def scope(year):
            return Scope(start=year, end=year, basis='totals')
        expected_left = scope(self.config.supported_season).model_dump()
        expected_right = scope(self.config.opponent_season).model_dump()
        records = list(self.evidence.values())
        left = next((r for r in reversed(records) if r.get('award_categories') and r.get('player_id')==self.config.supported_player and r.get('scope')==expected_left and all(k in r['values'] for k in DEFAULT_AWARDS)), None)
        right = next((r for r in reversed(records) if r.get('award_categories') and r.get('player_id')==self.config.opponent_player and r.get('scope')==expected_right and all(k in r['values'] for k in DEFAULT_AWARDS)), None)
        if not left or not right:
            return None
        return {'left_player':left['player_name'], 'right_player':right['player_name'],
                'left_scope':evidence_scope_text(left), 'right_scope':evidence_scope_text(right),
                'rows':[{'metric':k, 'label':AWARDS[k][0], 'left_value':left['values'][k]['value'],
                         'right_value':right['values'][k]['value'], 'left_seasons':left['values'][k]['covered_seasons'],
                         'right_seasons':right['values'][k]['covered_seasons']} for k in DEFAULT_AWARDS],
                'sources':left['sources']+right['sources'], 'limitations':left['limitations']}

    def saved_honors_comparison(self):
        """Rebuild all honor comparisons from the saved snapshots without fetching.

        Older conversations only compared the seven default categories, although
        their individual snapshots also contain First/Second/Third Team records.
        """
        records = list(self.evidence.values())
        sides = []
        for player, season in ((self.config.supported_player, self.config.supported_season),
                               (self.config.opponent_player, self.config.opponent_season)):
            scope = Scope(start=season, end=season, basis='totals').model_dump()
            record = next((r for r in reversed(records) if r.get('award_categories')
                           and r.get('player_id') == player and r.get('scope') == scope
                           and all(k in r['values'] for k in AWARDS)), None)
            if record is None:
                return None
            sides.append(record)
        return self._comparison(*sides, list(AWARDS))

    def repair_comparison_references(self, claims):
        """Resolve a child citation to its containing comparison only on exact audit.

        Never change values, scope, player or direction, or guess an unknown ID.
        The normal mandatory audit still runs after this narrow citation repair.
        """
        repaired, changes = [], []
        for index, claim in enumerate(claims):
            original = claim
            record = self.evidence.get(claim.evidence_id)
            if (record and record.get('kind') != 'comparison' and claim.relation is not None
                    and claim.other_value is not None):
                for candidate in reversed(list(self.evidence.values())):
                    if candidate.get('kind') != 'comparison':
                        continue
                    if claim.evidence_id not in (candidate['left']['id'], candidate['right']['id']):
                        continue
                    corrected = claim.model_copy(update={'evidence_id':candidate['id']})
                    if self.audit_argument(Audit(claims=[corrected]))['valid']:
                        claim = corrected
                        changes.append({'claim':index, 'from':original.evidence_id, 'to':claim.evidence_id})
                        break
            repaired.append(claim)
        return repaired, changes

    def query_evidence(self, p):
        self.allowed(p.player_id)
        scope = p.scope
        limitations = []
        name = self.names.get(str(p.player_id), str(p.player_id))
        if p.kind == 'stats':
            entry = self.data.fetch('career', player_id=p.player_id, per_mode36='Totals', league_id_nullable='00')
            dataset = 'SeasonTotalsRegularSeason' if scope.phase == 'Regular Season' else 'SeasonTotalsPostSeason'
            rows = season_rows(entry['data'].get(dataset, []), scope.start, scope.end)
            if not rows:
                raise DataUnavailable('No NBA season statistics in this range. Missing data is not zero.')
            values = aggregate(rows, scope.basis)
            if scope.start is not None:
                returned_years = {int(r['SEASON_ID'][:4]) for r in rows}
                missing_years = sorted(set(range(scope.start, scope.end + 1)) - returned_years)
                if missing_years:
                    limitations.append('No season rows for ' + ', '.join(season_label(y) for y in missing_years) +
                                       '; the requested range is only partially covered.')
                    for metric in values.values():
                        metric['complete'] = False
            if any(not m['complete'] for m in values.values()):
                limitations.append('Some historical metrics have incomplete coverage; inspect covered seasons. Missing values are not zero.')
            record = {'kind': 'stats', 'player_id': p.player_id, 'player_name': name, 'scope': scope.model_dump(),
                      'values': values, 'seasons': [r['SEASON_ID'] for r in rows], 'sources': [provenance(entry, p.player_id)],
                      'limitations': limitations}
            if scope.start is not None and scope.start == scope.end:
                try:
                    baseline = self.query_evidence(Query(player_id=p.player_id, kind='league', scope=scope))
                    ts = values['TS_PCT']['value']
                    league_ts = baseline['values']['TS_PCT']['value']
                    values['REL_TS'] = {'value': ts-league_ts if ts is not None and league_ts is not None else None,
                                         'unit': 'percentage points vs league', 'games': values['GP']['value'],
                                         'complete': ts is not None and league_ts is not None,
                                         'covered_seasons': record['seasons'], 'formula': 'player TS% - league TS%',
                                         'inputs': {'player_ts': ts, 'league_ts': league_ts}}
                    record['sources'] += baseline['sources']
                except DataUnavailable as exc:
                    limitations.append(f'Era baseline unavailable: {exc}. Raw efficiency is not era-adjusted.')
            return self.add(record)
        if p.kind in ('games', 'league') and (scope.start is None or scope.start != scope.end):
            raise ValueError('Games and league baselines require one explicit season; do not scan an entire career.')
        if p.kind == 'league':
            entry = self.data.fetch('league', season=season_label(scope.start), season_type_all_star=scope.phase,
                                    per_mode_detailed='Totals', measure_type_detailed_defense='Base', league_id_nullable='00')
            rows = entry['data'].get('LeagueDashTeamStats', [])
            teams = {r.get('TEAM_ID') for r in rows}
            counts = {1949:17,1950:11,1951:10,1953:9,1954:8,1961:9,1966:10,1967:12,1968:14,1970:17,1974:18,1976:22,1980:23,1988:25,1989:27,1995:29,2004:30}
            expected = next((v for y,v in sorted(counts.items(), reverse=True) if scope.start >= y), None)
            if scope.phase == 'Playoffs':
                expected = 16 if scope.start >= 1983 else 12 if scope.start >= 1976 else 10 if scope.start >= 1974 else 8 if scope.start >= 1966 else None
            if not expected or len(rows) != expected or len(teams) != expected or None in teams:
                raise DataUnavailable('A complete league team baseline could not be verified.')
            values = aggregate([dict(r, SEASON_ID=season_label(scope.start)) for r in rows], 'totals')
            if values['TS_PCT']['value'] is None:
                raise DataUnavailable('League shooting inputs are incomplete.')
            return self.add({'kind': 'league', 'player_id': None, 'player_name': 'NBA league', 'scope': scope.model_dump(),
                             'values': {'TS_PCT': values['TS_PCT']}, 'sources': [provenance(entry)], 'limitations': []})
        if p.kind == 'awards':
            entry = self.data.fetch('awards', player_id=p.player_id)
            rows = entry['data'].get('PlayerAwards', [])
            counts, seen = {}, set()
            for r in rows:
                description = r.get('DESCRIPTION', '')
                if not description or not r.get('SEASON'):
                    continue
                year = int(str(r['SEASON'])[:4])
                if scope.start is not None and not scope.start <= year <= scope.end:
                    continue
                key = tuple(str(r.get(k,'')) for k in ('DESCRIPTION','SEASON','MONTH','WEEK','ALL_NBA_TEAM_NUMBER','SUBTYPE1','SUBTYPE2'))
                if key in seen:
                    continue
                seen.add(key)
                counts.setdefault(description, []).append(str(r['SEASON']))
            if not counts:
                raise DataUnavailable('No verifiable award records returned; this does not establish zero awards.')
            values = {k: {'value':len(v), 'unit':'verified award records', 'games':None, 'covered_seasons':sorted(set(v)),
                           'complete':True, 'formula':'count(distinct sourced award records)', 'inputs':{}} for k,v in counts.items()}
            return self.add({'kind':'awards','player_id':p.player_id,'player_name':name,'scope':scope.model_dump(),
                             'values':values,'sources':[provenance(entry,p.player_id)],
                             'limitations':['Awards cover their named season, not the selected game phase. Missing awards are not zero; awards may not have existed in an earlier era. Counts describe returned records, not independently verified completeness.']})
        entry = self.data.fetch('games', player_id=p.player_id, season=season_label(scope.start), season_type_all_star=scope.phase)
        rows = entry['data'].get('PlayerGameLog', [])
        records = []
        seen = set()
        for row in rows:
            ident = str(row.get('Game_ID', ''))
            if not ident or ident in seen:
                continue
            seen.add(ident)
            clean = season_rows([dict(row, SEASON_ID=season_label(scope.start), GP=1)], scope.start, scope.end)
            records.append(self.add({'kind':'game','player_id':p.player_id,'player_name':name,'scope':scope.model_dump(),
                                    'nba_game_id':ident,'date':row.get('GAME_DATE'),'matchup':row.get('MATCHUP'),
                                    'values':aggregate(clean,'totals'),'sources':[provenance(entry,p.player_id)],
                                    'limitations':['A single game does not establish overall superiority.']}))
        return {'games':records,'checked_games':len(records),'scope':scope.model_dump(),
                'complete':bool(records),'limitations':['Coverage is the returned NBA season log; no career-wide absence inference.']}

    def compare_players(self, p):
        self.allowed(p.left_player)
        self.allowed(p.right_player)
        sides = {}
        for side in ('left', 'right'):
            player, scope = getattr(p, side+'_player'), getattr(p, side+'_scope')
            try:
                saved = next((r for r in reversed(list(self.evidence.values())) if r.get('kind') == 'stats'
                              and r.get('player_id') == player and r.get('scope') == scope.model_dump()), None)
                sides[side] = saved or self.query_evidence(Query(player_id=player, scope=scope))
            except DataUnavailable as exc:
                sides[side] = {'status':'unavailable', 'error':str(exc)}
        if any(r.get('error') for r in sides.values()):
            return {'status':'partial', **sides, 'limitations':['One side is unavailable; no comparative advantage is established.']}
        return self._comparison(sides['left'], sides['right'], list(dict.fromkeys([*p.metrics, *PANEL_METRICS])))

    def _comparison(self, left, right, keys):
        metrics = {}
        for metric in keys:
            a,b = left['values'].get(metric,{}), right['values'].get(metric,{})
            av,bv = a.get('value'),b.get('value')
            valid = a.get('complete') and b.get('complete') and av is not None and bv is not None
            metrics[metric] = {'left_value':av,'right_value':bv,'difference':av-bv if valid else None,
                               'relation':relation(av,bv) if valid else None, 'comparable':bool(valid),
                               'unit':a.get('unit',b.get('unit','')), 'left_games':a.get('games'), 'right_games':b.get('games')}
        return self.add({'kind':'comparison','left':left,'right':right,'metrics':metrics,
                         'sources':left['sources']+right['sources'],
                         'limitations':list(dict.fromkeys(left['limitations']+right['limitations']+['A metric advantage is not proof of overall superiority.']))})

    def find_counterexamples(self, p):
        if p.metric not in METRICS or p.metric in ('GP','REL_TS'):
            raise ValueError('Choose a supported single-game metric.')
        data = self.query_evidence(Query(player_id=p.player_id,kind='games',scope=p.scope))
        op = {'gt':operator.gt,'gte':operator.ge,'lt':operator.lt,'lte':operator.le,'eq':operator.eq}[p.comparison]
        matches = [r for r in data['games'] if r['values'].get(p.metric,{}).get('value') is not None and op(r['values'][p.metric]['value'],p.threshold)]
        return {'matches':matches[:5],'matching_games':len(matches),'checked_games':data['checked_games'],
                'scope':data['scope'],'complete':data['complete'], 'limitations':data['limitations'],
                'predicate':{'metric':p.metric,'comparison':p.comparison,'threshold':p.threshold}}

    def find_comparative_edges(self, p):
        dimensions = {'scoring':['PTS','TS_PCT','EFG_PCT'], 'playmaking':['AST','TOV'], 'rebounding':['REB'],
                      'defense_boxscore':['STL','BLK'], 'all':['PTS','REB','AST','TS_PCT','EFG_PCT','STL','BLK','TOV']}
        record = self.compare_players(Compare(**p.model_dump(exclude={'dimension'}),metrics=dimensions[p.dimension]))
        result = {'evidence':record,'advantages':[],'disadvantages':[],'ties':[],'unavailable':[]}
        if record.get('status') == 'partial':
            return record
        for key in dimensions[p.dimension]:
            m = record['metrics'][key]
            if not m['comparable']:
                result['unavailable'].append(key)
            elif m['relation']=='equal':
                result['ties'].append(key)
            else:
                better = m['relation'] == ('lower' if key=='TOV' else 'higher')
                result['advantages' if better else 'disadvantages'].append(key)
        result['limitations'] = ['Turnover volume depends on role and usage. Steals and blocks do not measure overall defense.']
        return result

    def audit_argument(self, p):
        accepted, errors, cards = [], [], []
        for index, claim in enumerate(p.claims):
            record = self.evidence.get(claim.evidence_id)
            error = None
            card = None
            if not record:
                error = 'Unknown evidence ID.'
            elif record['kind']=='comparison':
                a,b = record['left'],record['right']
                m = record['metrics'].get(claim.metric,{})
                if claim.player_id == b['player_id']:
                    a,b = b,a
                av,bv = a['values'].get(claim.metric,{}).get('value'),b['values'].get(claim.metric,{}).get('value')
                if claim.player_id != a['player_id'] or claim.scope.model_dump()!=a['scope']:
                    error = 'Wrong player or scope.'
                elif not m.get('comparable') or av is None or bv is None:
                    error = 'Incomplete or incomparable metric.'
                elif claim.value is None or abs(claim.value-av)>1e-6 or claim.other_value is None or abs(claim.other_value-bv)>1e-6 or claim.relation!=relation(av,bv):
                    error = 'Incorrect value or comparison direction; use unrounded tool values.'
                else:
                    card = {'title':claim.metric,'player':a['player_name'],'other_player':b['player_name'],
                            'value':av,'other_value':bv,'unit':m['unit'],'scope':evidence_scope_text(a),
                            'other_scope':evidence_scope_text(b), 'games':a['values'][claim.metric].get('games'),
                            'other_games':b['values'][claim.metric].get('games')}
                    if a.get('award_categories'):
                        card.update(label=a['values'][claim.metric]['label'], award=True,
                                    covered_seasons=a['values'][claim.metric]['covered_seasons'],
                                    other_covered_seasons=b['values'][claim.metric]['covered_seasons'])
            else:
                m=record.get('values',{}).get(claim.metric,{})
                if claim.player_id!=record.get('player_id') or claim.scope.model_dump()!=record['scope']:
                    error='Wrong player or scope.'
                elif claim.other_value is not None or claim.relation is not None:
                    error='This is a single-player evidence ID. Cite the containing comparison ID for other_value/relation, or omit both fields for an individual claim.'
                elif not m or ((claim.value is None) != (m.get('value') is None)) or (claim.value is not None and abs(claim.value-m['value'])>1e-6):
                    error='Incorrect or unsupported value.'
                else:
                    card={'title':claim.metric,'player':record['player_name'],'value':m['value'],'unit':m['unit'],
                          'scope':evidence_scope_text(record),'games':m.get('games'),'covered_seasons':m.get('covered_seasons',[]),
                          'formula':m.get('formula'),'inputs':m.get('inputs'), 'date':record.get('date'), 'matchup':record.get('matchup')}
                    if m.get('label'):
                        card['label'] = m['label']
                    if record.get('award_categories'):
                        card.update(label=m['label'], award=True)
            if error:
                errors.append({'claim':index,'error':error})
            else:
                accepted.append(claim.model_dump())
                subject = a if record['kind']=='comparison' else record
                if subject.get('kind') == 'team_result':
                    card['games'] = team_result_sample(subject)
                    if record['kind'] == 'comparison':
                        card['other_games'] = team_result_sample(b)
                if subject.get('context'):
                    card.update(context=subject['context'])
                    if subject['values'][claim.metric].get('label'):
                        card['label'] = subject['values'][claim.metric]['label']
                    if record['kind']=='comparison':
                        card['other_context'] = b.get('context')
                        card['inputs'] = {'player':subject['values'][claim.metric].get('inputs',{}),
                                          'other_player':b['values'][claim.metric].get('inputs',{})}
                        card['formula'] = subject['values'][claim.metric].get('formula')
                        card['team_games'] = subject.get('team_games')
                        card['other_team_games'] = b.get('team_games')
                    else:
                        card['team_games'] = subject.get('team_games')
                card.update(player_id=claim.player_id, evidence_id=claim.evidence_id,sources=record['sources'],limitations=record['limitations'])
                if card not in cards:
                    cards.append(card)
        return {'valid':not errors,'accepted':accepted,'errors':errors,'cards':cards}
