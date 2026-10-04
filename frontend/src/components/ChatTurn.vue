<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowUpRight, CircleAlert, Terminal, ChevronDown } from 'lucide-vue-next'
import ResultPanel from './ResultPanel.vue'
import EvidenceCards from './EvidenceCards.vue'
import { renderMarkdown } from '../markdown'
import type { Panel, Turn, ChatMode, ToolCall } from '../types'
const props = defineProps<{ turn: Turn; pending: boolean; mode?: ChatMode }>()
const thinkingOpen = ref(props.pending)
function toggleThinking(event: Event) { thinkingOpen.value = (event.target as HTMLDetailsElement).open }
const toolLabels: Record<string, string> = {
  plan_argument: 'Planning the counterpoint',
  style_argument: 'Adding the roast', review_style: 'Checking the styled reply',
  repair_submission: 'Repairing reply format',
  compare_awards: 'Comparing honors', player_awards: 'Looking up honors', resolve_player: 'Identifying a player',
  query_performance_context: 'Checking season ranks, team offense and opponent performance',
  query_evidence: 'Retrieving evidence', compare_players: 'Comparing player statistics',
  plan_team_context_research: 'Selecting relevant seasons', research_team_context: 'Investigating both supporting casts',
  query_competitive_context: 'Checking teammates and roles', compare_competitive_context: 'Comparing key teammates and support',
  submit_argument: 'Submitting reply', audit_argument: 'Checking factual claims', review_argument: 'Reviewing reply wording',
  find_counterexamples: 'Searching for counterexamples', find_comparative_edges: 'Comparing strengths and weaknesses',
  resolve_evidence_references: 'Matching evidence references', validate_response: 'Checking response format',
  verified_context_fallback: 'Preparing verified team facts', verified_honors_fallback: 'Preparing verified honors',
}
defineEmits<{ retry: [] }>()
const copyStatus = ref('')
const failureNotice = computed(() => {
  const validation = props.turn.debate_result?.validation
  // Old saved turns predate failure_type; distinguish schema failures from missing evidence there too.
  const last = validation?.attempts?.at(-1)
  const legacyFormat = last?.semantic_status === 'not_run' && last.issues.some(issue =>
    /validation error for Draft|Invalid structured tool arguments/.test(issue.reason))
  const kind = validation?.failure_type || (legacyFormat ? 'format' : undefined)
  if (kind === 'format') return { title: 'Reply format could not be repaired', text: 'The model could not submit a complete reply in the required format. Wording review was not reached for the last attempt. Any verified evidence remains below.' }
  if (kind === 'citation') return { title: 'Reply citations could not be verified', text: 'The submitted references or claim values did not match the evidence. Any verified evidence remains below.' }
  if (kind === 'argument') return { title: 'Argument needs revision', text: 'The reply did not resolve the reasoning or relevance issues raised in review. Any verified evidence remains below.' }
  if (kind === 'evidence') return { title: 'Reply needs more evidence', text: 'Some assertions still lack sufficient support. Any verified evidence remains below; it is not an approved comeback.' }
  return { title: 'Reply not approved', text: 'The reply could not complete verification within this turn’s limits. See the activity for details. Any verified evidence remains below.' }
})
function activityState(call: ToolCall) {
  // Older saved turns labeled recoverable review feedback as a failed tool call.
  if (call.name === 'review_argument' || call.name === 'submit_argument' || call.name === 'review_style') {
    try {
      const result = typeof call.result === 'string' ? JSON.parse(call.result) : call.result
      if (result?.status === 'needs_evidence') return 'needs_evidence'
      if (result?.status === 'revise') return 'needs_revision'
    } catch { /* Keep the execution status if the result is not JSON. */ }
  }
  return call.status || 'completed'
}
function activityLabel(call: ToolCall) {
  try {
    const result = typeof call.result === 'string' ? JSON.parse(call.result) : call.result
    if (result?.failure_type === 'format') return 'Format needs repair'
    if (result?.failure_type === 'citation') return 'Citations need repair'
  } catch { /* Fall back to the execution status. */ }
  return { running: 'Running', completed: 'Completed', failed: 'Failed', needs_evidence: 'Needs evidence', needs_revision: 'Needs revision' }[activityState(call)]
}
async function copyReply() {
  try { await navigator.clipboard.writeText(props.turn.response); copyStatus.value = 'Copied' }
  catch { copyStatus.value = 'Copy unavailable. Select the reply to copy it.' }
}
const renderedResponse = computed(() => renderMarkdown(props.turn.response))
const panels = computed<Panel[]>(() => props.turn.tool_calls.flatMap(call => {
  if (call.name !== 'display_panel') return []
  try {
    const result = typeof call.result === 'string' ? JSON.parse(call.result) : call.result
    if (!result || result.error || result.message || (!result.player && !result.team)) return []
    return [result as Panel]
  } catch { return [] }
}))
</script>

