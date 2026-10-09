<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Download, Edit, Lock, Plus, Refresh, Search, Upload, User, Warning } from '@element-plus/icons-vue'
import MetricCard from '../components/MetricCard.vue'
import StatusPill from '../components/StatusPill.vue'
import { authenticatedRequest } from '../stores/auth'

const importOpen = ref(false)
const search = ref('')
const users = ref([])
const stats = ref({ total: 0, students: 0, staff: 0, active: 0, abnormal: 0 })
const departments = ref([])
const roles = ref([])
const departmentId = ref('')
const roleFilter = ref('')
const statusFilter = ref('')
const page = ref(1)
const total = ref(0)
const pageSize = 20
const editOpen = ref(false)
const editing = ref(null)
const saving = ref(false)
const importFile = ref(null)
const importReview = ref(null)
let searchTimer = null
const form = reactive({ name: '', account: '', studentOrStaffNo: '', userType: 'staff', departmentId: null, email: '', phone: '', roleCodes: ['normal_user'], status: 'active' })

const loadUsers = async () => {
  const params = new URLSearchParams({ page: String(page.value), pageSize: String(pageSize) })
  if (search.value.trim()) params.set('q', search.value.trim())
  if (departmentId.value) params.set('departmentId', departmentId.value)
  if (roleFilter.value) params.set('role', roleFilter.value)
  if (statusFilter.value) params.set('status', statusFilter.value)
  try {
    const envelope = await authenticatedRequest(`/admin/users?${params}`, { includeEnvelope: true })
    users.value = envelope.data || []
    total.value = envelope.pagination?.total || 0
  } catch (error) { ElMessage.error(error.message || '用户列表加载失败') }
}

const loadPage = async () => {
  const [statsResult, deptResult, roleResult] = await Promise.allSettled([
    authenticatedRequest('/admin/users/stats'), authenticatedRequest('/admin/departments'), authenticatedRequest('/admin/roles'),
  ])
  if (statsResult.status === 'fulfilled') stats.value = statsResult.value
  if (deptResult.status === 'fulfilled') departments.value = deptResult.value
  if (roleResult.status === 'fulfilled') roles.value = roleResult.value
  await loadUsers()
}

const openCreate = () => {
  editing.value = null
  Object.assign(form, { name: '', account: '', studentOrStaffNo: '', userType: 'staff', departmentId: null, email: '', phone: '', roleCodes: ['normal_user'], status: 'active' })
  editOpen.value = true
}
const openEdit = (user) => {
  editing.value = user
  Object.assign(form, { name: user.name, account: user.account, studentOrStaffNo: user.studentOrStaffNo || '', userType: user.userType, departmentId: user.department?.id || null, email: user.email || '', phone: user.phone || '', roleCodes: user.roles.map(role => role.code), status: user.status })
  editOpen.value = true
}
const saveUser = async () => {
  if (!form.name.trim() || (!editing.value && !form.account.trim())) return ElMessage.warning('请填写姓名和账号')
  saving.value = true
  try {
    if (editing.value) {
      await authenticatedRequest(`/admin/users/${editing.value.id}`, { method: 'PATCH', body: { name: form.name, studentOrStaffNo: form.studentOrStaffNo || null, userType: form.userType, departmentId: form.departmentId, email: form.email || null, phone: form.phone || null, version: editing.value.version } })
      if (JSON.stringify(form.roleCodes.slice().sort()) !== JSON.stringify(editing.value.roles.map(role => role.code).sort())) {
        const fresh = await authenticatedRequest(`/admin/users/${editing.value.id}`)
        await authenticatedRequest(`/admin/users/${editing.value.id}/roles`, { method: 'PUT', body: { roleCodes: form.roleCodes, reason: '管理员在用户管理页面调整角色', version: fresh.version } })
      }
      ElMessage.success('用户资料已更新')
    } else {
      await authenticatedRequest('/admin/users', { method: 'POST', body: { name: form.name, account: form.account, studentOrStaffNo: form.studentOrStaffNo || null, userType: form.userType, departmentId: form.departmentId, email: form.email || null, phone: form.phone || null, roleCodes: form.roleCodes, status: form.status } })
      ElMessage.success('用户已创建，请通过密码重置流程发送初始凭据')
    }
    editOpen.value = false
    await loadPage()
  } catch (error) { ElMessage.error(error.message || '保存用户失败') }
  finally { saving.value = false }
}

const toggleStatus = async (user) => {
  const status = user.status === 'active' ? 'disabled' : 'active'
  try {
    await ElMessageBox.confirm(`确定${status === 'active' ? '启用' : '停用'}用户“${user.name}”吗？`, '账号状态变更', { type: 'warning' })
    await authenticatedRequest(`/admin/users/${user.id}/status`, { method: 'POST', body: { status, reason: '管理员在用户管理页面调整账号状态' } })
    ElMessage.success('账号状态已更新'); await loadPage()
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '状态更新失败') }
}

