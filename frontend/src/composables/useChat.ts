import { computed, ref } from 'vue'
import type { Conversation, Session, Turn } from '../types'

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options)
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `Request failed (${response.status})`)
  if (data === null) throw new Error('The server returned an unreadable response.')
  return data as T
}

export function useChat() {
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
      const data = await request<{ session_id: string; response: string; tool_calls: Turn['tool_calls'] }>('/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: turn.message, session_id: sessionId.value }),
      })
      sessionId.value = data.session_id
      if (title.value === 'New conversation') title.value = turn.message.slice(0, 80)
      turns.value[index] = { ...turn, response: data.response, tool_calls: data.tool_calls }
      await refreshHistory()
    } catch (e) {
      turns.value[index] = { ...turn, failed: true, response: e instanceof Error ? e.message : 'Unable to reach the server.' }
    } finally { sending.value = false }
  }

  return { sessions, turns, sessionId, title, sending, loadingSession, loadingHistory,
    historyError, error, busy, deletingId, deleteError, refreshHistory, newChat, openSession, deleteSession, send }
}