<template>
  <div class="chat-turn">
    <div class="user-message"><span class="message-label">YOU</span><div class="user-bubble">{{ turn.message }}</div></div>
    <article class="assistant-message">
      <div class="assistant-heading"><img src="/favicon.svg" width="27" height="27" alt="" /><strong>Game Lab</strong><span>ASSISTANT</span></div>
      <details v-if="pending || turn.tool_calls.length" class="tool-log thinking-log" :open="thinkingOpen" @toggle="toggleThinking">
        <summary><Terminal :size="14" /><strong>Thinking</strong><span v-if="turn.tool_calls.length">· {{ turn.tool_calls.length }} tool {{ turn.tool_calls.length === 1 ? 'call' : 'calls' }}</span><span v-if="pending" class="activity-dot running" aria-label="In progress"></span><ChevronDown :size="14" /></summary>
        <p v-if="pending" class="thinking" role="status">{{ turn.activity_phase || 'Preparing your request' }}</p>
        <p v-if="pending && !turn.tool_calls.length" class="thinking-detail">Tool activity will appear here as it happens.</p>
        <details v-for="(call, index) in turn.tool_calls" :key="call.id || index" class="tool-entry">
          <summary><span class="activity-dot" :class="activityState(call)"></span><span>{{ toolLabels[call.name] || call.name.replaceAll('_', ' ') }}<small class="activity-name">{{ call.name }}</small></span><span class="activity-status">{{ activityLabel(call) }}</span></summary>
          <strong class="activity-section-label">Input</strong><pre>{{ JSON.stringify(call.args, null, 2) }}</pre>
          <template v-if="call.result !== undefined"><strong class="activity-section-label">Result</strong><pre>{{ typeof call.result === 'string' ? call.result : JSON.stringify(call.result, null, 2) }}</pre></template>
          <p v-else class="thinking-detail">Waiting for the tool…</p>
        </details>
      </details>
      <div v-if="!pending && turn.failed" class="send-error" role="alert"><CircleAlert :size="18" /><div><strong>Message couldn't be completed</strong><p>{{ turn.response }}</p><button class="text-button" @click="$emit('retry')">Try again <ArrowUpRight :size="15" /></button></div></div>
      <template v-else-if="!pending">
        <div v-if="turn.debate_result?.review_status === 'limited'" class="review-notice" role="status"><strong>{{ failureNotice.title }}</strong><p>{{ failureNotice.text }}</p></div>
        <template v-else><p v-if="turn.debate_result?.style_status === 'reasoned_fallback'" class="review-notice" role="status">The roast could not be verified. Showing the approved, reasoned reply.</p><div class="answer-content" v-html="renderedResponse"></div></template>
        <button v-if="mode !== 'assistant' && turn.debate_result?.review_status !== 'limited'" type="button" class="copy-comeback" @click="copyReply">Copy reply</button><span v-if="copyStatus" class="copy-status" role="status">{{ copyStatus }}</span>
        <EvidenceCards v-if="turn.debate_result" :result="turn.debate_result" />
        <ResultPanel v-for="(panel, index) in panels" :key="index" :panel="panel" />
      </template>
    </article>
  </div>
</template>
