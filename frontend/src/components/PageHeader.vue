<script setup>
defineProps({
  eyebrow: { type: String, default: '' },
  title: { type: String, required: true },
  description: { type: String, default: '' },
})
</script>

<template>
  <div class="page-heading">
    <div class="page-heading-copy">
      <div v-if="eyebrow" class="eyebrow"><span />{{ eyebrow }}</div>
      <h1>{{ title }}</h1>
      <p v-if="description">{{ description }}</p>
      <slot name="meta" />
    </div>
    <div class="page-heading-actions"><slot /></div>
  </div>
</template>
