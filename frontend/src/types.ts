export interface ToolCall { attempt_id?: string; name: string; args: unknown; result?: unknown; id?: string; status?: 'running' | 'completed' | 'failed' | 'needs_evidence' | 'needs_revision' }
export interface Turn { message: string; response: string; tool_calls: ToolCall[]; failed?: boolean; debate_result?: DebateResult | null; activity_phase?: string }
export interface Session { session_id: string; title: string; updated_at: string; mode?: ChatMode }
export interface Conversation { session_id: string; title: string; turns: Turn[]; mode?: ChatMode; debate_config?: DebateConfig | null; player_names?: Record<string, string>; reply_tone?: ReplyTone }
export interface Metric {
  label: string; value: number | null; display_value?: string | null; unit?: string; estimated?: boolean
  unavailable_reason?: string; formula?: string; inputs?: unknown
}
export interface Panel {
  player?: string; team?: string; headshot?: string; position?: string; jersey?: string
  provisional?: boolean; metrics?: Record<string, Metric>; note?: string
}

export type ChatMode = 'assistant' | 'debate' | 'rebuttal'
export type ReplyTone = 'reasoned' | 'roast'
export interface DebateConfig {
  scope_mode?: 'auto' | 'configured'
  supported_player: number; opponent_player: number
  supported_season: number | null; opponent_season: number | null
  phase: 'Regular Season' | 'Playoffs'
}
export interface PlayerChoice {
  id: number; name: string; from_year: string | null; to_year: string | null
  active: boolean; source: string; stale: boolean
}
export interface EvidenceSource {
  provider: string; url: string; endpoint: string; fetched_at: string; stale: boolean; warning?: string
}
export interface EvidenceCard {
  evidence_id: string; title: string; player: string; value: number | null; unit: string; scope: string
  other_player?: string; other_value?: number; other_scope?: string
  games?: number | null; other_games?: number | null; covered_seasons?: string[]
  date?: string; matchup?: string; formula?: string; inputs?: unknown
  sources: EvidenceSource[]; limitations: string[]
  label?: string; award?: boolean; other_covered_seasons?: string[]
  context?: { team_id: number; team_name: string; focal_player_id: number }
  other_context?: { team_id: number; team_name: string; focal_player_id: number }
  team_games?: number | null; other_team_games?: number | null
}
export interface HonorsSummary {
  left_player: string; right_player: string; left_scope: string; right_scope: string
  rows: { metric: string; label: string; left_value: number | null; right_value: number | null; left_seasons: string[]; right_seasons: string[] }[]
  sources: EvidenceSource[]; limitations: string[]
}
export interface StyleAttempt {
  attempt_id: string; attempt: number
  status: 'pending' | 'pass' | 'revise' | 'needs_evidence' | 'format_error' | 'provider_error'
  issues?: ReviewIssue[]; fallback_reason?: string
}
export interface ArgumentPlan {
  objection: string; approach: string; evidence_needed: string[]; scope_reason: string; research_tools: string[]
  discussion_dimension?: string; target_claim?: string; guidance?: string
  team_context_reason?: string; team_context_intent?: 'unrelated' | 'count_only' | 'individual_credit'
  team_success_argument?: boolean
  support_scope?: 'none' | 'strongest_pair' | 'two_pairs'
  teammate_pairs?: Record<string, unknown>[]; replacement_reason?: string
}
export interface SupportResearch {
  support_scope: 'none' | 'strongest_pair' | 'two_pairs'; required_pairs: number
  next_action?: { tool: string; instruction: string; args?: Record<string, unknown> } | null
  tasks: { id: string; status: 'pending' | 'completed' | 'unavailable'; request: Record<string, unknown> | null; evidence_id: string | null; reason: string }[]
  changes: { task_id: string; previous: unknown; reason: string }[]
}
export interface DebateResult {
  argument_plan?: ArgumentPlan | null
  style_attempts?: StyleAttempt[]
  support_research?: SupportResearch
  style_status?: 'not_requested' | 'applied' | 'reasoned_fallback'
  claims: unknown[]; cards: EvidenceCard[]; review_status: 'reviewed' | 'limited'; review_note: string
  honors?: HonorsSummary | null
  research?: ResearchSummary | null
  validation?: ValidationSummary
  comparisons?: StatisticalComparison[]
}

export interface StatisticalComparison {
  kind?: 'key_teammates'
  selection?: { left_focal_name: string; right_focal_name: string; scope_reason: string; teammate_reason: string }
  honors?: { metric: string; label: string; left_value: number | null; right_value: number | null; comparable: boolean }[]
  evidence_id?: string | null; left_player: string; right_player: string; left_scope: string; right_scope: string
  rows: { metric: string; label: string; group: 'basic' | 'efficiency'; left_value: number | null; right_value: number | null
    unit: string; comparable: boolean; left_complete: boolean; right_complete: boolean
    relation: 'higher' | 'lower' | 'equal' | null; difference: number | null }[]
  sources: EvidenceSource[]; limitations: string[]
}

export interface ResearchScope {
  unit_id: string; left_player: number; right_player: number; left_season: string | null; right_season: string | null
  left_team_id: number | null; right_team_id: number | null; phase: string; supplement: boolean
  errors: string[]; evidence_ids: string[]; left_team_name?: string | null; right_team_name?: string | null
}
export interface ResearchSummary {
  directed_queries?: DirectedQuery[]; plans?: ResearchSummary[]
  plan_id: string; argument: string; pairing_rule: string; selection_rule: string
  completed: ResearchScope[]; pending: ResearchScope[]; unavailable: ResearchScope[]
  gaps: string[]; sources: EvidenceSource[]; parent_plan_id?: string | null
}

export interface ReviewIssue { clause: string; reason: string; missing_evidence: string }
export interface ArgumentAssessment {
  relevance: 'addresses_objection' | 'limited_concession' | 'evasive'
  evidence_followthrough: 'complete' | 'not_needed' | 'missing'
  reason: string
  // Older saved turns do not contain the direction assessment.
  target_claim?: string | null
  evidence_direction?: 'supports' | 'opposes' | 'inconclusive' | null
  evidence_ids?: string[] | null
  response_strategy?: 'counterargument' | 'concession' | 'qualified_answer' | null
  conclusion_supported?: boolean | null
  conclusion_addresses_objection?: boolean | null
  objection_faithful?: boolean | null
  advantage_example_count?: number | null
  team_success_argument?: boolean | null
  required_support_scope?: 'none' | 'strongest_pair' | 'two_pairs' | null
  single_pair_user_quote?: string | null
}
export interface ValidationSummary {
  submissions: number; stop_reason: string; numeric_valid: boolean | null; semantic_status: string
  citation_repairs?: number
  novelty_repairs?: number
  reason_code?: 'duplicate_evidence' | null
  failure_type?: 'format' | 'citation' | 'evidence' | 'argument' | null
  attempts: { attempt_id?: string; novelty_repair?: number; reason_code?: 'duplicate_evidence'; submission: number | null; citation_repair?: number; numeric_valid: boolean; semantic_status: string; issues: ReviewIssue[]; argument_assessment?: ArgumentAssessment }[]
  data_gaps: string[]
}
export interface DirectedQuery {
  id: string; tool: string; selection_reason: string; status: string
  request: { player_id?: number; season?: number; left_player?: number; right_player?: number; left_season?: number; right_season?: number; phase: string }
  evidence_ids: string[]; sources: EvidenceSource[]; errors: string[]
}
