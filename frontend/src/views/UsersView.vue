<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Delete, Download, Edit, Lock, Plus, Refresh, Search, Upload, User, Warning } from '@element-plus/icons-vue'
import MetricCard from '../components/MetricCard.vue'
import StatusPill from '../components/StatusPill.vue'
import { authenticatedRequest, useAuth } from '../stores/auth'

const { state } = useAuth()
const isSuperAdmin = computed(() => state.user?.roles?.some(role => role.code === 'super_admin'))
const protectedAdminRoles = new Set(['knowledge_admin', 'user_admin', 'super_admin'])
const canManageAccount = user => isSuperAdmin.value || !user.roles?.some(role => protectedAdminRoles.has(role.code))

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
const form = reactive({ name: '', account: '', studentOrStaffNo: '', userType: 'student', departmentId: null, email: '', phone: '', roleCodes: ['normal_user'], status: 'active' })
const createTypeLabel = computed(() => ({ student: '学生', staff: '教职工', external: '外部人员' })[form.userType] || '用户')

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
  Object.assign(form, { name: '', account: '', studentOrStaffNo: '', userType: 'student', departmentId: null, email: '', phone: '', roleCodes: ['normal_user'], status: 'active' })
  editOpen.value = true
}
const openImport = () => {
  importFile.value = null
  importReview.value = null
  importOpen.value = true
}
const openEdit = (user) => {
  editing.value = user
  Object.assign(form, { name: user.name, account: user.account, studentOrStaffNo: user.studentOrStaffNo || '', userType: user.userType, departmentId: user.department?.id || null, email: user.email || '', phone: user.phone || '', roleCodes: user.roles.map(role => role.code), status: user.status })
  editOpen.value = true
}
const saveUser = async () => {
  if (!form.name.trim()) return ElMessage.warning('请填写姓名')
  if (!editing.value && !form.account.trim()) return ElMessage.warning('请填写登录账号')
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
      await authenticatedRequest('/admin/users', { method: 'POST', body: {
        name: form.name.trim(),
        account: form.userType === 'student' ? null : form.account.trim(),
        studentOrStaffNo: form.userType === 'student' ? form.account.trim() : null,
        userType: form.userType,
        roleCodes: form.userType === 'staff' ? form.roleCodes : ['normal_user'],
      } })
      ElMessage.success(`${createTypeLabel.value}账号已创建：初始密码为 123456，首次登录必须修改密码`)
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
  const method = user.userType === 'student' ? 'manual' : user.email ? 'email' : user.phone ? 'sms' : null
  if (!method) return ElMessage.warning('该用户没有邮箱或手机号，无法安全发送重置凭据')
  try {
    const message = method === 'manual'
      ? '将该学生密码重置为 123456，并要求下次登录时强制修改密码。现有会话会立即失效。'
      : `将通过${method === 'email' ? '邮箱' : '短信'}发送重置凭据，并使现有会话失效。`
    await ElMessageBox.confirm(message, '重置密码', { type: 'warning' })
    await authenticatedRequest(`/admin/users/${user.id}/password-reset`, { method: 'POST', body: { deliveryMethod: method } })
    ElMessage.success(method === 'manual' ? '已重置为初始密码 123456' : '密码重置请求已提交')
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '密码重置失败') }
}

const deleteUser = async (user) => {
  try {
    await ElMessageBox.prompt(
      `删除后账号“${user.account}”将无法登录，且不会再出现在用户列表中。请输入账号确认删除。`,
      `删除用户：${user.name}`,
      {
        type: 'error',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        inputPlaceholder: user.account,
        inputValidator: value => value === user.account || '输入的账号不匹配',
      },
    )
    await authenticatedRequest(`/admin/users/${user.id}`, { method: 'DELETE' })
    ElMessage.success('用户已删除')
    if (users.value.length === 1 && page.value > 1) page.value -= 1
    else await loadPage()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '删除用户失败')
  }
}