const resetPassword = async (user) => {
  const method = user.email ? 'email' : user.phone ? 'sms' : null
  if (!method) return ElMessage.warning('该用户没有邮箱或手机号，无法安全发送重置凭据')
  try {
    await ElMessageBox.confirm(`将通过${method === 'email' ? '邮箱' : '短信'}发送重置凭据，并使现有会话失效。`, '重置密码', { type: 'warning' })
    await authenticatedRequest(`/admin/users/${user.id}/password-reset`, { method: 'POST', body: { deliveryMethod: method } })
    ElMessage.success('密码重置请求已提交')
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '密码重置失败') }
}

const validateImport = async (file) => {
  if (!file) return
  const body = new FormData(); body.append('file', file)
  try { importReview.value = await authenticatedRequest('/admin/user-imports/validate', { method: 'POST', body }) }
  catch (error) { ElMessage.error(error.message || '导入文件校验失败') }
}
const commitImport = async () => {
  if (!importReview.value) return
  try {
    const result = await authenticatedRequest(`/admin/user-imports/${importReview.value.importId}/commit`, { method: 'POST', body: { decisions: [] } })
    ElMessage.success(`导入完成：新增 ${result.result?.created || 0}，跳过 ${result.result?.skipped || 0}`)
    importOpen.value = false; importReview.value = null; await loadPage()
  } catch (error) { ElMessage.error(error.message || '用户导入失败') }
}

const exportUsers = () => {
  if (!users.value.length) return ElMessage.warning('当前没有可导出的用户')
  const quote = value => `"${String(value ?? '').replaceAll('"', '""')}"`
  const rows = ['姓名,账号,学工号,类型,部门,角色,状态,邮箱,手机号', ...users.value.map(item => [item.name,item.account,item.studentOrStaffNo,item.userType,item.department?.name,item.roles.map(role=>role.name).join('|'),item.status,item.email,item.phone].map(quote).join(','))]
  const url = URL.createObjectURL(new Blob([`\uFEFF${rows.join('\r\n')}`], {type:'text/csv;charset=utf-8'})); const link=document.createElement('a');link.href=url;link.download='用户列表.csv';link.click();URL.revokeObjectURL(url)
}

watch([search, departmentId, roleFilter, statusFilter], () => { page.value = 1; window.clearTimeout(searchTimer); searchTimer = window.setTimeout(loadUsers, 300) })
watch(page, loadUsers)
onMounted(loadPage)
</script>

