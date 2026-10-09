<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Bell, ChatDotRound, Clock, Collection, DataAnalysis, Document,
  Fold, House, Menu, Search, Switch, User, UserFilled,
} from '@element-plus/icons-vue'
import { authenticatedRequest, logout, useAuth } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const { state, canAccessAdmin } = useAuth()
const mobileOpen = ref(false)
const search = ref('')
const searchResults = ref([])
const searchOpen = ref(false)
const searchLoading = ref(false)
const notifications = ref([])
const unreadCount = ref(0)
const notificationsOpen = ref(false)

const baseGroups = [
  {
    label: '工作区',
    items: [
      { key: 'home', label: '首页问答', path: '/home', icon: ChatDotRound },
      { key: 'history', label: '历史会话', path: '/history', icon: Clock },
    ],
  },
  {
    label: '知识库与管理',
    items: [
      { key: 'overview', label: '管理概览', path: '/admin/overview', icon: DataAnalysis },
      { key: 'knowledge', label: '知识库管理', path: '/admin/knowledge-bases', icon: Collection },
      { key: 'documents', label: '文档中心', path: '/admin/document', icon: Document },
      { key: 'tasks', label: '任务中心', path: '/admin/upload', icon: Switch },
      { key: 'users', label: '用户权限', path: '/admin/users', icon: UserFilled },
      { key: 'feedback', label: '问答与反馈', path: '/admin/feedback', icon: ChatDotRound },
    ],
  },
]
const groups = computed(() => baseGroups.filter(group => group.label !== '知识库与管理' || canAccessAdmin.value))
const userSubtitle = computed(() => state.user?.subtitle || state.user?.department?.name || state.user?.roles?.[0]?.name || '')
const serviceOnline = computed(() => state.bootstrap?.serviceStatus?.status === 'operational')

const doSearch = async () => {
  const value = search.value.trim()
  if (!value) {
    searchResults.value = []
    searchOpen.value = false
    return
  }
  searchLoading.value = true
  try {
    const envelope = await authenticatedRequest(`/search?q=${encodeURIComponent(value)}&pageSize=8`, { includeEnvelope: true })
    searchResults.value = envelope.data || []
    searchOpen.value = true
  } catch (error) {
    ElMessage.error(error.message || '搜索失败')
  } finally {
    searchLoading.value = false
  }
}

const openSearchResult = (item) => {
  searchOpen.value = false
  if (item.type === 'conversation') return router.push({ path: '/chat', query: { conversationId: item.id } })
  if (item.type === 'document') return router.push({ path: '/source', query: { documentId: item.id } })
  return router.push({ path: '/admin/knowledge-bases', query: { selected: item.id } })
}

const loadNotifications = async () => {
  try {
    const envelope = await authenticatedRequest('/notifications?pageSize=8', { includeEnvelope: true })
    notifications.value = envelope.data || []
    unreadCount.value = envelope.meta?.unreadCount || 0
  } catch {
    notifications.value = []
  }
}

const toggleNotifications = async () => {
  notificationsOpen.value = !notificationsOpen.value
  if (notificationsOpen.value) await loadNotifications()
}

const openNotification = async (item) => {
  if (!item.readAt) {
    try {
      await authenticatedRequest(`/notifications/${item.id}/read`, { method: 'POST' })
      item.readAt = new Date().toISOString()
      unreadCount.value = Math.max(0, unreadCount.value - 1)
    } catch {
      // Opening the related record is still useful if marking as read fails.
    }
  }
  notificationsOpen.value = false
  if (item.targetType === 'conversation' && item.targetId) return router.push({ path: '/chat', query: { conversationId: item.targetId } })
  if (item.targetType === 'document' && item.targetId) return router.push({ path: '/admin/document', query: { documentId: item.targetId } })
  if (item.targetType === 'task' && item.targetId) return router.push({ path: '/admin/upload', query: { taskId: item.targetId } })
  if (item.targetType === 'feedback') return router.push('/admin/feedback')
  if (item.targetType === 'knowledge_base' && item.targetId) return router.push({ path: '/admin/knowledge-bases', query: { selected: item.targetId } })
}