const validateImport = async (file) => {
  if (!file) return
  importFile.value = file
  const body = new FormData(); body.append('file', file)
  try { importReview.value = await authenticatedRequest('/admin/user-imports/validate', { method: 'POST', body }) }
  catch (error) { ElMessage.error(error.message || '导入文件校验失败') }
}
const downloadImportTemplate = () => {
  const url = URL.createObjectURL(new Blob(['\uFEFF姓名,学号\r\n张三,20260001'], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a'); link.href = url; link.download = '学生账号导入模板.csv'; link.click(); URL.revokeObjectURL(url)
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
watch(() => form.userType, () => {
  if (editing.value) return
  form.roleCodes = ['normal_user']
})
watch(page, loadUsers)
onMounted(loadPage)
</script>

<template>
  <div class="page-content users-page">
    <header class="users-head"><div><div><h1>用户与权限管控</h1></div><p>管理学生、教职工账号及其权限</p></div><div><el-button :icon="Download" @click="exportUsers">导出当前页</el-button><el-button :icon="Upload" @click="openImport">批量导入学生</el-button><el-button class="primary-btn" :icon="Plus" @click="openCreate">新建用户</el-button></div></header>

    <div class="card-grid metric-grid user-metrics"><MetricCard label="教职工账号" :value="stats.staff" unit="人" tone="blue"><el-icon><User /></el-icon></MetricCard><MetricCard label="学生账号" :value="stats.students" unit="人" tone="green"><el-icon><User /></el-icon></MetricCard><MetricCard label="正常账号" :value="stats.active" :unit="`共 ${stats.total} 人`" tone="amber"><el-icon><Lock /></el-icon></MetricCard><MetricCard label="异常与暂停访问" :value="stats.abnormal" unit="人" tone="red"><el-icon><Warning /></el-icon></MetricCard></div>

    <section class="user-filters panel"><div class="filter-top"><el-input v-model="search" :prefix-icon="Search" clearable placeholder="输入姓名、账号或学工号搜索..."/><el-select v-model="departmentId" clearable placeholder="全部院系/职能部门"><el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id"/></el-select><el-select v-model="roleFilter" clearable placeholder="全部权限角色"><el-option v-for="item in roles" :key="item.code" :label="item.name" :value="item.code"/></el-select><el-select v-model="statusFilter" clearable placeholder="全部账号状态"><el-option label="正常" value="active"/><el-option label="已禁用" value="disabled"/></el-select></div><div class="role-chips"><b>用户目录 · 全部身份</b><span>共 {{ total }} 条记录</span><el-button text circle :icon="Refresh" @click="loadPage" /></div></section>

    <section class="users-table panel">
      <el-table :data="users" row-key="id"><el-table-column label="姓名与基本档案" width="220"><template #default="{row}"><div class="user-cell"><span>{{row.name?.slice(0,1)}}</span><div><b>{{row.name}}</b><small>{{row.email || row.phone || row.account}}</small></div></div></template></el-table-column><el-table-column label="学号 / 工号" width="130"><template #default="{row}">{{row.studentOrStaffNo || row.account}}</template></el-table-column><el-table-column label="身份类型" width="120"><template #default="{row}"><b>{{({student:'学生',staff:'教职工',external:'外部人员'})[row.userType] || row.userType}}</b><p class="table-sub">{{row.authSource==='local'?'本地账号':'统一认证'}}</p></template></el-table-column><el-table-column label="所属院系/职能" min-width="150"><template #default="{row}">{{row.department?.name || '未分配'}}</template></el-table-column><el-table-column label="分配角色" min-width="180"><template #default="{row}"><div class="scope-tags"><span v-for="role in row.roles" :key="role.code" class="role-tag">{{role.name}}</span></div></template></el-table-column><el-table-column label="账号状态" width="100"><template #default="{row}"><StatusPill :tone="row.status==='active'?'success':'danger'" :text="row.status==='active'?'正常':'已禁用'" /></template></el-table-column><el-table-column label="最近活跃时间" width="165"><template #default="{row}">{{row.lastActiveAt?new Date(row.lastActiveAt).toLocaleString('zh-CN'):'从未活跃'}}</template></el-table-column><el-table-column label="操作" fixed="right" width="220"><template #default="{row}"><template v-if="canManageAccount(row)"><el-button text circle :icon="Edit" title="编辑" @click="openEdit(row)"/><el-button text circle :icon="Lock" title="重置密码" @click="resetPassword(row)"/><el-button text :type="row.status==='active'?'danger':'primary'" @click="toggleStatus(row)">{{row.status==='active'?'停用':'启用'}}</el-button></template><el-button v-if="isSuperAdmin && row.id !== state.user?.id" text circle type="danger" :icon="Delete" title="删除用户" @click="deleteUser(row)"/></template></el-table-column></el-table>
      <footer><span>共 {{ total }} 条记录</span><el-pagination v-model:current-page="page" background layout="prev, pager, next" :page-size="pageSize" :total="total" /></footer>
    </section>

    <el-dialog v-model="importOpen" width="760" align-center append-to-body class="import-dialog">
      <template #header><div class="import-title"><span><el-icon><Check /></el-icon></span><div><h3>批量导入学生</h3><p>仅读取“姓名、学号”两列表头，其他列自动忽略</p></div></div></template>
      <div class="import-guide"><div><b>导入规则</b><p>账号自动使用学号，初始密码统一为 123456，学生首次登录必须修改密码。</p></div><el-button @click="downloadImportTemplate">下载模板</el-button></div>
      <input class="import-file" type="file" accept=".csv,.xlsx" @change="event=>validateImport(event.target.files?.[0])" />
      <div v-if="importReview" class="import-stats"><div><small>总行数</small><b>{{importReview.summary.total}}</b></div><div class="success"><small>有效</small><b>{{importReview.summary.valid}}</b></div><div class="danger"><small>冲突</small><b>{{importReview.summary.conflicts}}</b></div><div><small>错误</small><b>{{importReview.summary.errors}}</b></div></div><el-empty v-else description="请选择 CSV 或 XLSX 文件进行预校验" />
      <div v-if="importReview?.errors?.length" class="import-errors"><p v-for="item in importReview.errors" :key="item.row">第 {{ item.row }} 行：{{ item.message }}</p></div>
      <div class="import-actions"><el-button @click="importOpen=false">关闭</el-button><el-button class="primary-btn" :disabled="!importReview" @click="commitImport">确认导入（冲突项自动跳过）</el-button></div>
    </el-dialog>

    <el-dialog v-model="editOpen" :title="editing?'编辑用户':'新建用户'" width="720" align-center append-to-body>
      <el-form label-position="top">
        <template v-if="!editing">
          <div class="student-create-grid">
            <el-form-item label="姓名 *"><el-input v-model="form.name" :placeholder="`请输入${createTypeLabel}姓名`"/></el-form-item>
            <el-form-item label="用户类型"><el-select v-model="form.userType"><el-option label="学生" value="student"/><el-option label="教职工" value="staff"/></el-select></el-form-item>
            <el-form-item label="登录账号 *"><el-input v-model="form.account" placeholder="请输入登录账号"/></el-form-item>
            <el-form-item class="permission-choice" label="账号权限 *"><el-radio-group v-model="form.roleCodes[0]"><el-radio-button value="normal_user">普通账户</el-radio-button><el-radio-button value="knowledge_admin" :disabled="form.userType==='student' || !isSuperAdmin">管理员账户</el-radio-button></el-radio-group></el-form-item>
          </div>
        </template>
        <template v-else>
          <div class="filter-top"><el-form-item label="姓名 *"><el-input v-model="form.name"/></el-form-item><el-form-item label="账号"><el-input v-model="form.account" disabled/></el-form-item><el-form-item label="学号 / 工号"><el-input v-model="form.studentOrStaffNo"/></el-form-item><el-form-item label="用户类型"><el-select v-model="form.userType"><el-option label="教职工" value="staff"/><el-option label="学生" value="student"/><el-option v-if="form.userType==='external'" label="外部人员（仅访客入口）" value="external" disabled/></el-select></el-form-item></div>
          <div class="filter-top"><el-form-item label="所属部门"><el-select v-model="form.departmentId" clearable :disabled="form.userType==='external'"><el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id"/></el-select></el-form-item><el-form-item label="邮箱"><el-input v-model="form.email"/></el-form-item><el-form-item label="手机号"><el-input v-model="form.phone"/></el-form-item><el-form-item label="角色"><el-select v-model="form.roleCodes" multiple :disabled="['student','external'].includes(form.userType) || !isSuperAdmin"><el-option v-for="item in roles" :key="item.code" :label="item.name" :value="item.code"/></el-select><small v-if="['student','external'].includes(form.userType)" class="role-help">该类型只能是普通用户</small><small v-else-if="!isSuperAdmin" class="role-help">管理员角色只能由超级管理员授权</small></el-form-item></div>
        </template>
      </el-form>
      <template #footer><el-button @click="editOpen=false">取消</el-button><el-button class="primary-btn" :loading="saving" @click="saveUser">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.users-head{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;margin-bottom:17px}.users-head>div:first-child>div{display:flex;align-items:center;gap:9px}.users-head h1{margin:0;font-size:27px}.users-head p{margin:7px 0 0;color:#64748b;font-size:11px}.users-head p b{color:#0f172a;font-size:14px}.users-head>div:last-child{display:flex;gap:8px}.user-metrics{margin-bottom:15px}.user-metrics :deep(.metric-card){min-height:105px}.user-filters{padding:14px;margin-bottom:14px}.filter-top{display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr;gap:8px}.role-chips{display:flex;align-items:center;gap:7px;padding-top:11px;margin-top:11px;border-top:1px solid #eff4ff;overflow:auto}.role-chips>b{color:#94a3b8;font-size:9px;white-space:nowrap}.role-chips button{padding:6px 10px;border:0;border-radius:999px;background:#f8fafc;color:#64748b;font-size:9px;white-space:nowrap;cursor:pointer}.role-chips button.active{background:#2a2e35;color:white}.role-chips>span{margin-left:auto;color:#94a3b8;font-size:8px;white-space:nowrap}.users-table{overflow:hidden}.user-cell{display:flex;align-items:center;gap:9px}.user-cell>span{width:34px;height:34px;display:grid;place-items:center;border-radius:50%;background:#1e2024;color:#fff;font-size:10px;font-weight:800}.user-cell>div{display:flex;flex-direction:column}.user-cell b{font-size:10px}.user-cell small{margin-top:3px;color:#64748b;font-size:8px}.table-sub{margin:3px 0 0;color:#94a3b8;font-size:8px}.role-tag{display:inline-flex;padding:5px 7px;border-radius:999px;background:#eff4ff;color:#0051d5;font-size:8px}.scope-tags{display:flex;flex-wrap:wrap;gap:4px}.scope-tags span{padding:4px 6px;border-radius:5px;background:#f1f5f9;color:#475569;font-size:8px}.users-table>footer{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;color:#64748b;font-size:9px}.import-title{display:flex;align-items:center;gap:10px;padding-right:42px;min-width:0}.import-title>span{width:38px;height:38px;flex:none;display:grid;place-items:center;border-radius:10px;background:#eff4ff;color:#0051d5}.import-title>div{flex:1;min-width:0}.import-title h3{margin:0;font-size:15px}.import-title p{margin:4px 0 0;color:#64748b;font-size:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.import-title :deep(.import-review-status){position:relative;z-index:1;flex:none;min-width:max-content;overflow:hidden;isolation:isolate;box-shadow:0 0 0 1px rgba(0,81,213,.04) inset}.import-title :deep(.import-review-status i){flex:none}.import-guide{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:13px 15px;margin-bottom:14px;border-radius:10px;background:#eff4ff}.import-guide b{font-size:11px}.import-guide p{margin:4px 0 0;color:#64748b;font-size:9px}.import-file{width:100%;padding:12px;margin-bottom:14px;border:1px dashed #cbd5e1;border-radius:9px;background:#f8fafc}.import-errors{max-height:110px;overflow:auto;padding:9px 12px;margin-top:10px;border-radius:8px;background:#fef2f2;color:#b91c1c;font-size:9px}.import-errors p{margin:3px 0}.import-actions{display:flex;justify-content:flex-end;gap:7px;margin:14px 0}.import-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.import-stats>div{padding:12px;border-radius:9px;background:#f8fafc}.import-stats small,.import-stats b,.import-stats span{display:block}.import-stats small{color:#64748b;font-size:8px}.import-stats b{margin:5px 0;font-size:19px}.import-stats span{color:#94a3b8;font-size:8px}.import-stats .success{background:#ecfdf5}.import-stats .success b{color:#059669}.import-stats .danger{background:#fef2f2}.import-stats .danger b{color:#dc2626}.student-create-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}.permission-choice{grid-column:1/-1}.permission-choice :deep(.el-radio-group){width:100%}.permission-choice :deep(.el-radio-button){flex:1}.permission-choice :deep(.el-radio-button__inner){width:100%}.role-help{display:block;margin-top:5px;color:#64748b;font-size:9px}.conflict-head{display:flex;align-items:center;justify-content:space-between;padding:13px 0 8px;margin-top:10px;border-bottom:1px solid #eff4ff}.conflict-head>div{display:flex;align-items:center;gap:7px;color:#dc2626}.conflict-head span{display:flex;align-items:baseline;gap:5px}.conflict-head b{font-size:10px}.conflict-head small{color:#64748b;font-size:8px}.conflict-head p{margin:0;color:#94a3b8;font-size:8px}.conflict{display:flex;align-items:center;gap:9px;padding:10px;margin-top:8px;border-radius:9px;background:#fef2f2}.conflict>span{padding:4px 6px;border-radius:5px;background:#fff;color:#dc2626;font-size:8px;font-weight:800}.conflict>div{flex:1}.conflict b{font-size:9px}.conflict p{margin:3px 0 0;color:#7f1d1d;font-size:8px}
@media(max-width:1050px){.filter-top{grid-template-columns:1fr 1fr}.users-table :deep(.el-table){min-width:1080px}.users-table{overflow:auto}.users-head{align-items:flex-start;flex-direction:column}}
@media(max-width:700px){.filter-top,.student-create-grid{grid-template-columns:1fr}.users-head>div:last-child .el-button:first-child{display:none}.users-table>footer{align-items:flex-start;flex-direction:column;gap:10px}.import-stats{grid-template-columns:1fr 1fr}.import-guide{align-items:flex-start;flex-direction:column}.conflict{align-items:flex-start;flex-wrap:wrap}.conflict>div{flex-basis:70%}.conflict-head{align-items:flex-start;flex-direction:column;gap:5px}.import-actions .el-button:first-child{display:none}.import-actions .primary-btn{flex:1}}
</style>
