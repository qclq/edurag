<script setup>
defineProps({
  label: String,
  value: [String, Number],
  unit: String,
  hint: String,
  tone: { type: String, default: 'blue' },
})
</script>

<template>
  <article class="metric-card">
    <div>
      <p>{{ label }}</p>
      <strong>{{ value }} <small>{{ unit }}</small></strong>
      <span v-if="hint" :class="`tone-${tone}`">{{ hint }}</span>
    </div>
    <div :class="['metric-icon', `tone-${tone}`]"><slot /></div>
  </article>
</template>
