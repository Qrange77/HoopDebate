<script setup lang="ts">
import type { DebateResult } from '../types'
defineProps<{ result: DebateResult }>()
const metricLabels: Record<string, string> = { PTS: 'Points', REB: 'Rebounds', AST: 'Assists', STL: 'Steals', BLK: 'Blocks', TOV: 'Turnovers', GP: 'Games played', MIN: 'Minutes', TS_PCT: 'True shooting', EFG_PCT: 'Effective field goal shooting', FG_PCT: 'Field goal shooting', FG3_PCT: 'Three-point shooting', FT_PCT: 'Free throw shooting', REL_TS: 'True shooting vs. league', FGM: 'Field goals made', FGA: 'Field goal attempts', FG3M: 'Threes made', FG3A: 'Three-point attempts', FTM: 'Free throws made', FTA: 'Free throw attempts', OREB: 'Offensive rebounds', DREB: 'Defensive rebounds' }
function number(value: number | null) { return value != null && Number.isFinite(value) ? value.toLocaleString('en-US', { maximumFractionDigits: 2 }) : 'Unavailable' }
function safeLink(value: string) {
  try { const url = new URL(value); return url.protocol === 'https:' && (url.hostname === 'nba.com' || url.hostname.endsWith('.nba.com')) ? url.href : undefined } catch { return undefined }
}
function date(value: string) { return new Date(value).toLocaleString() }
</script>
<template>
  <section class="evidence-section" aria-label="Supporting evidence">
    <div class="evidence-heading"><strong>The receipts</strong><span>{{ result.review_status === 'limited' ? (result.cards.length ? 'Verified facts only' : 'Reply verification failed') : 'Numbers checked' }}</span></div>
    <p v-if="result.validation?.stop_reason === 'passed'" class="picker-note">Reply approved after {{ result.validation.submissions }} {{ result.validation.submissions === 1 ? 'submission' : 'submissions' }}. Earlier requests for evidence are revision history.</p>
    <section v-for="(comparison, index) in result.comparisons || []" :key="comparison.evidence_id || index" class="honors-panel stats-comparison" aria-label="Player statistics comparison">
      <strong>{{ comparison.kind === 'key_teammates' ? 'Key teammate comparison' : 'Player comparison' }}</strong>
      <template v-if="comparison.selection">
        <p class="honors-scope">{{ comparison.left_player }} supports {{ comparison.selection.left_focal_name }} · {{ comparison.right_player }} supports {{ comparison.selection.right_focal_name }}</p>
        <p class="picker-note">Why these teammates: {{ comparison.selection.teammate_reason }}</p>
        <p class="picker-note">Selected scope: {{ comparison.selection.scope_reason }}</p>
      </template>
      <p class="honors-scope">{{ comparison.left_player }}: {{ comparison.left_scope }}<br />{{ comparison.right_player }}: {{ comparison.right_scope }}</p>
      <table v-for="group in (['basic', 'efficiency'] as const)" :key="group" class="honors-table">
        <caption>{{ group === 'basic' ? 'Basic statistics' : 'Shooting efficiency' }}</caption>
        <thead><tr><th scope="col">Metric</th><th scope="col">{{ comparison.left_player }}</th><th scope="col">{{ comparison.right_player }}</th></tr></thead>
        <tbody><tr v-for="row in comparison.rows.filter(r => r.group === group)" :key="row.metric">
          <th scope="row">{{ row.label }}<small class="comparison-unit">{{ row.unit }}</small><small v-if="!row.comparable" class="comparison-unit">Incomplete / unavailable comparison</small></th>
          <td>{{ number(row.left_value) }}<small v-if="row.left_value != null && !row.left_complete" class="comparison-unit">Partial coverage</small></td>
          <td>{{ number(row.right_value) }}<small v-if="row.right_value != null && !row.right_complete" class="comparison-unit">Partial coverage</small></td>
        </tr></tbody>
      </table>
      <p v-if="comparison.kind === 'key_teammates'" class="picker-note">Two selected teammates, not an overall roster ranking.</p>
      <details><summary>Sources & comparison limits</summary>
        <p v-for="note in comparison.limitations" :key="note">{{ note }}</p>
        <div v-for="(source, i) in comparison.sources" :key="i" class="evidence-source"><a :href="safeLink(source.url)" target="_blank" rel="noopener noreferrer">{{ source.provider }} ↗</a><span>Retrieved {{ date(source.fetched_at) }}</span><strong v-if="source.stale">Expired cache · refresh unavailable</strong></div>
      </details>
    </section>
    <details v-if="result.research" class="honors-panel research-panel">
      <summary><strong>Research coverage</strong><span>{{ result.research.completed.length }} queue units checked · {{ result.research.pending.length }} pending · {{ result.research.directed_queries?.length || 0 }} directed lookups</span></summary>
      <p>{{ result.research.argument.replaceAll('_', ' ') }} · {{ result.research.pairing_rule }}</p>
      <p>{{ result.research.selection_rule }}</p>
      <p>Checked units are season/team pairs in one phase. They do not represent an entire career. Missing participation does not establish injury or team nonqualification.</p>
      <template v-for="group in (['completed', 'pending', 'unavailable'] as const)" :key="group">
        <details v-if="result.research[group].length"><summary>{{ group }} ({{ result.research[group].length }})</summary>
          <ul><li v-for="unit in result.research[group]" :key="unit.unit_id">
            {{ result.honors?.left_player || unit.left_player }}: {{ unit.left_season || 'No corresponding season' }} ({{ unit.left_team_name || (unit.left_team_id ? 'Team ' + unit.left_team_id : 'Team unavailable') }}) vs.
            {{ result.honors?.right_player || unit.right_player }}: {{ unit.right_season || 'No corresponding season' }} ({{ unit.right_team_name || (unit.right_team_id ? 'Team ' + unit.right_team_id : 'Team unavailable') }}) · {{ unit.phase }}
            <span v-if="unit.supplement"> · Separate regular-season supplement</span>
            <p v-for="error in unit.errors" :key="error">{{ error }}</p>
          </li></ul>
        </details>
      </template>
      <p v-for="gap in result.research.gaps" :key="gap">{{ gap }}</p>
      <p v-if="result.research.pending.length">The remaining queue is saved. Further investigation depends on what your next question needs.</p>
      <p v-if="result.research.parent_plan_id">The earlier investigation queue remains saved and can be resumed.</p>
      <details v-if="result.research.directed_queries?.length" open><summary>Model-selected scopes</summary>
        <article v-for="query in result.research.directed_queries" :key="query.id" class="honors-seasons">
          <strong>{{ query.request.season ?? query.request.left_season }}<template v-if="query.request.right_season"> / {{ query.request.right_season }}</template> · {{ query.request.phase }} · {{ query.status }}</strong>
          <p>Selection reason: {{ query.selection_reason }}</p>
          <p v-for="error in query.errors" :key="error">{{ error }}</p>
          <div v-for="(source, i) in query.sources" :key="i" class="evidence-source"><a :href="safeLink(source.url)" target="_blank" rel="noopener noreferrer">{{ source.provider }} ↗</a><span>Retrieved {{ date(source.fetched_at) }}</span><strong v-if="source.stale">Expired cache · refresh unavailable</strong></div>
        </article>
      </details>
      <details v-if="(result.research.plans?.length || 0) > 1"><summary>Other saved queues</summary>
        <p v-for="plan in result.research.plans?.filter(p => p.plan_id !== result.research?.plan_id)" :key="plan.plan_id">{{ plan.argument }} · {{ plan.completed.length }} checked · {{ plan.pending.length }} pending · {{ plan.unavailable.length }} unavailable. {{ plan.selection_rule }}</p>
      </details>
      <details><summary>Selection sources</summary><div v-for="(source, i) in result.research.sources" :key="i" class="evidence-source"><a :href="safeLink(source.url)" target="_blank" rel="noopener noreferrer">{{ source.provider }} ↗</a><span>Retrieved {{ date(source.fetched_at) }}</span><strong v-if="source.stale">Expired cache · refresh unavailable</strong></div></details>
    </details>
    <details v-if="result.honors" class="honors-panel" :open="result.cards.some(card => card.award)">
      <summary><strong>Trophy cabinet</strong><span>{{ result.honors.left_player }} vs. {{ result.honors.right_player }}</span></summary>
      <p class="honors-scope">{{ result.honors.left_player }}: {{ result.honors.left_scope }}<br />{{ result.honors.right_player }}: {{ result.honors.right_scope }}</p>
      <table class="honors-table"><caption class="sr-only">Verified NBA honors by award season</caption><thead><tr><th scope="col">Honor</th><th scope="col">{{ result.honors.left_player }}</th><th scope="col">{{ result.honors.right_player }}</th></tr></thead><tbody><tr v-for="row in result.honors.rows" :key="row.metric"><th scope="row">{{ row.label }}</th><td>{{ row.left_value == null ? '—' : row.left_value }}</td><td>{{ row.right_value == null ? '—' : row.right_value }}</td></tr></tbody></table>
      <p class="honors-note">— means no matching source records, not zero. Championships are team achievements.</p>
      <details class="honors-sources"><summary>Winning seasons & sources</summary><div v-for="row in result.honors.rows" :key="row.metric" class="honors-seasons"><strong>{{ row.label }}</strong><p>{{ result.honors.left_player }}: {{ row.left_seasons.join(', ') || 'Unavailable' }}</p><p>{{ result.honors.right_player }}: {{ row.right_seasons.join(', ') || 'Unavailable' }}</p></div><div v-for="(source, i) in result.honors.sources" :key="i" class="evidence-source"><a :href="safeLink(source.url)" target="_blank" rel="noopener noreferrer">{{ source.provider }} ↗</a><span>Retrieved {{ date(source.fetched_at) }}</span><strong v-if="source.stale">Expired cache · refresh unavailable</strong></div><p v-for="note in result.honors.limitations" :key="note">{{ note }}</p></details>
      <span v-if="result.honors.sources.some(s => s.stale)" class="stale-badge">Using older cached data</span>
    </details>
    <p v-if="!result.cards.length" class="picker-note">No verified evidence cards are available for this reply.</p>
    <div class="evidence-grid"><article v-for="(card, i) in result.cards" :key="`${card.evidence_id}-${card.title}-${i}`" class="evidence-card">
      <span v-if="card.award" class="honor-badge">NBA HONOR</span>
      <span v-else-if="card.context" class="honor-badge">TEAM CONTEXT</span>
      <h3>{{ card.label || metricLabels[card.title] || card.title }} <small>{{ card.unit }}</small></h3>
      <div class="evidence-value"><span>{{ card.player }}</span><strong>{{ number(card.value) }}</strong></div><p>{{ card.scope }}<span v-if="card.games != null"> · {{ number(card.games) }} games</span></p>
      <template v-if="card.other_player && card.other_value != null"><div class="evidence-value other"><span>{{ card.other_player }}</span><strong>{{ number(card.other_value) }}</strong></div><p>{{ card.other_scope }}<span v-if="card.other_games != null"> · {{ number(card.other_games) }} games</span></p></template>
      <p v-if="card.date">{{ card.date }} · {{ card.matchup }}</p>
      <p v-if="card.team_games != null">Team sample: {{ card.team_games }} games<span v-if="card.other_team_games != null"> · Other team: {{ card.other_team_games }} games</span>. Team shares include games the player missed.</p>
      <details><summary>Sources & coverage</summary><p v-if="card.covered_seasons?.length">{{ card.award ? card.player + ' · winning seasons' : 'Covered seasons' }}: {{ card.covered_seasons.join(', ') }}</p><p v-if="card.other_covered_seasons?.length">{{ card.other_player }} · winning seasons: {{ card.other_covered_seasons.join(', ') }}</p><p v-for="note in card.limitations" :key="note">{{ note }}</p><p v-if="card.formula">{{ card.formula }}</p><pre v-if="card.inputs">{{ JSON.stringify(card.inputs, null, 2) }}</pre><div v-for="(source, index) in card.sources" :key="index" class="evidence-source"><a :href="safeLink(source.url)" target="_blank" rel="noopener noreferrer">{{ source.provider }} ↗</a><span>Retrieved {{ date(source.fetched_at) }}</span><strong v-if="source.stale">Expired cache · refresh unavailable</strong></div></details>
      <span v-if="card.sources.some(s => s.stale)" class="stale-badge">Using older cached data</span>
    </article></div>
    <details v-if="result.validation" class="honors-panel">
      <summary><strong>Reply checks</strong><span>{{ result.validation.submissions }} draft submissions · {{ result.validation.semantic_status.replaceAll('_', ' ') }}</span></summary>
      <p>Latest draft — data consistency: {{ result.validation.numeric_valid === null ? 'Not checked' : result.validation.numeric_valid ? 'Passed' : 'Needs correction' }}. Wording review: {{ result.validation.semantic_status.replaceAll('_', ' ') }}.</p>
      <p v-if="result.validation.stop_reason !== 'passed'">Stopped: {{ result.validation.stop_reason.replaceAll('_', ' ') }}. An unapproved draft is never published.</p>
      <details v-for="(attempt, index) in result.validation.attempts" :key="index"><summary>{{ attempt.citation_repair ? `Citation repair ${attempt.citation_repair}` : `Draft ${attempt.submission}` }} · data {{ attempt.numeric_valid ? 'passed' : 'needs correction' }}<template v-if="!attempt.citation_repair"> · wording {{ attempt.semantic_status.replaceAll('_', ' ') }}</template></summary>
        <div v-for="(issue, i) in attempt.issues" :key="i"><blockquote v-if="issue.clause">{{ issue.clause }}</blockquote><p>{{ issue.reason }}</p><p v-if="issue.missing_evidence">Missing evidence: {{ issue.missing_evidence }}</p></div>
      </details>
      <p v-for="gap in result.validation.data_gaps" :key="gap">{{ gap }}</p>
    </details>
    <p class="evidence-disclaimer">{{ result.review_note }}</p>
  </section>
</template>
