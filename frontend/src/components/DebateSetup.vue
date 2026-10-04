<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import PlayerPicker from './PlayerPicker.vue'
import type { ChatMode, DebateConfig, PlayerChoice } from '../types'
defineProps<{ mode: ChatMode; disabled: boolean }>()
const emit = defineEmits<{ 'update:mode': [mode: ChatMode]; config: [value: DebateConfig | null]; names: [value: Record<string, string>] }>()
const supported = ref<PlayerChoice | null>(null)
const opponent = ref<PlayerChoice | null>(null)
const problem = computed(() => {
  if (!supported.value || !opponent.value) return 'Choose two players to start.'
  if (supported.value.id === opponent.value.id) return 'Choose two different players.'
  return ''
})
watch([supported, opponent], () => {
  emit('names', { ...(supported.value ? { [supported.value.id]: supported.value.name } : {}), ...(opponent.value ? { [opponent.value.id]: opponent.value.name } : {}) })
  emit('config', problem.value ? null : {
    supported_player: supported.value!.id, opponent_player: opponent.value!.id,
    scope_mode: 'auto',
    supported_season: null, opponent_season: null, phase: 'Regular Season',
  })
})
</script>
<template>
  <section class="debate-setup" aria-label="Conversation mode">
    <div class="mode-tabs"><button type="button" :disabled="disabled" :aria-pressed="mode === 'assistant'" @click="emit('update:mode', 'assistant')">Game Lab Assistant</button><button type="button" :disabled="disabled" :aria-pressed="mode !== 'assistant'" @click="emit('update:mode', 'debate')">Fan Debate</button></div>
    <template v-if="mode !== 'assistant'">
      <div class="debate-intro"><span class="section-kicker">TAKE A SIDE. BRING RECEIPTS.</span><h2>Pick your players. Make your case.</h2><p>The agent chooses relevant stats, seasons and matchups to answer your argument.</p></div>
      <fieldset :disabled="disabled" class="debate-fields"><legend class="sr-only">Choose debate roles</legend>
        <div class="player-pair"><PlayerPicker v-model="supported" label="Your player" :disabled="disabled" /><span class="versus">VS</span><PlayerPicker v-model="opponent" label="AI's player" :disabled="disabled" /></div>
        <p class="picker-note" role="status">{{ problem || `The agent defends ${opponent?.name}. Make your opening argument below.` }}</p>
        <p class="picker-note">No scope setup needed. Mention a particular game, season or playoffs whenever it matters to your argument.</p>
      </fieldset>
    </template>
  </section>
</template>
