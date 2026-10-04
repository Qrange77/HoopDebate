"""Shared debate parameters and validation, independent of tool execution."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.data.nba import current_season
from backend.data.honors import AWARDS, DEFAULT_AWARDS

Phase = Literal['Regular Season', 'Playoffs']
Basis = Literal['totals', 'per_game']
METRICS = ['GP', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TOV', 'MIN', 'FGM', 'FGA', 'FG3M', 'FG3A', 'FTM', 'FTA',
           'OREB', 'DREB', 'FG_PCT', 'FG3_PCT', 'FT_PCT', 'TS_PCT', 'EFG_PCT', 'REL_TS']

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
    start: int | None = Field(default=None, ge=1946, le=2100)
    end: int | None = Field(default=None, ge=1946, le=2100)
    phase: Phase = 'Regular Season'
    basis: Basis = 'per_game'

    @model_validator(mode='after')
    def range_check(self):
        if (self.start is None) != (self.end is None) or (self.start is not None and self.start > self.end):
            raise ValueError('Supply an ordered start/end pair, or neither for career.')
        if self.end is not None and self.end > current_season():
            raise ValueError('Future seasons are not available.')
        return self

class Resolve(Params):
    query: str = Field(min_length=1, max_length=100)

class Query(Params):
    player_id: int = Field(gt=0)
    kind: Literal['stats', 'awards', 'games', 'league'] = 'stats'
    scope: Scope = Field(default_factory=Scope)

class Compare(Params):
    left_player: int = Field(gt=0)
    right_player: int = Field(gt=0)
    left_scope: Scope = Field(default_factory=Scope)
    right_scope: Scope = Field(default_factory=Scope)
    metrics: list[str] = Field(default_factory=lambda: ['PTS', 'REB', 'AST', 'TS_PCT'], min_length=1, max_length=12)

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
    player_id: int = Field(gt=0)
    scope: Scope
    metric: str
    comparison: Literal['gt', 'gte', 'lt', 'lte', 'eq']
    threshold: float

class PlayerAwards(Params):
    player_id: int = Field(gt=0)
    scope: Scope = Field(default_factory=lambda: Scope(basis='totals'))
    award: str = 'all'

    @model_validator(mode='after')
    def known_award(self):
        if self.award != 'all' and self.award not in AWARDS:
            raise ValueError('Use all or an award code: ' + ', '.join(AWARDS))
        return self

class CompareAwards(Params):
    left_player: int = Field(gt=0)
    right_player: int = Field(gt=0)
    left_scope: Scope = Field(default_factory=lambda: Scope(basis='totals'))
    right_scope: Scope = Field(default_factory=lambda: Scope(basis='totals'))
    awards: list[str] = Field(default_factory=lambda: list(DEFAULT_AWARDS), min_length=1, max_length=15)

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
    left_player: int = Field(gt=0)
    right_player: int = Field(gt=0)
    left_scope: Scope = Field(default_factory=Scope)
    right_scope: Scope = Field(default_factory=Scope)
    dimension: Literal['scoring', 'playmaking', 'rebounding', 'defense_boxscore', 'all'] = 'all'

class Claim(Params):
    evidence_id: str
    metric: str
    player_id: int
    scope: Scope
    value: float | None
    other_value: float | None = None
    relation: Literal['higher', 'lower', 'equal'] | None = None

ContextDimension = Literal['teammate_support', 'individual_role']

class CompetitiveContext(Params):
    selection_reason: str = Field(min_length=1, max_length=600)
    player_id: int = Field(gt=0)
    season: int = Field(ge=1946, le=2100)
    phase: Phase = 'Regular Season'
    team_id: int | None = Field(default=None, gt=0)
    teammate_id: int | None = Field(default=None, gt=0, description='Optional verified non-focal teammate to inspect instead of the broad shortlist.')
    dimensions: list[ContextDimension] = Field(default_factory=lambda:['teammate_support','individual_role'], min_length=1, max_length=2)

    @model_validator(mode='after')
    def available_season(self):
        if self.season > current_season():
            raise ValueError('Future seasons are not available.')
        if self.teammate_id is not None and 'teammate_support' not in self.dimensions:
            raise ValueError('teammate_id requires the teammate_support dimension.')
        return self

class CompareContext(Params):
    focus: Literal['key_teammates', 'team_context'] = 'key_teammates'
    selection_reason: str = Field(min_length=1, max_length=600)
    left_player: int = Field(gt=0)
    right_player: int = Field(gt=0)
    left_season: int = Field(ge=1946, le=2100, description='Season START year: 2017 means 2017-18, including playoffs in calendar 2018.')
    right_season: int = Field(ge=1946, le=2100, description='Season START year: 2017 means 2017-18, including playoffs in calendar 2018.')
    phase: Phase = 'Regular Season'
    left_team_id: int | None = Field(default=None, gt=0)
    right_team_id: int | None = Field(default=None, gt=0)
    left_teammate_id: int | None = Field(default=None, gt=0)
    right_teammate_id: int | None = Field(default=None, gt=0)
    teammate_selection_reason: str = Field(default='', max_length=1000, description='Explain why EACH is a representative strongest co-star using the same criteria and season evidence. Name the main alternatives and why the nominees fit better. Do not choose two lesser role players merely because their roles match.')
    dimensions: list[ContextDimension] = Field(default_factory=lambda:['teammate_support','individual_role'], min_length=1, max_length=2)

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
    player_id: int = Field(gt=0)
    season: int = Field(ge=1946, le=2100)
    phase: Phase = 'Regular Season'
    dimension: Literal['player_ranks', 'team_offense', 'opponent_splits']
    selection_reason: str = Field(min_length=1, max_length=600)
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
    claims: list[Claim] = Field(max_length=8)