const doLogout = async () => {
  try {
    await logout()
  } catch {
    ElMessage.warning('服务端退出失败，本地登录状态已清除')
  }
  await router.replace('/login')
}

onMounted(loadNotifications)
</script>

<template>
  <div class="app-shell">
    <div v-if="mobileOpen" class="nav-backdrop" @click="mobileOpen = false" />
    <aside class="side-nav" :class="{ open: mobileOpen }">
      <div class="side-scroll">
        <div class="brand-row">
          <router-link to="/home" class="brand" @click="mobileOpen = false">
            <span class="brand-mark"><el-icon><House /></el-icon></span>
            <strong>{{ state.bootstrap?.systemName || '校园智库' }}</strong>
          </router-link>
          <span class="version-pill">RAG V2</span>
        </div>

        <div class="profile-card">
          <span class="avatar"><el-icon><User /></el-icon></span>
          <span class="profile-copy"><b>{{ state.user?.name }}</b><small>{{ userSubtitle }}</small></span>
        </div>

        <nav class="nav-groups">
          <section v-for="group in groups" :key="group.label" class="nav-group">
            <p>{{ group.label }}</p>
            <router-link
              v-for="item in group.items"
              :key="item.key"
              :to="item.path"
              class="nav-item"
              :class="{ active: route.meta.nav === item.key }"
              @click="mobileOpen = false"
            >
              <el-icon><component :is="item.icon" /></el-icon>
              <span>{{ item.label }}</span>
            </router-link>
          </section>
        </nav>
      </div>

      <div class="side-bottom">
        <button class="nav-item logout-item" type="button" @click="doLogout"><el-icon><Switch /></el-icon><span>退出登录</span></button>
      </div>
    </aside>

    <div class="shell-body">
      <header class="topbar">
        <el-button class="mobile-menu" circle text @click="mobileOpen = !mobileOpen">
          <el-icon><component :is="mobileOpen ? Fold : Menu" /></el-icon>
        </el-button>
        <div class="global-search-wrap">
          <el-input v-model="search" class="global-search" :prefix-icon="Search" clearable placeholder="检索文档、知识库与历史会话，按 Enter 搜索" @keyup.enter="doSearch" @clear="searchOpen=false" />
          <div v-if="searchOpen" class="global-search-results panel">
            <button v-for="item in searchResults" :key="`${item.type}-${item.id}`" type="button" @click="openSearchResult(item)"><b>{{ item.title }}</b><small>{{ item.summary || ({document:'文档',conversation:'历史会话',knowledge_base:'知识库'}[item.type] || item.type) }}</small></button>
            <el-empty v-if="!searchResults.length && !searchLoading" description="没有找到匹配内容" :image-size="48" />
          </div>
        </div>
        <div class="top-actions">
          <span class="engine-pill"><i :class="{ offline: !serviceOnline }" />{{ serviceOnline ? '系统服务正常' : '服务状态未知' }}</span>
          <div class="notification-wrap"><el-badge :value="unreadCount" :hidden="!unreadCount" :max="99"><el-button circle text aria-label="通知" @click="toggleNotifications"><el-icon><Bell /></el-icon></el-button></el-badge><div v-if="notificationsOpen" class="notification-panel panel"><header><b>通知</b><span>{{ unreadCount }} 条未读</span></header><button v-for="item in notifications" :key="item.id" type="button" :class="{ unread: !item.readAt }" @click="openNotification(item)"><b>{{ item.title }}</b><small>{{ item.body }}</small></button><el-empty v-if="!notifications.length" description="暂无通知" :image-size="48" /></div></div>
          <button type="button" class="top-avatar" aria-label="退出登录" title="退出登录" @click="doLogout"><el-icon><User /></el-icon></button>
        </div>
      </header>
      <main class="page-canvas"><slot /></main>
    </div>
  </div>
</template>
