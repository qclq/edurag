<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Hide, Lock, OfficeBuilding, Promotion, User, View } from '@element-plus/icons-vue'
import { externalAccess as externalAccessRequest, getCaptcha, loadBootstrap, login as loginRequest, useAuth } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const { state } = useAuth()
const form = reactive({ account: '', password: '', captchaCode: '' })
const captcha = ref(null)
const showPassword = ref(false)
const loading = ref(false)
const captchaLoading = ref(false)
const externalLoading = ref(false)
const fieldErrors = ref({})
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
    const user = await loginRequest({
      account: form.account,
      password: form.password,
      captchaId: captcha.value?.captchaId,
      captchaCode: form.captchaCode,
      remember: true,
    })
    if (user.mustChangePassword) {
      ElMessage.warning('首次登录，请先修改初始密码')
      await router.replace('/change-password')
    } else {
      ElMessage.success('身份认证成功')
      await router.replace(safeRedirect())
    }
  } catch (error) {
    fieldErrors.value = error.fieldErrors || {}
    ElMessage.error(error.message || '登录失败，请稍后重试')
    await refreshCaptcha()
  } finally {
    loading.value = false
  }
}

const enterExternalAccess = async () => {
  externalLoading.value = true
  try {
    await externalAccessRequest()
    ElMessage.success('已进入外部人员问答模式')
    await router.replace('/home')
  } catch (error) {
    ElMessage.error(error.message || '外部人员访问失败，请稍后重试')
  } finally {
    externalLoading.value = false
  }
}

onMounted(async () => {
  try {
    await loadBootstrap()
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
        <div class="online-label"><i />系统服务已连接</div>
        <h1>{{ systemName }}</h1>
        <p>{{ state.bootstrap?.description || 'Campus AI Knowledge Base' }}</p>
      </div>

      <div class="login-method"><el-icon><User /></el-icon>学工号密码登录</div>

      <form class="local-pane" @submit.prevent="login">
        <label>学号 / 教工号<el-input v-model="form.account" :prefix-icon="User" autocomplete="username" placeholder="请输入登录账号" /><small v-if="fieldErrors.account" class="field-error">{{ fieldErrors.account }}</small></label>
        <label>登录密码
          <el-input v-model="form.password" :type="showPassword ? 'text' : 'password'" :prefix-icon="Lock" autocomplete="current-password" :maxlength="passwordMaxLength" :placeholder="passwordPlaceholder">
            <template #suffix><el-icon class="clickable" @click="showPassword = !showPassword"><component :is="showPassword ? View : Hide" /></el-icon></template>
          </el-input>
          <small v-if="fieldErrors.password" class="field-error">{{ fieldErrors.password }}</small>
        </label>
        <label v-if="captchaEnabled">安全验证码
          <div class="captcha-row"><el-input v-model="form.captchaCode" placeholder="请输入验证码" :maxlength="state.bootstrap?.captchaPolicy?.length || 4" /><button class="captcha" type="button" :disabled="captchaLoading" title="点击刷新验证码" @click="refreshCaptcha"><img v-if="captcha?.imageDataUrl" :src="captcha.imageDataUrl" alt="安全验证码" /><span v-else>加载中</span></button></div>
          <small v-if="fieldErrors.captchaCode" class="field-error">{{ fieldErrors.captchaCode }}</small>
        </label>
        <div class="session-policy"><el-icon><Lock /></el-icon><span>登录状态保留 {{rememberDays}} 天，关闭浏览器后无需重新登录；到期后需再次验证密码。</span></div>
        <el-button native-type="submit" class="primary-btn login-submit" :loading="loading">登 录 进 入</el-button>
      </form>

      <div class="external-divider"><span>或</span></div>
      <el-button class="external-access" :icon="Promotion" :loading="externalLoading" @click="enterExternalAccess">外部人员访问</el-button>
      <p class="external-note">无需账号和验证码，仅开放问答功能；不提供个人历史记录。</p>

      <footer class="login-footer">
        <p>遇到登录异常请联系系统管理员</p>
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
.login-method { display:flex; align-items:center; justify-content:center; gap:6px; margin-top:24px; padding:11px; border-radius:9px; background:#f8fafc; color:#334155; font-size:12px; font-weight:700; }
.local-pane { display:grid; gap:14px; padding-top:17px; }
.local-pane label { display:grid; gap:7px; color:#334155; font-size:11px; font-weight:700; }
.captcha-row { display:grid; grid-template-columns:1fr 100px; gap:8px; }
.captcha { height:32px; display:grid; place-items:center; padding:0; overflow:hidden; border:1px solid #dbe5f3; border-radius:9px; background:#eff4ff; color:#64748b; cursor:pointer; }
.captcha img { display:block; width:100%; height:100%; object-fit:contain; }
.field-error { color:#dc2626; font-size:10px; font-weight:500; }
.session-policy { display:flex; align-items:flex-start; gap:7px; padding:9px 10px; border-radius:9px; background:#f8fafc; color:#64748b; font-size:10px; line-height:1.55; }
.session-policy .el-icon { flex:none; margin-top:2px; color:#0051d5; }
.login-submit { width:100%; height:42px; margin-top:1px; }
.external-divider{display:flex;align-items:center;gap:10px;margin:15px 0 11px;color:#94a3b8;font-size:9px}.external-divider:before,.external-divider:after{content:"";height:1px;flex:1;background:#e8edf4}.external-access{width:100%;height:40px;border-color:#cbd5e1;color:#334155;font-weight:700}.external-note{margin:7px 0 0;color:#94a3b8;font-size:9px;text-align:center}
.clickable { cursor:pointer; }
.login-footer { position:relative; padding-top:20px; margin-top:18px; border-top:1px solid #eff4ff; text-align:center; color:#94a3b8; font-size:9px; }
.login-footer p { color:#64748b; }
.login-footer nav { display:flex; justify-content:center; gap:8px; margin-top:7px; }
@media(max-width:520px){ .login-card{padding:24px 20px 18px}.login-head h1{font-size:20px} }
</style>
