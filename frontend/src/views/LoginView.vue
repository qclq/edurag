<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Connection, Hide, Lock, OfficeBuilding, School, User, View } from '@element-plus/icons-vue'
import { completeSsoLogin, getCaptcha, loadBootstrap, login as loginRequest, useAuth } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const { state } = useAuth()
const tab = ref('sso')
const form = reactive({ account: '', password: '', captchaCode: '', remember: true })
const captcha = ref(null)
const showPassword = ref(false)
const loading = ref(false)
const captchaLoading = ref(false)
const fieldErrors = ref({})
const ssoEnabled = computed(() => Boolean(state.bootstrap?.loginMethods?.sso?.enabled))
const captchaEnabled = computed(() => state.bootstrap?.captchaPolicy?.enabled !== false)
const systemName = computed(() => state.bootstrap?.systemName || '智慧校园知识库问答系统')
const rememberDays = computed(() => state.bootstrap?.sessionPolicy?.rememberDays || 30)
const passwordMinLength = computed(() => state.bootstrap?.passwordPolicy?.minLength || 6)
const passwordMaxLength = computed(() => state.bootstrap?.passwordPolicy?.maxLength || 18)
const passwordPlaceholder = computed(() => `请输入 ${passwordMinLength.value}–${passwordMaxLength.value} 位密码`)
const currentYear = new Date().getFullYear()
const safeRedirect = () => (
  typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/')
    ? route.query.redirect
    : '/home'
)

const refreshCaptcha = async () => {
  if (!captchaEnabled.value) return
  captchaLoading.value = true
  try {
    captcha.value = await getCaptcha()
    form.captchaCode = ''
  } catch (error) {
    ElMessage.error(error.message || '验证码加载失败')
  } finally {
    captchaLoading.value = false
  }
}

const login = async () => {
  fieldErrors.value = {}
  if (!form.account || !form.password || (captchaEnabled.value && !form.captchaCode)) {
    ElMessage.warning('请完整填写学工号、密码和验证码')
    return
  }
  if (form.password.length < passwordMinLength.value || form.password.length > passwordMaxLength.value) {
    fieldErrors.value = { password: `密码长度须为 ${passwordMinLength.value}–${passwordMaxLength.value} 位` }
    ElMessage.warning(fieldErrors.value.password)
    return
  }
  loading.value = true
  try {
    await loginRequest({
      account: form.account,
      password: form.password,
      captchaId: captcha.value?.captchaId,
      captchaCode: form.captchaCode,
      remember: form.remember,
    })
    ElMessage.success('身份认证成功')
    await router.replace(safeRedirect())
  } catch (error) {
    fieldErrors.value = error.fieldErrors || {}
    ElMessage.error(error.message || '登录失败，请稍后重试')
    await refreshCaptcha()
  } finally {
    loading.value = false
  }
}

const ssoLogin = (role) => {
  if (!ssoEnabled.value) {
    ElMessage.warning('统一身份认证尚未配置，请使用本地账号登录')
    return
  }
  const callbackPath = '/login?sso=success'
  const returnTo = encodeURIComponent(callbackPath)
  window.location.assign(`/api/v1/auth/sso/authorize?channel=${role}&returnTo=${returnTo}`)
}