<template>
  <div class="page-content users-page">
    <header class="users-head"><div><div><h1>师生用户与权限管控</h1></div><p>管理本地与统一认证账号、角色和启停状态</p></div><div><el-button :icon="Download" @click="exportUsers">导出当前页</el-button><el-button :icon="Upload" @click="importOpen=true">批量导入用户</el-button><el-button class="primary-btn" :icon="Plus" @click="openCreate">新建用户</el-button></div></header>

    <div class="card-grid metric-grid user-metrics"><MetricCard label="教职工账号" :value="stats.staff" unit="人" tone="blue"><el-icon><User /></el-icon></MetricCard><MetricCard label="学生账号" :value="stats.students" unit="人" tone="green"><el-icon><User /></el-icon></MetricCard><MetricCard label="正常账号" :value="stats.active" :unit="`共 ${stats.total} 人`" tone="amber"><el-icon><Lock /></el-icon></MetricCard><MetricCard label="异常与暂停访问" :value="stats.abnormal" unit="人" tone="red"><el-icon><Warning /></el-icon></MetricCard></div>

    <section class="user-filters panel"><div class="filter-top"><el-input v-model="search" :prefix-icon="Search" clearable placeholder="输入姓名、账号或学工号搜索..."/><el-select v-model="departmentId" clearable placeholder="全部院系/职能部门"><el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id"/></el-select><el-select v-model="roleFilter" clearable placeholder="全部权限角色"><el-option v-for="item in roles" :key="item.code" :label="item.name" :value="item.code"/></el-select><el-select v-model="statusFilter" clearable placeholder="全部账号状态"><el-option label="正常" value="active"/><el-option label="已禁用" value="disabled"/></el-select></div><div class="role-chips"><b>用户目录 · 全部身份</b><span>共 {{ total }} 条记录</span><el-button text circle :icon="Refresh" @click="loadPage" /></div></section>

    <section class="users-table panel">
      <el-table :data="users" row-key="id"><el-table-column label="姓名与基本档案" width="220"><template #default="{row}"><div class="user-cell"><span>{{row.name?.slice(0,1)}}</span><div><b>{{row.name}}</b><small>{{row.email || row.phone || row.account}}</small></div></div></template></el-table-column><el-table-column label="学号 / 工号" width="130"><template #default="{row}">{{row.studentOrStaffNo || row.account}}</template></el-table-column><el-table-column label="身份类型" width="120"><template #default="{row}"><b>{{({student:'学生',staff:'教职工',external:'外部用户'})[row.userType] || row.userType}}</b><p class="table-sub">{{row.authSource==='local'?'本地账号':'统一认证'}}</p></template></el-table-column><el-table-column label="所属院系/职能" min-width="150"><template #default="{row}">{{row.department?.name || '未分配'}}</template></el-table-column><el-table-column label="分配角色" min-width="180"><template #default="{row}"><div class="scope-tags"><span v-for="role in row.roles" :key="role.code" class="role-tag">{{role.name}}</span></div></template></el-table-column><el-table-column label="账号状态" width="100"><template #default="{row}"><StatusPill :tone="row.status==='active'?'success':'danger'" :text="row.status==='active'?'正常':'已禁用'" /></template></el-table-column><el-table-column label="最近活跃时间" width="165"><template #default="{row}">{{row.lastActiveAt?new Date(row.lastActiveAt).toLocaleString('zh-CN'):'从未活跃'}}</template></el-table-column><el-table-column label="操作" fixed="right" width="180"><template #default="{row}"><el-button text circle :icon="Edit" title="编辑" @click="openEdit(row)"/><el-button text circle :icon="Lock" title="重置密码" @click="resetPassword(row)"/><el-button text :type="row.status==='active'?'danger':'primary'" @click="toggleStatus(row)">{{row.status==='active'?'停用':'启用'}}</el-button></template></el-table-column></el-table>
      <footer><span>共 {{ total }} 条记录</span><el-pagination v-model:current-page="page" background layout="prev, pager, next" :page-size="pageSize" :total="total" /></footer>
    </section>

    <el-dialog v-model="importOpen" width="760" align-center append-to-body class="import-dialog">
      <template #header><div class="import-title"><span><el-icon><Check /></el-icon></span><div><h3>批量导入用户</h3><p>支持 CSV / XLSX，先预校验再提交</p></div></div></template>
      <input type="file" accept=".csv,.xlsx" @change="event=>validateImport(event.target.files?.[0])" />
      <div v-if="importReview" class="import-stats"><div><small>总行数</small><b>{{importReview.summary.total}}</b></div><div class="success"><small>有效</small><b>{{importReview.summary.valid}}</b></div><div class="danger"><small>冲突</small><b>{{importReview.summary.conflicts}}</b></div><div><small>错误</small><b>{{importReview.summary.errors}}</b></div></div><el-empty v-else description="请选择 CSV 或 XLSX 文件进行预校验" />
      <div class="import-actions"><el-button @click="importOpen=false">关闭</el-button><el-button class="primary-btn" :disabled="!importReview" @click="commitImport">确认导入（冲突项自动跳过）</el-button></div>
    </el-dialog>

    <el-dialog v-model="editOpen" :title="editing?'编辑用户':'新建用户'" width="680" align-center append-to-body><el-form label-position="top"><div class="filter-top"><el-form-item label="姓名 *"><el-input v-model="form.name"/></el-form-item><el-form-item label="账号 *"><el-input v-model="form.account" :disabled="!!editing"/></el-form-item><el-form-item label="学工号"><el-input v-model="form.studentOrStaffNo"/></el-form-item><el-form-item label="用户类型"><el-select v-model="form.userType"><el-option label="教职工" value="staff"/><el-option label="学生" value="student"/><el-option label="外部用户" value="external"/></el-select></el-form-item></div><div class="filter-top"><el-form-item label="所属部门"><el-select v-model="form.departmentId" clearable><el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id"/></el-select></el-form-item><el-form-item label="邮箱"><el-input v-model="form.email"/></el-form-item><el-form-item label="手机号"><el-input v-model="form.phone"/></el-form-item><el-form-item label="角色"><el-select v-model="form.roleCodes" multiple><el-option v-for="item in roles" :key="item.code" :label="item.name" :value="item.code"/></el-select></el-form-item></div></el-form><template #footer><el-button @click="editOpen=false">取消</el-button><el-button class="primary-btn" :loading="saving" @click="saveUser">保存</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.users-head{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;margin-bottom:17px}.users-head>div:first-child>div{display:flex;align-items:center;gap:9px}.users-head h1{margin:0;font-size:27px}.users-head p{margin:7px 0 0;color:#64748b;font-size:11px}.users-head p b{color:#0f172a;font-size:14px}.users-head>div:last-child{display:flex;gap:8px}.user-metrics{margin-bottom:15px}.user-metrics :deep(.metric-card){min-height:105px}.user-filters{padding:14px;margin-bottom:14px}.filter-top{display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr;gap:8px}.role-chips{display:flex;align-items:center;gap:7px;padding-top:11px;margin-top:11px;border-top:1px solid #eff4ff;overflow:auto}.role-chips>b{color:#94a3b8;font-size:9px;white-space:nowrap}.role-chips button{padding:6px 10px;border:0;border-radius:999px;background:#f8fafc;color:#64748b;font-size:9px;white-space:nowrap;cursor:pointer}.role-chips button.active{background:#2a2e35;color:white}.role-chips>span{margin-left:auto;color:#94a3b8;font-size:8px;white-space:nowrap}.users-table{overflow:hidden}.user-cell{display:flex;align-items:center;gap:9px}.user-cell>span{width:34px;height:34px;display:grid;place-items:center;border-radius:50%;background:#1e2024;color:#fff;font-size:10px;font-weight:800}.user-cell>div{display:flex;flex-direction:column}.user-cell b{font-size:10px}.user-cell small{margin-top:3px;color:#64748b;font-size:8px}.table-sub{margin:3px 0 0;color:#94a3b8;font-size:8px}.role-tag{display:inline-flex;padding:5px 7px;border-radius:999px;background:#eff4ff;color:#0051d5;font-size:8px}.scope-tags{display:flex;flex-wrap:wrap;gap:4px}.scope-tags span{padding:4px 6px;border-radius:5px;background:#f1f5f9;color:#475569;font-size:8px}.users-table>footer{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;color:#64748b;font-size:9px}.import-title{display:flex;align-items:center;gap:10px;padding-right:42px;min-width:0}.import-title>span{width:38px;height:38px;flex:none;display:grid;place-items:center;border-radius:10px;background:#eff4ff;color:#0051d5}.import-title>div{flex:1;min-width:0}.import-title h3{margin:0;font-size:15px}.import-title p{margin:4px 0 0;color:#64748b;font-size:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.import-title :deep(.import-review-status){position:relative;z-index:1;flex:none;min-width:max-content;overflow:hidden;isolation:isolate;box-shadow:0 0 0 1px rgba(0,81,213,.04) inset}.import-title :deep(.import-review-status i){flex:none}.import-actions{display:flex;justify-content:flex-end;gap:7px;margin-bottom:14px}.import-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.import-stats>div{padding:12px;border-radius:9px;background:#f8fafc}.import-stats small,.import-stats b,.import-stats span{display:block}.import-stats small{color:#64748b;font-size:8px}.import-stats b{margin:5px 0;font-size:19px}.import-stats span{color:#94a3b8;font-size:8px}.import-stats .success{background:#ecfdf5}.import-stats .success b{color:#059669}.import-stats .danger{background:#fef2f2}.import-stats .danger b{color:#dc2626}.import-stats .small-value{font-size:10px}.conflict-head{display:flex;align-items:center;justify-content:space-between;padding:13px 0 8px;margin-top:10px;border-bottom:1px solid #eff4ff}.conflict-head>div{display:flex;align-items:center;gap:7px;color:#dc2626}.conflict-head span{display:flex;align-items:baseline;gap:5px}.conflict-head b{font-size:10px}.conflict-head small{color:#64748b;font-size:8px}.conflict-head p{margin:0;color:#94a3b8;font-size:8px}.conflict{display:flex;align-items:center;gap:9px;padding:10px;margin-top:8px;border-radius:9px;background:#fef2f2}.conflict>span{padding:4px 6px;border-radius:5px;background:#fff;color:#dc2626;font-size:8px;font-weight:800}.conflict>div{flex:1}.conflict b{font-size:9px}.conflict p{margin:3px 0 0;color:#7f1d1d;font-size:8px}
@media(max-width:1050px){.filter-top{grid-template-columns:1fr 1fr}.users-table :deep(.el-table){min-width:1080px}.users-table{overflow:auto}.users-head{align-items:flex-start;flex-direction:column}}
@media(max-width:700px){.filter-top{grid-template-columns:1fr}.users-head>div:last-child .el-button:first-child{display:none}.users-table>footer{align-items:flex-start;flex-direction:column;gap:10px}.import-stats{grid-template-columns:1fr 1fr}.conflict{align-items:flex-start;flex-wrap:wrap}.conflict>div{flex-basis:70%}.conflict-head{align-items:flex-start;flex-direction:column;gap:5px}.import-actions .el-button:first-child{display:none}.import-actions .primary-btn{flex:1}}
</style>
