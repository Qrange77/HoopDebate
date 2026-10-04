<script setup lang="ts">
import { computed, ref } from 'vue'
import { Plus, Search, MessageSquare, X, History, RefreshCw, Trash2, LoaderCircle } from 'lucide-vue-next'
import Button from './ui/Button.vue'
import type { Session } from '../types'

const props = defineProps<{
  sessions: Session[]; activeId: string | null; busy: boolean; loading: boolean; error: string; open: boolean
  deletingId: string | null; deleteError: string
}>()
const emit = defineEmits<{ new: []; select: [id: string]; delete: [id: string]; close: []; refresh: [] }>()
const query = ref('')
const groups = computed(() => {
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const week = new Date(today); week.setDate(week.getDate() - 7)
  const buckets = [
    { label: 'Today', records: [] as Session[] },
    { label: 'Previous 7 days', records: [] as Session[] },
    { label: 'Earlier', records: [] as Session[] },
  ]
  for (const item of props.sessions.filter(s => s.title.toLowerCase().includes(query.value.toLowerCase()))) {
    const date = new Date(item.updated_at)
    buckets[date >= today ? 0 : date >= week ? 1 : 2].records.push(item)
  }
  return buckets.filter(group => group.records.length)
})
function startNew() { query.value = ''; emit('new') }
</script>

<template>
  <!-- Sidebar shell and menu utilities adapted from TailAdmin Vue Free. -->
  <aside id="conversation-sidebar" class="sidebar bg-white dark:bg-gray-900 border-e border-gray-200 dark:border-gray-800" :class="{ 'is-open': open }" aria-label="Conversation history">
    <div class="sidebar-brand">
      <img src="/favicon.svg" width="39" height="39" alt="" />
      <div><span class="brand-name">NBA <strong>GAME LAB</strong></span><span class="brand-caption">A NEW ANGLE ON THE GAME</span></div>
      <button class="icon-button mobile-only" aria-label="Close history" @click="emit('close')"><X :size="19" /></button>
    </div>
    <div class="sidebar-actions">
      <Button class="new-chat" :start-icon="Plus" :disabled="busy" :on-click="startNew">New conversation <span class="new-chat-symbol">↗</span></Button>
      <label class="history-search"><Search :size="16" /><input v-model="query" type="search" placeholder="Search conversations" aria-label="Search conversations" /></label>
    </div>
    <div class="history-heading"><span>YOUR CONVERSATIONS</span><span class="history-count">{{ sessions.length }}</span></div>
    <div class="history-scroll">
      <p v-if="deleteError" class="history-delete-error" role="alert">{{ deleteError }}</p>
      <div v-if="error" class="history-empty" role="alert"><p>Couldn't load history.</p><button class="text-button" @click="emit('refresh')"><RefreshCw :size="14" /> Retry</button></div>
      <div v-else-if="loading && !sessions.length" class="history-empty" role="status">Loading conversations…</div>
      <template v-else-if="groups.length">
        <section v-for="group in groups" :key="group.label" class="history-group">
          <h2>{{ group.label }}</h2>
          <div v-for="session in group.records" :key="session.session_id" class="menu-item history-item" :class="session.session_id === activeId ? 'menu-item-active' : 'menu-item-inactive'" :aria-busy="deletingId === session.session_id">
            <button class="history-select" :aria-current="session.session_id === activeId ? 'page' : undefined" :title="session.title" :disabled="busy" @click="emit('select', session.session_id)"><MessageSquare :size="17" /><span><small v-if="session.mode && session.mode !== 'assistant'" class="history-mode">DEBATE · </small>{{ session.title }}</span></button>
            <button class="icon-button history-delete" :aria-label="`Delete conversation: ${session.title}`" :title="deletingId === session.session_id ? 'Deleting…' : 'Delete conversation'" :disabled="busy" @click="emit('delete', session.session_id)"><LoaderCircle v-if="deletingId === session.session_id" :size="16" class="animate-spin" /><Trash2 v-else :size="16" /></button>
          </div>
        </section>
      </template>
      <div v-else class="history-empty"><History :size="26" /><p>{{ query ? 'No matching conversations' : 'Room for your next discovery' }}</p><small>{{ query ? 'Try a different search.' : 'Your conversations will appear here after your first message.' }}</small></div>
    </div>
  </aside>
</template>