onMounted(async () => {
  try {
    await loadBootstrap()
    if (!ssoEnabled.value) tab.value = 'local'
    if (route.query.sso === 'success') {
      loading.value = true
      await completeSsoLogin()
      await router.replace(safeRedirect())
      return
    }
    await refreshCaptcha()
  } catch (error) {
    ElMessage.error(error.message || '系统配置加载失败')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main class="login-page">
    <section class="login-card">
      <div class="login-orb orb-a" /><div class="login-orb orb-b" />
      <div class="login-head">
        <div class="login-logo"><el-icon><OfficeBuilding /></el-icon></div>
        <div class="online-label"><i />认证服务已连接</div>
        <h1>{{ systemName }}</h1>
        <p>{{ state.bootstrap?.description || 'Campus AI Knowledge Base' }}</p>
      </div>

      <div class="login-tabs">
        <button :class="{ active: tab === 'sso' }" :disabled="!ssoEnabled" @click="tab = 'sso'"><el-icon><Connection /></el-icon>统一身份认证 (SSO)</button>
        <button :class="{ active: tab === 'local' }" @click="tab = 'local'"><el-icon><User /></el-icon>学工号密码登录</button>
      </div>

      <div v-if="tab === 'sso'" class="sso-pane">
        <button class="identity-card blue" @click="ssoLogin('student')">
          <span><el-icon><School /></el-icon></span>
          <div><b>在籍学生一键认证</b><small>本科生 / 研究生 / 留学生 CAS 通道</small></div>
          <el-icon><ArrowRight /></el-icon>
        </button>
        <button class="identity-card dark" @click="ssoLogin('staff')">
          <span><el-icon><User /></el-icon></span>
          <div><b>教职工身份认证</b><small>专任教师 / 科研人员 / 行政教工通行</small></div>
          <el-icon><ArrowRight /></el-icon>
        </button>
        <div class="login-note">
          <el-icon><Connection /></el-icon>
          <p><b>CAS 认证服务指引</b>优先推荐使用学校统一 CAS 平台登录，无需重复录入密码，支持直接挂载个人选课、科研项目等个性化检索语义。</p>
        </div>
        <div class="secure-copy"><el-icon><Lock /></el-icon>认证会话由服务端安全校验 <span>异常时请联系系统管理员</span></div>
      </div>

      <form v-else class="local-pane" @submit.prevent="login">
        <label>学号 / 教工号<el-input v-model="form.account" :prefix-icon="User" autocomplete="username" placeholder="请输入登录账号" /><small v-if="fieldErrors.account" class="field-error">{{ fieldErrors.account }}</small></label>
        <label>统一密码
          <el-input v-model="form.password" :type="showPassword ? 'text' : 'password'" :prefix-icon="Lock" autocomplete="current-password" :maxlength="passwordMaxLength" :placeholder="passwordPlaceholder">
            <template #suffix><el-icon class="clickable" @click="showPassword = !showPassword"><component :is="showPassword ? View : Hide" /></el-icon></template>
          </el-input>
          <small v-if="fieldErrors.password" class="field-error">{{ fieldErrors.password }}</small>
        </label>
        <label v-if="captchaEnabled">安全验证码
          <div class="captcha-row"><el-input v-model="form.captchaCode" placeholder="请输入验证码" :maxlength="state.bootstrap?.captchaPolicy?.length || 4" /><button class="captcha" type="button" :disabled="captchaLoading" title="点击刷新验证码" @click="refreshCaptcha"><img v-if="captcha?.imageDataUrl" :src="captcha.imageDataUrl" alt="安全验证码" /><span v-else>加载中</span></button></div>
          <small v-if="fieldErrors.captchaCode" class="field-error">{{ fieldErrors.captchaCode }}</small>
        </label>
        <div class="remember-row"><el-checkbox v-model="form.remember">记住登录状态 ({{rememberDays}}天)</el-checkbox><span>忘记密码请联系系统管理员</span></div>
        <el-button native-type="submit" class="primary-btn login-submit" :loading="loading">登 录 进 入</el-button>
      </form>

      <footer class="login-footer">
        <p>遇到认证异常请联系系统管理员</p>
        <div>© {{currentYear}} 智慧校园信息化办公室</div>
        <nav><span>账号与访问均受校内安全策略保护</span></nav>
      </footer>
    </section>
  </main>
</template>

<style scoped>
.login-page { min-height:100vh; display:grid; place-items:center; padding:32px 18px; background:#f8fafc; }
.login-card { position:relative; width:min(100%, 448px); overflow:hidden; padding:30px 30px 22px; border-radius:12px; background:#fff; box-shadow:0 22px 50px rgba(15,23,42,.12); }
.login-orb { position:absolute; width:180px; height:180px; border-radius:50%; filter:blur(50px); pointer-events:none; }
.orb-a { top:-100px; right:-90px; background:rgba(0,81,213,.12); }
.orb-b { bottom:-100px; left:-90px; background:rgba(15,23,42,.08); }
.login-head { position:relative; text-align:center; }
.login-logo { width:56px; height:56px; display:grid; place-items:center; margin:0 auto 15px; border-radius:12px; background:#eff4ff; color:#0051d5; font-size:26px; box-shadow:0 3px 8px rgba(0,81,213,.08); }
.online-label { width:max-content; max-width:100%; display:flex; align-items:center; gap:6px; margin:0 auto 10px; padding:4px 9px; border-radius:999px; background:#ecfdf5; color:#059669; font-size:10px; font-weight:700; }
.online-label i { width:6px; height:6px; border-radius:50%; background:#10b981; }
.login-head h1 { margin:0 0 4px; font-size:23px; letter-spacing:-.03em; }
.login-head p { margin:0; color:#64748b; font-size:11px; letter-spacing:.06em; }
.login-tabs { display:flex; gap:4px; margin-top:24px; padding:4px; background:#f8fafc; border-radius:9px; }
.login-tabs button { flex:1; display:flex; align-items:center; justify-content:center; gap:6px; padding:9px 5px; border:0; border-radius:7px; color:#64748b; background:transparent; font-size:12px; font-weight:650; cursor:pointer; }
.login-tabs button.active { color:#0f172a; background:#fff; box-shadow:0 2px 7px rgba(15,23,42,.08); }
.login-tabs button:disabled { opacity:.45; cursor:not-allowed; }
.sso-pane { padding-top:16px; }
.identity-card { width:100%; display:flex; align-items:center; gap:12px; padding:13px; margin-bottom:10px; border:0; border-radius:10px; background:#f8fafc; text-align:left; cursor:pointer; transition:.18s ease; }
.identity-card:hover { transform:translateY(-1px); box-shadow:0 5px 14px rgba(15,23,42,.07); }
.identity-card > span { width:40px; height:40px; flex:none; display:grid; place-items:center; border-radius:9px; color:#fff; font-size:19px; }
.identity-card.blue > span { background:#0051d5; }
.identity-card.dark > span { background:#06080c; }
.identity-card > div { flex:1; display:flex; flex-direction:column; gap:3px; }
.identity-card b { font-size:13px; }
.identity-card small { color:#64748b; font-size:10px; }
.identity-card > .el-icon { color:#94a3b8; }
.login-note { display:flex; gap:10px; padding:13px; margin-top:15px; border-radius:9px; background:#eff4ff; color:#0051d5; }
.login-note > .el-icon { margin-top:2px; flex:none; }
.login-note p { margin:0; color:#64748b; font-size:10px; line-height:1.6; }
.login-note b { display:block; margin-bottom:3px; color:#334155; font-size:11px; }
.secure-copy { display:flex; align-items:center; gap:6px; margin-top:14px; color:#94a3b8; font-size:10px; }
.secure-copy a { margin-left:auto; color:#64748b; }
.local-pane { display:grid; gap:14px; padding-top:17px; }
.local-pane label { display:grid; gap:7px; color:#334155; font-size:11px; font-weight:700; }
.captcha-row { display:grid; grid-template-columns:1fr 100px; gap:8px; }
.captcha { height:32px; display:grid; place-items:center; padding:0; overflow:hidden; border:1px solid #dbe5f3; border-radius:9px; background:#eff4ff; color:#64748b; cursor:pointer; }
.captcha img { width:100%; height:100%; object-fit:cover; }
.field-error { color:#dc2626; font-size:10px; font-weight:500; }
.remember-row { display:flex; justify-content:space-between; align-items:center; }
.remember-row a { color:#0051d5; font-size:11px; }
.login-submit { width:100%; height:42px; margin-top:1px; }
.clickable { cursor:pointer; }
.login-footer { position:relative; padding-top:20px; margin-top:18px; border-top:1px solid #eff4ff; text-align:center; color:#94a3b8; font-size:9px; }
.login-footer p { color:#64748b; }
.login-footer nav { display:flex; justify-content:center; gap:8px; margin-top:7px; }
@media(max-width:520px){ .login-card{padding:24px 20px 18px}.login-head h1{font-size:20px}.login-tabs button{font-size:11px} }
</style>
