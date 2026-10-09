<script setup>
defineProps({ tone: { type: String, default: 'success' }, text: String })
</script>

<template><span :class="['status-pill', `status-${tone}`]"><i />{{ text }}</span></template>
