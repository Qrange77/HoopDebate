<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'
import type { PlayerChoice } from '../types'
const props = defineProps<{ label: string; modelValue: PlayerChoice | null; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: PlayerChoice | null] }>()
const query = ref(props.modelValue?.name || '')
const choices = ref<PlayerChoice[]>([])
const searching = ref(false)
const error = ref('')
let timer: ReturnType<typeof setTimeout>
let controller: AbortController | undefined
let version = 0
watch(query, value => {
  clearTimeout(timer); controller?.abort(); version++
  choices.value = []; error.value = ''; searching.value = false
  if (value === props.modelValue?.name) return
  emit('update:modelValue', null)
  if (value.trim().length < 2) return
  const current = version
  timer = setTimeout(async () => {
    controller = new AbortController(); searching.value = true
    try {
      const response = await fetch(`/players?query=${encodeURIComponent(value.trim())}`, { signal: controller.signal })
      if (!response.ok) throw new Error('Player search is unavailable. Try again.')
      const data = await response.json()
      if (current !== version) return
      choices.value = data.players
      if (!data.players.length) error.value = 'No matching NBA players. Try the full name.'
    } catch (e) {
      if (current === version && !(e instanceof DOMException && e.name === 'AbortError')) error.value = e instanceof Error ? e.message : 'Search failed.'
    } finally { if (current === version) searching.value = false }
  }, 350)
})
function years(player: PlayerChoice) {
  return player.from_year && player.to_year ? `${player.from_year}–${player.active ? 'present' : Number(player.to_year) + 1}` : 'Playing years unavailable'
}
function choose(player: PlayerChoice) {
  emit('update:modelValue', player); query.value = player.name; choices.value = []; error.value = ''
}
onBeforeUnmount(() => { clearTimeout(timer); controller?.abort(); version++ })
</script>
<template>
  <div class="player-picker">
    <label>{{ label }}<input v-model="query" type="search" :disabled="disabled" placeholder="Search current or retired players" autocomplete="off" :aria-label="label" /></label>
    <p v-if="searching" class="picker-note" role="status">Searching the NBA directory…</p>
    <p v-if="error" class="picker-note" role="status">{{ error }}</p>
    <ul v-if="choices.length" class="player-options" :aria-label="`${label} candidates`"><li v-for="player in choices" :key="player.id"><button type="button" @click="choose(player)"><strong>{{ player.name }}</strong><span>{{ years(player) }} · {{ player.active ? 'Active' : 'Retired' }}</span></button></li></ul>
    <p v-if="modelValue" class="picker-note">{{ years(modelValue) }}<span v-if="modelValue.stale"> · Cached directory</span></p>
  </div>
</template>
