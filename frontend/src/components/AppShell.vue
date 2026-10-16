<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Bell, ChatDotRound, Clock, Collection, DataAnalysis, Document,
  Fold, House, Menu, Search, Switch, User, UserFilled,
} from '@element-plus/icons-vue'
import { authenticatedRequest, logout, removeCurrentUserAvatar, uploadCurrentUserAvatar, useAuth } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const { state, canAccessAdmin, isSuperAdmin } = useAuth()
const isExternalUser = computed(() => state.user?.userType === 'external')
const mobileOpen = ref(false)
const search = ref('')
const searchResults = ref([])
const searchOpen = ref(false)
const searchLoading = ref(false)
const notifications = ref([])
const unreadCount = ref(0)
const notificationsOpen = ref(false)
const avatarOpen = ref(false)
const avatarFileInput = ref(null)
const avatarFile = ref(null)
const avatarPreviewUrl = ref('')
const avatarSaving = ref(false)

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
      { key: 'users', label: '用户权限', path: '/admin/users', icon: UserFilled, superAdminOnly: true },
      { key: 'feedback', label: '问答与反馈', path: '/admin/feedback', icon: ChatDotRound },
    ],
  },
]
const groups = computed(() => baseGroups
  .filter(group => group.label !== '知识库与管理' || canAccessAdmin.value)
  .map(group => ({
    ...group,
    items: group.items.filter(item =>
      (!isExternalUser.value || item.key !== 'history')
      && (!item.superAdminOnly || isSuperAdmin.value)),
  })))
const userSubtitle = computed(() => state.user?.subtitle || state.user?.department?.name || state.user?.roles?.[0]?.name || '')
const avatarUrl = computed(() => state.user?.avatarUrl || '')

const doSearch = async () => {
  if (searchLoading.value) return
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

const resetAvatarEditor = () => {
  if (avatarPreviewUrl.value.startsWith('blob:')) URL.revokeObjectURL(avatarPreviewUrl.value)
  avatarFile.value = null
  avatarPreviewUrl.value = ''
  if (avatarFileInput.value) avatarFileInput.value.value = ''
}

const openAvatarEditor = () => {
  if (isExternalUser.value) return
  resetAvatarEditor()
  avatarPreviewUrl.value = avatarUrl.value
  avatarOpen.value = true
}

const chooseAvatar = () => avatarFileInput.value?.click()

const onAvatarSelected = (event) => {
  const file = event.target.files?.[0]
  if (!file) return
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
    ElMessage.warning('头像仅支持 JPG、PNG 或 WebP 图片')
    event.target.value = ''
    return
  }
  if (file.size > 2 * 1024 * 1024) {
    ElMessage.warning('头像图片不能超过 2 MB')
    event.target.value = ''
    return
  }
  if (avatarPreviewUrl.value.startsWith('blob:')) URL.revokeObjectURL(avatarPreviewUrl.value)
  avatarFile.value = file
  avatarPreviewUrl.value = URL.createObjectURL(file)
}

const saveAvatar = async () => {
  if (!avatarFile.value) return ElMessage.warning('请先选择头像图片')
  avatarSaving.value = true
  try {
    await uploadCurrentUserAvatar(avatarFile.value)
    ElMessage.success('头像已更新')
    avatarOpen.value = false
  } catch (error) {
    ElMessage.error(error.message || '头像更新失败')
  } finally {
    avatarSaving.value = false
  }
}

const clearAvatar = async () => {
  avatarSaving.value = true
  try {
    await removeCurrentUserAvatar()
    ElMessage.success('头像已移除')
    avatarOpen.value = false
  } catch (error) {
    ElMessage.error(error.message || '头像移除失败')
  } finally {
    avatarSaving.value = false
  }
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
          <span class="avatar"><img v-if="avatarUrl" :src="avatarUrl" alt="用户头像"/><el-icon v-else><User /></el-icon></span>
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
        <div v-if="!isExternalUser" class="global-search-wrap">
          <el-input v-model="search" class="global-search" clearable placeholder="检索文档、知识库与历史会话" @keyup.enter="doSearch" @focus="searchResults.length && (searchOpen = true)" @clear="searchOpen=false">
            <template #suffix><el-button text circle :icon="Search" :loading="searchLoading" aria-label="搜索" title="搜索" @click="doSearch" /></template>
          </el-input>
          <div v-if="searchOpen" class="global-search-results panel">
            <button v-for="item in searchResults" :key="`${item.type}-${item.id}`" type="button" @click="openSearchResult(item)"><b>{{ item.title }}</b><small>{{ item.summary || ({document:'文档',conversation:'历史会话',knowledge_base:'知识库'}[item.type] || item.type) }}</small></button>
            <el-empty v-if="!searchResults.length && !searchLoading" description="没有找到匹配内容" :image-size="48" />
          </div>
        </div>
        <div class="top-actions">
          <div class="notification-wrap"><el-badge :value="unreadCount" :hidden="!unreadCount" :max="99"><el-button circle text aria-label="通知" @click="toggleNotifications"><el-icon><Bell /></el-icon></el-button></el-badge><div v-if="notificationsOpen" class="notification-panel panel"><header><b>通知</b><span>{{ unreadCount }} 条未读</span></header><button v-for="item in notifications" :key="item.id" type="button" :class="{ unread: !item.readAt }" @click="openNotification(item)"><b>{{ item.title }}</b><small>{{ item.body }}</small></button><el-empty v-if="!notifications.length" description="暂无通知" :image-size="48" /></div></div>
          <button type="button" class="top-avatar" :class="{ 'avatar-readonly': isExternalUser }" :aria-label="isExternalUser ? '外部人员头像' : '修改头像'" :title="isExternalUser ? '外部人员头像不可修改' : '修改头像'" @click="openAvatarEditor"><img v-if="avatarUrl" :src="avatarUrl" alt="用户头像"/><el-icon v-else><User /></el-icon></button>
        </div>
      </header>
      <main class="page-canvas"><slot /></main>
    </div>

    <el-dialog v-model="avatarOpen" title="修改头像" width="420" align-center append-to-body @closed="resetAvatarEditor">
      <div class="avatar-editor">
        <div class="avatar-preview"><img v-if="avatarPreviewUrl" :src="avatarPreviewUrl" alt="头像预览"/><el-icon v-else><User /></el-icon></div>
        <p>支持 JPG、PNG、WebP 格式，文件不超过 2 MB。</p>
        <input ref="avatarFileInput" class="avatar-file-input" type="file" accept="image/jpeg,image/png,image/webp" @change="onAvatarSelected"/>
        <el-button @click="chooseAvatar">选择图片</el-button>
      </div>
      <template #footer>
        <el-button v-if="avatarUrl" text type="danger" :loading="avatarSaving" @click="clearAvatar">移除头像</el-button>
        <el-button @click="avatarOpen=false">取消</el-button>
        <el-button class="primary-btn" :loading="avatarSaving" :disabled="!avatarFile" @click="saveAvatar">保存头像</el-button>
      </template>
    </el-dialog>
  </div>
</template>
