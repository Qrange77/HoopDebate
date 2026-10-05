"""Shared debate parameters and validation, independent of tool execution."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from backend.data.nba import current_season
from backend.data.honors import AWARDS, DEFAULT_AWARDS

Phase = Literal['Regular Season', 'Playoffs']
Basis = Literal['totals', 'per_game']
METRICS = ['GP', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TOV', 'MIN', 'FGM', 'FGA', 'FG3M', 'FG3A', 'FTM', 'FTA',
           'OREB', 'DREB', 'FG_PCT', 'FG3_PCT', 'FT_PCT', 'TS_PCT', 'EFG_PCT', 'REL_TS']

Metric = Literal[tuple(METRICS)]
GameMetric = Literal[tuple(m for m in METRICS if m not in ('GP', 'REL_TS'))]
Award = Literal[tuple(AWARDS)]
AwardSelection = Literal[('all', *AWARDS)]

class Params(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class DebateConfig(Params):
    scope_mode: Literal['auto', 'configured'] = 'configured'
    supported_player: int = Field(gt=0)
    opponent_player: int = Field(gt=0)
    supported_season: int | None = Field(default=None, ge=1946, le=2100)
    opponent_season: int | None = Field(default=None, ge=1946, le=2100)
    phase: Phase = 'Regular Season'

    @model_validator(mode='after')
    def distinct(self):
        if self.supported_player == self.opponent_player:
            raise ValueError('Choose two different players.')
        if (self.supported_season is None) != (self.opponent_season is None):
            raise ValueError('Choose a season for both players, or career for both.')
        if any(s is not None and s > current_season() for s in (self.supported_season, self.opponent_season)):
            raise ValueError('Future seasons are not available.')
        return self

class Scope(Params):
    start: int | None = Field(default=None, ge=1946, le=2100, description='Inclusive first season START year, e.g. 2017 means 2017-18. Supply both start/end, or omit/null both for career.')
    end: int | None = Field(default=None, ge=1946, le=2100, description='Inclusive last season START year; must be >= start and not in the future. Set equal to start for one season.')
    phase: Phase = Field(default='Regular Season', description='Regular Season or Playoffs, never a mixture. Default: Regular Season.')
    basis: Basis = Field(default='per_game', description='totals sums counting stats; per_game divides by games played. Shooting percentages are recomputed from totals and use 0-100 percentage units; REL_TS uses percentage points.')

    @model_validator(mode='after')
    def range_check(self):
        if (self.start is None) != (self.end is None) or (self.start is not None and self.start > self.end):
            raise ValueError('Supply an ordered start/end pair, or neither for career.')
        if self.end is not None and self.end > current_season():
            raise ValueError('Future seasons are not available.')
        return self

class Resolve(Params):
    query: str = Field(min_length=1, max_length=100, description='Player full name or recognized alias, e.g. LeBron James or MJ. If resolved=false, clarify using returned candidates before using an ID.')

    @field_validator('query')
    @classmethod
    def nonblank_query(cls, value):
        if not value.strip():
            raise ValueError('Supply a player name or alias, e.g. LeBron James or MJ.')
        return value.strip()

class Query(Params):
    player_id: int = Field(gt=0, description='NBA player ID returned by resolve_player; never use an ESPN ID.')
    kind: Literal['stats', 'awards', 'games', 'league'] = Field(default='stats', description='stats: season/career statistics; games: one season game log; league: one season league TS% baseline; awards: raw award records. Prefer player_awards for normalized honor categories.')
    scope: Scope = Field(default_factory=Scope, description='Season range, phase and statistical basis. Omit for career regular-season per-game stats. games/league require start=end with an explicit season START year.')

class Compare(Params):
    left_player: int = Field(gt=0, description='NBA player ID for the left comparison side, from resolve_player. In Debate use the configured supported player.')
    right_player: int = Field(gt=0, description='NBA player ID for the right comparison side, from resolve_player. In Debate use the configured opponent.')
    left_scope: Scope = Field(default_factory=Scope, description='Scope for the left player; defaults to career regular-season per-game stats. Match the right phase, basis and range length.')
    right_scope: Scope = Field(default_factory=Scope, description='Scope for the right player; defaults to career regular-season per-game stats. Match the left phase, basis and range length.')
    metrics: list[Metric] = Field(default_factory=lambda: ['PTS', 'REB', 'AST', 'TS_PCT'], min_length=1, max_length=12, description='Exact uppercase metric codes to compare; default PTS, REB, AST, TS_PCT. Percentages use 0-100 units; REL_TS uses percentage points.')

    @model_validator(mode='after')
    def comparable(self):
        a, b = self.left_scope, self.right_scope
        if self.left_player == self.right_player:
            raise ValueError('Comparison requires different players.')
        if a.phase != b.phase or a.basis != b.basis:
            raise ValueError('Both players must use the same phase and statistical basis.')
        if (a.start is None) != (b.start is None) or (a.start is not None and a.end-a.start != b.end-b.start):
            raise ValueError('Compare career to career or equally long season ranges.')
        if any(m not in METRICS for m in self.metrics):
            raise ValueError('Unsupported metric. Available: ' + ', '.join(METRICS))
        return self

class Counterexample(Params):
    player_id: int = Field(gt=0, description='NBA player ID returned by resolve_player; never use an ESPN ID.')
    scope: Scope = Field(description='One explicit season: set start=end to its START year and choose a phase. Counting-stat predicates use game totals regardless of basis.')
    metric: GameMetric = Field(description='Single-game uppercase metric code. GP and REL_TS are unsupported. Shooting percentages use 0-100 units.')
    comparison: Literal['gt', 'gte', 'lt', 'lte', 'eq'] = Field(description='Numeric predicate: gt >, gte >=, lt <, lte <=, eq ==. Applied to each returned game.')
    threshold: float = Field(description='Finite numeric cutoff in the metric unit. Example: PTS >= 30 uses 30; TS_PCT >= 60% uses 60, not 0.60. Counting stats use single-game totals.')

class PlayerAwards(Params):
    player_id: int = Field(gt=0, description='NBA player ID returned by resolve_player; never use an ESPN ID.')
    scope: Scope = Field(default_factory=lambda: Scope(basis='totals'), description='Inclusive award-season range, with start/end as season START years. Omit for career honors. Counts ignore phase and basis.')
    award: AwardSelection = Field(default='all', description='Use all for every supported honor or one uppercase code, e.g. MVP or CHAMPIONSHIPS. Missing categories are unknown, not zero.')

    @model_validator(mode='after')
    def known_award(self):
        if self.award != 'all' and self.award not in AWARDS:
            raise ValueError('Use all or an award code: ' + ', '.join(AWARDS))
        return self

class CompareAwards(Params):
    left_player: int = Field(gt=0, description='NBA player ID for the left comparison side, from resolve_player. In Debate use the configured supported player.')
    right_player: int = Field(gt=0, description='NBA player ID for the right comparison side, from resolve_player. In Debate use the configured opponent.')
    left_scope: Scope = Field(default_factory=lambda: Scope(basis='totals'), description='Inclusive award-season range for the left player; omit for career. Both sides must be career or equally long ranges; phase/basis are ignored.')
    right_scope: Scope = Field(default_factory=lambda: Scope(basis='totals'), description='Inclusive award-season range for the right player; omit for career. Both sides must be career or equally long ranges; phase/basis are ignored.')
    awards: list[Award] = Field(default_factory=lambda: list(DEFAULT_AWARDS), min_length=1, max_length=15, description='Uppercase honor codes to compare. Defaults to CHAMPIONSHIPS, MVP, FINALS_MVP, DPOY, ALL_NBA, ALL_DEFENSIVE, ALL_STAR.')

    @model_validator(mode='after')
    def comparable(self):
        # Honors are counted by award season, independent of a box-score phase.
        Compare(left_player=self.left_player, right_player=self.right_player,
                left_scope=self.left_scope.model_copy(update={'phase':'Regular Season','basis':'totals'}),
                right_scope=self.right_scope.model_copy(update={'phase':'Regular Season','basis':'totals'}))
        if any(code not in AWARDS for code in self.awards):
            raise ValueError('Unsupported award code. Available: ' + ', '.join(AWARDS))
        return self

class Edges(Params):
    left_player: int = Field(gt=0, description='NBA player ID for the left comparison side, from resolve_player. In Debate use the configured supported player.')
    right_player: int = Field(gt=0, description='NBA player ID for the right comparison side, from resolve_player. In Debate use the configured opponent.')
    left_scope: Scope = Field(default_factory=Scope, description='Scope for the left player; defaults to career regular-season per-game stats. Match the right phase, basis and range length.')
    right_scope: Scope = Field(default_factory=Scope, description='Scope for the right player; defaults to career regular-season per-game stats. Match the left phase, basis and range length.')
    dimension: Literal['scoring', 'playmaking', 'rebounding', 'defense_boxscore', 'all'] = Field(default='all', description='Metric group to inspect for the left player; all covers scoring, playmaking, rebounding and box-score defense. Advantages do not establish an overall winner.')

class Claim(Params):
    evidence_id: str = Field(description='Exact ev_ ID from a prior result in this conversation. For two-player relations use the containing comparison ID, not a child ID.')
    metric: str = Field(description='Exact uppercase metric code returned by evidence tools; use the same units as the returned value.')
    player_id: int = Field(description='NBA player ID returned by resolve_player; never use an ESPN ID.')
    scope: Scope = Field(description="Copy the exact scope object from the cited player evidence; do not replace it with the question's broader scope.")
    value: float | None = Field(description='Exact unrounded metric value from the cited evidence, or null only when that value is unavailable. Percentages use 0-100 units.')
    other_value: float | None = Field(default=None, description='Exact unrounded value for the other player in the cited comparison. Omit/null for individual evidence.')
    relation: Literal['higher', 'lower', 'equal'] | None = Field(default=None, description='Direction of value versus other_value: higher, lower or equal. Omit/null for individual evidence; lower is not automatically worse.')

ContextDimension = Literal['teammate_support', 'individual_role']

class CompetitiveContext(Params):
    selection_reason: str = Field(min_length=1, max_length=600, description='Explain why this season/phase or comparison answers the current question. Limit conclusions to the selected sample.')
    player_id: int = Field(gt=0, description='NBA player ID returned by resolve_player; never use an ESPN ID.')
    season: int = Field(ge=1946, le=2100, description='Season START year, e.g. 2017 means 2017-18 including playoffs in calendar 2018. Future seasons are unavailable.')
    phase: Phase = Field(default='Regular Season', description='Regular Season or Playoffs, never a mixture. Default: Regular Season.')
    team_id: int | None = Field(default=None, gt=0, description='Optional NBA team ID from verified season/stint results, not an ESPN ID. Supply when a traded season needs one team selected.')
    teammate_id: int | None = Field(default=None, gt=0, description='Optional verified non-focal teammate to inspect instead of the broad shortlist.')
    dimensions: list[ContextDimension] = Field(default_factory=lambda:['teammate_support','individual_role'], min_length=1, max_length=2, description='teammate_support returns teammate evidence; individual_role returns player shares/ranks/usage. Default includes both; box-score context does not prove causation.')

    @model_validator(mode='after')
    def available_season(self):
        if self.season > current_season():
            raise ValueError('Future seasons are not available.')
        if self.teammate_id is not None and 'teammate_support' not in self.dimensions:
            raise ValueError('teammate_id requires the teammate_support dimension.')
        return self

class CompareContext(Params):
    focus: Literal['key_teammates', 'team_context'] = Field(default='key_teammates', description='key_teammates discovers rosters then compares selected teammates; team_context compares broad role/support context.')
    selection_reason: str = Field(min_length=1, max_length=600, description='Explain why this season/phase or comparison answers the current question. Limit conclusions to the selected sample.')
    left_player: int = Field(gt=0, description='NBA player ID for the left comparison side, from resolve_player. In Debate use the configured supported player.')
    right_player: int = Field(gt=0, description='NBA player ID for the right comparison side, from resolve_player. In Debate use the configured opponent.')
    left_season: int = Field(ge=1946, le=2100, description='Season START year: 2017 means 2017-18, including playoffs in calendar 2018.')
    right_season: int = Field(ge=1946, le=2100, description='Season START year: 2017 means 2017-18, including playoffs in calendar 2018.')
    phase: Phase = Field(default='Regular Season', description='Regular Season or Playoffs, never a mixture. Default: Regular Season.')
    left_team_id: int | None = Field(default=None, gt=0, description="Optional verified NBA team ID selecting the left player's team stint in a traded season.")
    right_team_id: int | None = Field(default=None, gt=0, description="Optional verified NBA team ID selecting the right player's team stint in a traded season.")
    left_teammate_id: int | None = Field(default=None, gt=0, description='NBA teammate ID from left roster discovery, excluding the focal player. Omit both teammate IDs for discovery; supply both for comparison.')
    right_teammate_id: int | None = Field(default=None, gt=0, description='NBA teammate ID from right roster discovery, excluding the focal player. Omit both teammate IDs for discovery; supply both for comparison.')
    teammate_selection_reason: str = Field(default='', max_length=1000, description='Explain why EACH is a representative strongest co-star using the same criteria and season evidence. Name the main alternatives and why the nominees fit better. Do not choose two lesser role players merely because their roles match.')
    dimensions: list[ContextDimension] = Field(default_factory=lambda:['teammate_support','individual_role'], min_length=1, max_length=2, description='teammate_support returns teammate evidence; individual_role returns player shares/ranks/usage. Default includes both; box-score context does not prove causation.')

    @model_validator(mode='after')
    def distinct_and_available(self):
        if self.left_player == self.right_player:
            raise ValueError('Choose two different players.')
        if max(self.left_season, self.right_season) > current_season():
            raise ValueError('Future seasons are not available.')
        if (self.left_teammate_id is None) != (self.right_teammate_id is None):
            raise ValueError('Choose a teammate for BOTH sides, or neither to discover candidates.')
        if self.left_teammate_id is not None:
            if self.focus != 'key_teammates' or not self.teammate_selection_reason.strip():
                raise ValueError('Selected teammates require focus=key_teammates and teammate_selection_reason.')
            if self.left_teammate_id == self.left_player or self.right_teammate_id == self.right_player:
                raise ValueError('Exclude each focal player from their own teammate candidates.')
        return self

class PerformanceContext(Params):
    player_id: int = Field(gt=0, description='NBA player ID returned by resolve_player; never use an ESPN ID.')
    season: int = Field(ge=1946, le=2100, description='Season START year, e.g. 2017 means 2017-18 including playoffs in calendar 2018. Future seasons are unavailable.')
    phase: Phase = Field(default='Regular Season', description='Regular Season or Playoffs, never a mixture. Default: Regular Season.')
    dimension: Literal['player_ranks', 'team_offense', 'opponent_splits'] = Field(description='player_ranks: per-game scoring/assist ranks; team_offense: team offensive rating/rank; opponent_splits: stats against top regular-season defenses. Last two require 1996+.')
    selection_reason: str = Field(min_length=1, max_length=600, description='Explain why this season/phase or comparison answers the current question. Limit conclusions to the selected sample.')
    team_id: int | None = Field(default=None, gt=0, description='Only for team_offense; select a verified stint if traded.')
    min_games: int = Field(default=20, ge=1, le=82, description='player_ranks eligibility, not official award qualification. State this threshold.')
    top_n: int = Field(default=5, ge=1, le=10, description='opponent_splits: lowest regular-season team defensive ratings, including ties.')

    @model_validator(mode='after')
    def valid_scope(self):
        if self.season > current_season():
            raise ValueError('Future seasons are not available.')
        if self.team_id is not None and self.dimension != 'team_offense':
            raise ValueError('team_id applies only to team_offense, not player ranks or opponent splits.')
        return self

class Audit(Params):
    claims: list[Claim] = Field(max_length=8, description='Up to eight numeric claims to check against saved evidence; use exact evidence IDs, player IDs, values, metric codes and scopes.')

