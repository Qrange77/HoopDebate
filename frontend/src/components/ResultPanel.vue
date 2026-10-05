<script setup lang="ts">
import { computed, ref } from 'vue'
import { UserRound, ChevronDown } from 'lucide-vue-next'
import type { Panel } from '../types'
const props = defineProps<{ panel: Panel }>()
const failedImage = ref(false)
const headshot = computed(() => {
  try {
    const url = new URL(props.panel.headshot || '')
    if (url.protocol === 'https:' && url.hostname === 'a.espncdn.com' && url.pathname.startsWith('/i/headshots/')) return url.href
  } catch { /* Missing and unsupported image URLs use the placeholder. */ }
  return null
})
</script>

<template>
  <section class="result-card">
    <div class="player-header">
      <template v-if="panel.player">
        <img v-if="headshot && !failedImage" class="headshot" :src="headshot" :alt="`${panel.player} headshot`" referrerpolicy="no-referrer" @error="failedImage = true" />
        <div v-else class="photo-placeholder"><UserRound :size="25" /><span>Photo unavailable</span></div>
      </template>
      <div><span class="eyebrow">GAME LAB / PLAYER & TEAM INSIGHTS</span><h2>{{ panel.player || panel.team }}</h2><p>{{ [panel.player ? panel.team : null, panel.position, panel.jersey ? `#${panel.jersey}` : null].filter(Boolean).join(' · ') }}</p><p v-if="panel.provisional" class="metric-badge">Live game · Provisional statistics</p></div>
    </div>
    <div v-if="panel.metrics && Object.keys(panel.metrics).length" class="metrics">
      <div v-for="(metric, key) in panel.metrics" :key="key" class="metric">
        <h3>{{ metric.label }}</h3>
        <div class="metric-value">{{ metric.display_value ?? (typeof metric.value === 'number' && Number.isFinite(metric.value) ? (Number.isInteger(metric.value) ? String(metric.value) : metric.value.toFixed(2)) + (metric.unit === '%' ? '%' : '') : 'N/A') }}</div>
        <p v-if="metric.unit !== '%'">{{ metric.unit }}</p>
        <span v-if="metric.estimated" class="metric-badge">Estimated</span>
        <p v-if="metric.unavailable_reason">{{ metric.unavailable_reason }}</p>
        <details v-if="metric.formula || (metric.inputs && Object.keys(metric.inputs).length)"><summary>Formula & inputs <ChevronDown :size="13" /></summary><p>{{ metric.formula }}</p><pre>{{ JSON.stringify(metric.inputs, null, 2) }}</pre></details>
      </div>
    </div>
    <p v-if="panel.note" class="result-note">{{ panel.note }}</p>
  </section>
</template>
