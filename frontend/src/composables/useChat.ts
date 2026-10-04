import { computed, ref } from 'vue'
import type { Conversation, Session, Turn, ChatMode, DebateConfig, DebateResult, ToolCall, ReplyTone } from '../types'

type ChatResult = { session_id: string; response: string; tool_calls: ToolCall[]; debate_result?: DebateResult; failed?: boolean }
type ChatEvent = { type: 'phase'; label: string } | { type: 'session'; session_id: string } |
  { type: 'tool_start' | 'tool_end'; call: ToolCall } | { type: 'done'; data: ChatResult } |
  { type: 'error'; message: string }

async function streamChat(body: unknown, onEvent: (event: ChatEvent) => void): Promise<ChatResult> {
  const response = await fetch('/chat/stream', {
    method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body: JSON.stringify(body),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => null)
    throw new Error(typeof error?.detail === 'string' ? error.detail : `Request failed (${response.status})`)
  }
  if (!response.body) throw new Error('Live updates are unavailable in this browser.')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      let boundary: number
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const data = block.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
        if (!data) continue
        const event = JSON.parse(data) as ChatEvent
        if (event.type === 'error') throw new Error(event.message)
        onEvent(event)
        if (event.type === 'done') return event.data
      }
      if (done) throw new Error('Live connection ended before the reply completed. The conversation may still finish saving; check history before retrying.')
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options)
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `Request failed (${response.status})`)
  if (data === null) throw new Error('The server returned an unreadable response.')
  return data as T
}

export function useChat() {
  const mode = ref<ChatMode>('assistant')
  const replyTone = ref<ReplyTone>('reasoned')
  const debateConfig = ref<DebateConfig | null>(null)
  const playerNames = ref<Record<string, string>>({})
  const sessions = ref<Session[]>([])
  const turns = ref<Turn[]>([])
  const sessionId = ref<string | null>(null)
  const title = ref('New conversation')
  const sending = ref(false)
  const loadingSession = ref(false)
  const loadingHistory = ref(false)
  const deletingId = ref<string | null>(null)
  const deleteError = ref('')
  const historyError = ref('')
  const error = ref('')
  // The initial history request establishes the browser's ownership cookie.
  const busy = computed(() => sending.value || loadingSession.value || loadingHistory.value || deletingId.value !== null)

  async function refreshHistory() {
    loadingHistory.value = true
    historyError.value = ''
    try { sessions.value = await request<Session[]>('/sessions') }
    catch (e) { historyError.value = e instanceof Error ? e.message : 'Unable to load history.' }
    finally { loadingHistory.value = false }
  }

  function newChat() {
    if (busy.value) return
    mode.value = 'assistant'
    replyTone.value = 'reasoned'
    debateConfig.value = null
    playerNames.value = {}
    sessionId.value = null
    turns.value = []
    title.value = 'New conversation'
    error.value = ''
  }

  async function openSession(id: string) {
    if (busy.value) return false
    loadingSession.value = true
    error.value = ''
    try {
      const record = await request<Conversation>(`/sessions/${encodeURIComponent(id)}`)
      mode.value = record.mode || 'assistant'
      replyTone.value = record.reply_tone || 'reasoned'
      debateConfig.value = record.debate_config || null
      playerNames.value = record.player_names || {}
      sessionId.value = record.session_id
      title.value = record.title
      turns.value = record.turns
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Unable to open this conversation.'
      return false
    } finally { loadingSession.value = false }
  }

  async function deleteSession(id: string) {
    if (busy.value) return false
    deletingId.value = id
    deleteError.value = ''
    try {
      await request(`/clear?session_id=${encodeURIComponent(id)}`, { method: 'POST' })
      sessions.value = sessions.value.filter(session => session.session_id !== id)
      if (sessionId.value === id) {
        sessionId.value = null
        turns.value = []
        title.value = 'New conversation'
        mode.value = 'assistant'
        replyTone.value = 'reasoned'
        debateConfig.value = null
        playerNames.value = {}
        error.value = ''
      }
      return true
    } catch (e) {
      deleteError.value = `Couldn't delete conversation. ${e instanceof Error ? e.message : 'Please try again.'}`
      return false
    } finally { deletingId.value = null }
  }

  async function send(message: string) {
    if (busy.value || !message.trim()) return
    sending.value = true
    error.value = ''
    const turn: Turn = { message: message.trim(), response: '', tool_calls: [] }
    turns.value.push(turn)
    const index = turns.value.length - 1
    try {
      const data = await streamChat({ message: turn.message, session_id: sessionId.value, mode: mode.value, ...(mode.value !== 'assistant' ? { debate_config: debateConfig.value, reply_tone: replyTone.value } : {}) }, event => {
        const active = turns.value[index]!
        if (event.type === 'phase') active.activity_phase = event.label
        if (event.type === 'session') sessionId.value = event.session_id
        if (event.type === 'tool_start' || event.type === 'tool_end') {
          const found = active.tool_calls.findIndex(call => call.id === event.call.id)
          if (found < 0) active.tool_calls.push(event.call)
          else active.tool_calls[found] = event.call
        }
      })
      sessionId.value = data.session_id
      if (title.value === 'New conversation') title.value = turn.message.slice(0, 80)
      turns.value[index] = { ...turn, response: data.response, tool_calls: data.tool_calls, debate_result: data.debate_result, failed: data.failed }
      await refreshHistory()
    } catch (e) {
      const active = turns.value[index]!
      turns.value[index] = { ...active, tool_calls: active.tool_calls.map(call => call.status === 'running' ? { ...call, status: 'failed', result: 'Live updates stopped before completion was confirmed.' } : call), failed: true, response: e instanceof Error ? e.message : 'Unable to reach the server.' }
    } finally { sending.value = false }
  }

  return { mode, replyTone, debateConfig, playerNames, sessions, turns, sessionId, title, sending, loadingSession, loadingHistory,
    historyError, error, busy, deletingId, deleteError, refreshHistory, newChat, openSession, deleteSession, send }
}
