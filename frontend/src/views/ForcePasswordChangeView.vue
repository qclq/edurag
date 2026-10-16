<script setup>
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Key, Lock, SwitchButton, User } from '@element-plus/icons-vue'
import { authenticatedRequest, fetchCurrentUser, logout, useAuth } from '../stores/auth'

const router = useRouter()
const { state } = useAuth()
const form = reactive({ newPassword: '', confirmPassword: '' })
const saving = ref(false)
const minLength = computed(() => state.bootstrap?.passwordPolicy?.minLength || 6)
const maxLength = computed(() => state.bootstrap?.passwordPolicy?.maxLength || 18)

const submit = async () => {
  if (!form.newPassword || !form.confirmPassword) {
    ElMessage.warning('请完整填写新密码和确认密码')
    return
  }
  if (form.newPassword.length < minLength.value || form.newPassword.length > maxLength.value) {
    ElMessage.warning(`新密码长度须为 ${minLength.value}–${maxLength.value} 位`)
    return
  }
  if (form.newPassword !== form.confirmPassword) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  saving.value = true
  try {
    await authenticatedRequest('/me/password', {
      method: 'POST',
      body: { newPassword: form.newPassword },
    })
    await fetchCurrentUser()
    ElMessage.success('密码修改成功，正在进入系统')
    await router.replace('/home')
  } catch (error) {
    ElMessage.error(error.message || '密码修改失败')
  } finally {
    saving.value = false
  }
}

const exit = async () => {
  try { await logout() } finally { await router.replace('/login') }
}
</script>

<template>
  <main class="force-password-page">
    <section class="force-password-card">
      <div class="force-icon"><el-icon><Key /></el-icon></div>
      <p class="eyebrow">FIRST SIGN-IN SECURITY</p>
      <h1>首次登录，请修改初始密码</h1>
      <p class="intro">为了保护账号安全，在完成密码修改前暂时不能进入系统其他页面。</p>

      <div class="account-line"><el-icon><User /></el-icon><span>当前账号</span><b>{{ state.user?.account }}</b></div>

      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item :label="`新密码（${minLength}–${maxLength} 位）`"><el-input v-model="form.newPassword" type="password" show-password autocomplete="new-password" :maxlength="maxLength" :prefix-icon="Lock" /></el-form-item>
        <el-form-item label="确认新密码"><el-input v-model="form.confirmPassword" type="password" show-password autocomplete="new-password" :maxlength="maxLength" :prefix-icon="Lock" /></el-form-item>
        <el-button class="primary-btn submit-btn" native-type="submit" :loading="saving">保存新密码并进入系统</el-button>
      </el-form>

      <button class="exit-button" type="button" @click="exit"><el-icon><SwitchButton /></el-icon>退出当前账号</button>
    </section>
  </main>
</template>

<style scoped>
.force-password-page{min-height:100vh;display:grid;place-items:center;padding:28px 18px;background:radial-gradient(circle at 50% 0,rgba(0,81,213,.08),transparent 38%),#f8fafc}.force-password-card{width:min(100%,480px);padding:34px;border:1px solid #e8eef7;border-radius:16px;background:#fff;box-shadow:0 24px 60px rgba(15,23,42,.12)}.force-icon{width:54px;height:54px;display:grid;place-items:center;border-radius:14px;background:#eff4ff;color:#0051d5;font-size:25px}.eyebrow{margin:20px 0 7px;color:#0051d5;font-size:10px;font-weight:800;letter-spacing:.14em}.force-password-card h1{margin:0;font-size:25px;letter-spacing:-.03em}.intro{margin:9px 0 22px;color:#64748b;font-size:12px;line-height:1.7}.account-line{display:flex;align-items:center;gap:8px;padding:11px 13px;margin-bottom:20px;border-radius:10px;background:#f8fafc;color:#64748b;font-size:11px}.account-line b{margin-left:auto;color:#0f172a;font-size:12px}.submit-btn{width:100%;height:42px;margin-top:3px}.exit-button{width:100%;display:flex;align-items:center;justify-content:center;gap:6px;padding:12px 0 0;border:0;background:transparent;color:#64748b;font-size:11px;cursor:pointer}@media(max-width:520px){.force-password-card{padding:26px 20px}.force-password-card h1{font-size:22px}}
</style>
