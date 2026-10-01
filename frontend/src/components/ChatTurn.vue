<script setup lang="ts">
import { computed } from 'vue'
import { ArrowUpRight, CircleAlert, Terminal, ChevronDown } from 'lucide-vue-next'
import ResultPanel from './ResultPanel.vue'
import { renderMarkdown } from '../markdown'
import type { Panel, Turn } from '../types'
const props = defineProps<{ turn: Turn; pending: boolean }>()
defineEmits<{ retry: [] }>()
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
      <div v-if="pending" class="thinking" role="status"><span class="thinking-dots"><i></i><i></i><i></i></span> Looking into the game<span class="thinking-detail">Checking the details. Larger comparisons can take a moment.</span></div>
      <div v-else-if="turn.failed" class="send-error" role="alert"><CircleAlert :size="18" /><div><strong>Message couldn't be completed</strong><p>{{ turn.response }}</p><button class="text-button" @click="$emit('retry')">Try again <ArrowUpRight :size="15" /></button></div></div>
      <template v-else>
        <details v-if="turn.tool_calls.length" class="tool-log"><summary><Terminal :size="14" /> {{ turn.tool_calls.length }} tool {{ turn.tool_calls.length === 1 ? 'call' : 'calls' }} <ChevronDown :size="14" /></summary><details v-for="(call, index) in turn.tool_calls" :key="index" class="tool-entry"><summary>{{ call.name }}</summary><pre>{{ JSON.stringify(call.args, null, 2) }}
{{ typeof call.result === 'string' ? call.result : JSON.stringify(call.result, null, 2) }}</pre></details></details>
        <div class="answer-content" v-html="renderedResponse"></div>
        <ResultPanel v-for="(panel, index) in panels" :key="index" :panel="panel" />
      </template>
    </article>
  </div>
</template>
