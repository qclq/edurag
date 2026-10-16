import { createRouter, createWebHistory } from 'vue-router'
import { canAccessAdmin, isAuthenticated, isExternalUser, isSuperAdmin, mustChangePassword, restoreSession } from './stores/auth'

const LoginView = () => import('./views/LoginView.vue')
const ForcePasswordChangeView = () => import('./views/ForcePasswordChangeView.vue')
const HomeView = () => import('./views/HomeWorkspaceView.vue')
const ChatView = () => import('./views/ChatView.vue')
const HistoryView = () => import('./views/HistoryView.vue')
const SourceView = () => import('./views/SourceView.vue')
const OverviewView = () => import('./views/OverviewView.vue')
const KnowledgeBaseView = () => import('./views/KnowledgeBaseView.vue')
const UploadView = () => import('./views/UploadView.vue')
const DocumentView = () => import('./views/DocumentView.vue')
const UsersView = () => import('./views/UsersView.vue')
const FeedbackView = () => import('./views/FeedbackView.vue')

const routes = [
  { path: '/', redirect: '/login' },
  { path: '/login', component: LoginView, meta: { standalone: true, title: '账号登录' } },
  { path: '/change-password', component: ForcePasswordChangeView, meta: { standalone: true, requiresAuth: true, title: '首次登录修改密码' } },
  { path: '/home', component: HomeView, meta: { requiresAuth: true, nav: 'home', title: '首页问答' } },
  { path: '/chat', component: ChatView, meta: { requiresAuth: true, nav: 'home', title: '对话详情' } },
  { path: '/history', component: HistoryView, meta: { requiresAuth: true, disallowExternal: true, nav: 'history', title: '历史会话' } },
  { path: '/source', component: SourceView, meta: { requiresAuth: true, nav: 'home', title: '引用来源详情' } },
  { path: '/admin/overview', component: OverviewView, meta: { requiresAuth: true, requiresAdmin: true, nav: 'overview', title: '管理概览' } },
  { path: '/admin/knowledge-bases', component: KnowledgeBaseView, meta: { requiresAuth: true, requiresAdmin: true, nav: 'knowledge', title: '知识库管理' } },
  { path: '/admin/upload', component: UploadView, meta: { requiresAuth: true, requiresAdmin: true, nav: 'tasks', title: '任务中心' } },
  { path: '/admin/document', component: DocumentView, meta: { requiresAuth: true, requiresAdmin: true, nav: 'documents', title: '文档详情' } },
  { path: '/admin/users', component: UsersView, meta: { requiresAuth: true, requiresAdmin: true, requiresSuperAdmin: true, nav: 'users', title: '用户权限' } },
  { path: '/admin/feedback', component: FeedbackView, meta: { requiresAuth: true, requiresAdmin: true, nav: 'feedback', title: '问答与反馈' } },
  { path: '/:pathMatch(.*)*', redirect: '/login' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  await restoreSession()
  if (to.path === '/login' && isAuthenticated.value) {
    if (mustChangePassword.value) return '/change-password'
    const redirect = typeof to.query.redirect === 'string' && to.query.redirect.startsWith('/')
      ? to.query.redirect
      : '/home'
    return redirect
  }
  if (to.meta.requiresAuth && !isAuthenticated.value) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (isAuthenticated.value && mustChangePassword.value && to.path !== '/change-password') {
    return '/change-password'
  }
  if (to.path === '/change-password' && !mustChangePassword.value) return '/home'
  if (to.meta.disallowExternal && isExternalUser.value) return '/home'
  if (to.meta.requiresAdmin && !canAccessAdmin.value) return '/home'
  if (to.meta.requiresSuperAdmin && !isSuperAdmin.value) return '/home'
  return true
})

router.afterEach((to) => {
  document.title = `${to.meta.title || '校园智库'} · 校园智库`
})

export default router
