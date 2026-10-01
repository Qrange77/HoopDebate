<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowUp, ArrowUpRight, Activity, ChartNoAxesCombined, ChevronRight, CircleDot, Menu, Moon, Sun, Users, Sparkles, X } from 'lucide-vue-next'
import ConversationSidebar from './components/ConversationSidebar.vue'
import ChatTurn from './components/ChatTurn.vue'
import { useChat } from './composables/useChat'

const chat = useChat()
const { sessions, turns, sessionId, title, sending, loadingSession, loadingHistory, historyError, error, busy, deletingId, deleteError } = chat
const draft = ref('')
const sidebarOpen = ref(false)
const dark = ref(true)
const composer = ref<HTMLTextAreaElement>()
const scrollArea = ref<HTMLElement>()
const menuButton = ref<HTMLButtonElement>()
const mobileViewport = window.matchMedia('(max-width: 900px)')
function onViewportChange(event: MediaQueryListEvent) { if (!event.matches) sidebarOpen.value = false }
const prompts = [
  { icon: CircleDot, label: 'Explore a matchup', text: 'Start with the game. Follow the story.', prompt: 'Find Orlando’s game against Memphis on January 15, 2026. What was the final score?' },
  { icon: Users, label: 'Get to know a player', text: 'The player behind the performance.', prompt: 'Show Paolo Banchero’s photo and profile from the game against Memphis on January 15, 2026.' },
  { icon: ChartNoAxesCombined, label: 'Go beyond the box score', text: 'Shooting efficiency, explained.', prompt: 'Calculate Paolo Banchero’s true shooting percentage against Memphis on January 15, 2026, and explain the inputs.' },
  { icon: Activity, label: 'Break down a team', text: 'See what drove the result.', prompt: 'What was Orlando’s estimated offensive rating against Memphis on January 15, 2026? Show the calculation.' },
]

function syncTheme() {
  document.documentElement.classList.toggle('dark', dark.value)
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark.value ? '#101828' : '#ffffff')
}
function toggleTheme() {
  dark.value = !dark.value; syncTheme()
  try { localStorage.setItem('game-lab-theme', dark.value ? 'dark' : 'light') } catch { /* Theme remains usable with storage disabled. */ }
}
async function resizeComposer() {
  await nextTick()
  if (composer.value) { composer.value.style.height = 'auto'; composer.value.style.height = `${Math.min(composer.value.scrollHeight, 156)}px` }
}
watch(draft, resizeComposer)
async function scrollToEnd() { await nextTick(); if (scrollArea.value) scrollArea.value.scrollTop = scrollArea.value.scrollHeight }
watch(() => [turns.value.length, sending.value, loadingSession.value], scrollToEnd)
function closeSidebar() { sidebarOpen.value = false; nextTick(() => menuButton.value?.focus()) }
async function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value
  if (sidebarOpen.value) { await nextTick(); document.querySelector<HTMLButtonElement>('#conversation-sidebar .mobile-only')?.focus() }
}
function keyboard(event: KeyboardEvent) {
  if (!sidebarOpen.value) return
  if (event.key === 'Escape') { event.preventDefault(); closeSidebar() }
  if (event.key === 'Tab') {
    const items = [...document.querySelectorAll<HTMLElement>('#conversation-sidebar button:not(:disabled), #conversation-sidebar input')].filter(el => el.getClientRects().length)
    const first = items[0], last = items[items.length - 1]
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
  }
}
function newChat() {
  chat.newChat(); draft.value = ''; sidebarOpen.value = false
  nextTick(() => composer.value?.focus())
}
async function openSession(id: string) {
  if (await chat.openSession(id)) { draft.value = ''; sidebarOpen.value = false; await scrollToEnd() }
}
async function deleteSession(id: string) {
  const wasActive = sessionId.value === id
  if (await chat.deleteSession(id)) {
    if (wasActive) draft.value = ''
    await nextTick()
    document.querySelector<HTMLButtonElement>('#conversation-sidebar .new-chat')?.focus()
  }
}
function choosePrompt(prompt: string) { draft.value = prompt; composer.value?.focus() }
async function submit() {
  if (busy.value || !draft.value.trim()) return
  const text = draft.value; draft.value = ''
  await chat.send(text)
  composer.value?.focus()
}
function onEnter(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void submit() }
}
async function retry(index: number) {
  if (busy.value) return
  const message = turns.value[index].message
  turns.value.splice(index, 1)
  await chat.send(message)
}
onMounted(() => {
  try { dark.value = localStorage.getItem('game-lab-theme') !== 'light' } catch { /* Default to dark. */ }
  syncTheme(); void chat.refreshHistory()
  document.addEventListener('keydown', keyboard)
  mobileViewport.addEventListener('change', onViewportChange)
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', keyboard)
  mobileViewport.removeEventListener('change', onViewportChange)
})
</script>

