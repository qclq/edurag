<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElConfigProvider } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import AppShell from './components/AppShell.vue'
import router from './router'

const route = useRoute()
const standalone = computed(() => route.meta.standalone)
const navigating = ref(false)
let finishTimer

const removeBeforeGuard = router.beforeEach(() => {
  window.clearTimeout(finishTimer)
  navigating.value = true
})
const removeAfterHook = router.afterEach(() => {
  finishTimer = window.setTimeout(() => {
    navigating.value = false
  }, 260)
})

onUnmounted(() => {
  window.clearTimeout(finishTimer)
  removeBeforeGuard()
  removeAfterHook()
})
</script>

<template>
  <ElConfigProvider :locale="zhCn">
    <div class="route-progress" :class="{ active: navigating }"><i /></div>
    <router-view v-if="standalone" v-slot="{ Component, route: viewRoute }">
      <Transition name="route-view" mode="out-in" appear>
        <component :is="Component" :key="viewRoute.fullPath" />
      </Transition>
    </router-view>
    <AppShell v-else>
      <router-view v-slot="{ Component, route: viewRoute }">
        <Transition name="route-view" mode="out-in" appear>
          <component :is="Component" :key="viewRoute.fullPath" />
        </Transition>
      </router-view>
    </AppShell>
  </ElConfigProvider>
</template>