<template>
  <div class="app-shell">
    <button v-if="sidebarOpen" class="sidebar-backdrop" aria-label="Close conversation history" tabindex="-1" @click="closeSidebar"></button>
    <ConversationSidebar :sessions="sessions" :active-id="sessionId" :busy="busy" :loading="loadingHistory" :error="historyError" :open="sidebarOpen" :deleting-id="deletingId" :delete-error="deleteError" @new="newChat" @select="openSession" @delete="deleteSession" @close="closeSidebar" @refresh="chat.refreshHistory" />
    <main class="main-panel" :inert="sidebarOpen || undefined">
      <header class="topbar">
        <div class="breadcrumb"><button ref="menuButton" class="icon-button mobile-only" aria-label="Open conversation history" aria-controls="conversation-sidebar" :aria-expanded="sidebarOpen" @click="toggleSidebar"><Menu :size="21" /></button><span class="breadcrumb-root">Workspace</span><ChevronRight class="breadcrumb-root" :size="14" /><span>AI Assistant</span></div>
        <div class="topbar-actions"><span class="sport-tag"><CircleDot :size="13" /> NBA EDITION</span><span class="topbar-divider"></span><button class="icon-button" :aria-label="dark ? 'Switch to light theme' : 'Switch to dark theme'" @click="toggleTheme"><Sun v-if="dark" :size="19" /><Moon v-else :size="19" /></button><span class="profile-avatar" aria-label="Personal workspace">GL</span></div>
      </header>
      <div class="conversation-header"><div><span class="section-kicker">THE ASSISTANT</span><h1>{{ title }}</h1></div><span class="source-badge"><span></span> Powered by ESPN data</span></div>
      <div v-if="error" class="page-error" role="alert">{{ error }}<button class="icon-button" aria-label="Dismiss error" @click="error = ''"><X :size="16" /></button></div>
      <div ref="scrollArea" class="conversation-scroll" :aria-busy="busy">
        <div v-if="loadingSession" class="session-loading" role="status">Opening your conversation…</div>
        <section v-else-if="!turns.length" class="welcome">
          <div class="hero-art" aria-hidden="true"><svg viewBox="0 0 500 400"><g fill="none" stroke="currentColor" stroke-width="1"><rect x="60" y="35" width="330" height="380" rx="4"/><path d="M60 225h330M163 35v95h124V35M181 35v76h88V35"/><circle cx="225" cy="225" r="48"/><path d="M163 130a62 62 0 0 0 124 0M94 35v40a131 131 0 0 0 262 0V35"/><circle cx="225" cy="58" r="9"/><path d="M201 46h48"/></g></svg></div>
          <div class="welcome-eyebrow"><span class="tiny-line"></span> YOUR COURTSIDE INTELLIGENCE</div>
          <div class="hero-mark"><img src="/favicon.svg" width="54" height="54" alt="" /><span class="sparkle"><Sparkles :size="14" /></span></div>
          <h2>Your game.<br /><span>A clearer picture.</span></h2>
          <p class="welcome-description">From the final score to the finer details.<br />Ask anything about NBA games, players, and performance.</p>
          <div class="prompt-heading"><span>MAKE YOUR FIRST PLAY</span><span>Start with an idea <ArrowUpRight :size="14" /></span></div>
          <div class="prompt-grid"><button v-for="(prompt, index) in prompts" :key="prompt.label" class="prompt-card" @click="choosePrompt(prompt.prompt)"><span class="prompt-icon" :class="`prompt-icon-${index}`"><component :is="prompt.icon" :size="21" /></span><span class="prompt-copy"><strong>{{ prompt.label }}</strong><span>{{ prompt.text }}</span></span><ArrowUpRight class="prompt-arrow" :size="17" /></button></div>
          <div class="welcome-footnote"><span class="tiny-dot"></span> Game facts first. Every insight starts with the data.</div>
        </section>
        <div v-else class="message-list" aria-live="polite" aria-relevant="additions text"><ChatTurn v-for="(turn, index) in turns" :key="index" :turn="turn" :pending="sending && index === turns.length - 1 && !turn.response" @retry="retry(index)" /></div>
      </div>
      <footer class="composer-area">
        <form class="composer-box" @submit.prevent="submit">
          <label for="message" class="sr-only">Your message</label>
          <textarea id="message" ref="composer" v-model="draft" rows="1" maxlength="12000" placeholder="Ask about a game, a player, a moment…" :disabled="loadingSession" @keydown="onEnter"></textarea>
          <div class="composer-bottom"><span class="composer-mode"><Sparkles :size="15" /> Game Lab Assistant<span class="mode-pill">NBA</span></span><div class="send-controls"><span class="enter-hint">↵ to send</span><button class="send-button" type="submit" :disabled="busy || !draft.trim()" aria-label="Send message"><ArrowUp :size="21" /></button></div></div>
        </form>
        <p class="composer-disclaimer">AI can make mistakes. Check important game details.<span>Shift + Enter for a new line</span></p>
      </footer>
    </main>
  </div>
</template>
