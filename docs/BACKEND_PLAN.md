# 校园知识库问答系统后端规划（基于现有前端反向设计）

> 文档状态：后端实现基线（核心 API、迁移、worker 与模型适配已于 2026-10-09 落地）  
> 分析日期：2026-10-08  
> 需求事实来源：`frontend/` 当前源码、`docs/PRD.md`  
> 当前边界：后端已实现；前端联调改造仍按第 16 节单独进行

> 实现说明：`backend/` 已覆盖第 8 节列出的全部路由，包含 PostgreSQL/Alembic、RBAC/ACL、文档流水线、Milvus、Celery、SSE、反馈/通知/分析/审计。模型链路采用 Qwen `text-embedding-v4`（1024 维）与 DeepSeek `deepseek-flash`，均通过环境变量独立配置。测试环境使用无外部依赖的 provider，生产默认启用真实 provider。

## 1. 结论摘要

当前 `frontend/` 是 Vue 3 + Vue Router + Element Plus + Vite 的高保真交互原型。项目没有 API 客户端、状态管理层、类型层、环境变量示例、开发代理或路由鉴权；没有任何 Axios、`fetch`、WebSocket 或 SSE 请求。页面中的业务数据全部是组件内硬编码数组/模板文本，提交行为主要由 `setTimeout`、本地 `ref` 和 `ElMessage` 模拟。

因此，不存在必须兼容的既有 API URL；后端可以统一采用 `/api/v1`。但是页面已明确使用的数据字段、状态、交互和 PRD 中的权限约束必须作为契约来源。建议后端采用模块化单体：FastAPI + PostgreSQL + Milvus + Redis + Celery，文件保存在可切换的本地/S3 兼容对象存储中，问答流使用 SSE。该结构足以支撑当前规模，同时避免过早拆分微服务。

## 2. 已扫描范围与前端架构

### 2.1 已扫描文件

- 工程配置：`frontend/package.json`、`frontend/package-lock.json`、`frontend/vite.config.js`、`frontend/index.html`、`frontend/README.md`。
- 应用入口：`frontend/src/main.js`、`frontend/src/App.vue`、`frontend/src/router.js`。
- 公共组件：`AppShell.vue`、`PageHeader.vue`、`MetricCard.vue`、`StatusPill.vue`。
- 全部 11 个页面组件：登录、首页、对话、历史、来源、管理概览、知识库、上传、文档详情、用户、问答与反馈。
- 全局样式：`frontend/src/styles/theme.css`。
- 产品文档：`docs/PRD.md` 全文。
- `frontend/dist/` 是构建产物，不作为需求的独立来源。

### 2.2 技术与目录现状

| 项目 | 当前情况 | 后端/联调影响 |
| --- | --- | --- |
| 框架 | Vue 3.5、Vue Router 4.5、Element Plus 2.10、Vite 7 | 后端提供 JSON REST + SSE 即可 |
| 状态管理 | 无 Pinia/Vuex | 登录态、用户信息、会话流后续需增加轻量 store 或 composable |
| 请求层 | 无 `api/`、`services/`，无 Axios/fetch | 没有既有 URL 需要兼容；需新增统一请求层 |
| 类型层 | 无 TypeScript、`types/`、`interfaces/` | 契约应先固化为 OpenAPI，再按需生成前端类型 |
| 环境变量 | 无 `.env.example` | 联调阶段需要 `VITE_API_BASE_URL` 等配置 |
| Vite 代理 | 未配置 | 开发期可用 Vite `/api` 代理，生产同源反代 |
| 路由鉴权 | 无 `beforeEach` 权限判断 | 后端仍必须执行所有鉴权；前端后续补路由体验层守卫 |
| 路由参数 | 对话、来源、知识库、文档均无业务 ID | 联调时需最小调整为路径参数或 query 参数 |
| 实时通信 | 无 WebSocket/SSE | 问答流和任务状态需新增 SSE 客户端 |

### 2.3 应用框架数据需求

`AppShell.vue` 当前硬编码用户“李明”、部门和管理员身份，并静态显示“RAG 引擎正常”。顶部全局搜索、通知、消息按钮均无行为。后端需要提供：

- 当前用户 `/auth/me`；
- 按权限生成的导航能力或前端可判断的 permission codes；
- 服务状态摘要；
- 全局搜索；
- 未读通知数量和通知列表；
- 登录退出与普通端/管理端切换依据。

## 3. 页面、功能与数据流分析

### 3.1 已实现页面

| 路由/文件 | 当前功能 | 当前数据与交互 | 推导出的后端能力 |
| --- | --- | --- | --- |
| `/login` `LoginView.vue` | SSO 学生/教职工入口；本地账号、密码、四位验证码、记住 30 天 | 本地校验后 `setTimeout` 直接进入 `/home`；验证码固定；SSO 也是延时跳转 | 本地登录、动态验证码、SSO/CAS 跳转与回调、refresh session、账号状态/锁定、当前用户 |
| `/home` `HomeView.vue` | 欢迎信息、当前检索范围、业务分类、热门问题、知识库选择、500 字提问、常用文档 | 分类和问题均为数组；提交仅跳转 `/chat`，问题和知识库没有传递 | 首页聚合、可访问知识库、推荐分类/问题、快速文档、创建会话并发送首问 |
| `/chat` `ChatView.vue` | 会话标题/知识库、消息、检索诊断、流式状态外观、引用、推荐追问、评价、重生成、停止 | 全部内容静态；发送只切换 1.3 秒 loading；点赞仅本地布尔值 | 会话 CRUD、消息持久化、RAG 检索、SSE 流、停止/重生成、引用、评价/反馈、收藏、清空 |
| `/history` `HistoryView.vue` | 会话搜索、机构/时间筛选、分组、分页、打开/重命名/删除外观、洞察 | 5 条静态数组；只有关键词本地过滤；时间/机构/分页无效；删除只关闭弹窗 | 本人会话分页、全文搜索、筛选、重命名、收藏、软删除、会话统计与最近引用 |
| `/source` `SourceView.vue` | 回答与引用对照、引用切换、文档元数据、原文定位/高亮、缩放、下载、过期反馈 | 全部静态；只实现缩放和返回 | 引用详情、相邻引用、受权原文上下文、预览/下载权限、文档下载、引用失效反馈 |
| `/admin/overview` `OverviewView.vue` | 指标、问答趋势、满意度、分类、知识缺口、异常任务 | 全部静态 SVG/数组，周期切换无状态 | 权限范围内的仪表盘聚合、趋势、未命中聚类、异常任务、报告导出 |
| `/admin/knowledge-bases` `KnowledgeBaseView.vue` | 知识库卡片/表格、筛选、配置抽屉、ACL、检索参数、检索测试、重建/新建入口 | 4 条静态数组；搜索和筛选不生效；保存只 Toast；抽屉总是固定第一库 | 知识库 CRUD、状态、ACL、检索配置、统计、重建任务、检索测试 |
| `/admin/upload` `UploadView.vue` | 多文件选择、队列、进度外观、元数据、OCR/切片配置、草稿/提交 | `action="#"` 且 `auto-upload=false`；仅本地追加文件；两条假进度 | 上传规则、单文件上传进度、重复检查、上传暂存、批量入库、草稿、异步处理任务 |
| `/admin/document` `DocumentView.vue` | 文档元数据、处理时间线、切片列表、切片检索、RAG 预览、下载/停用/新版本/重处理 | 文档和切片全部静态；检索只 Toast | 文档详情/版本/下载/状态、处理历史、切片分页/编辑、文档内检索测试、重处理任务 |
| `/admin/users` `UsersView.vue` | 用户统计、筛选、分页、角色/范围/状态、导入校验、启停/编辑/新建/导出外观 | 4 条静态数组；搜索/筛选不生效；导入弹窗默认打开；确认只 Toast | 用户/角色/部门 CRUD 与分页、权限范围、导入预校验和冲突处理、异步导入、导出、审计 |
| `/admin/feedback` `FeedbackView.vue` | 问答记录与反馈两标签、筛选、详情、处理结论/备注/通知用户 | 两组静态数组；关键词仅本地过滤；其他筛选无效；保存不更新记录 | 管理问答审计、反馈 CRUD/领取/解决、关联文档、通知用户、质量指标/导出 |

### 3.2 PRD 中存在但当前未形成完整页面的能力

| PRD 页面/能力 | 前端现状 | 后端规划处理 |
| --- | --- | --- |
| 忘记密码、403、404、服务异常 | 无路由；未知路由直接回 `/home` | 预留认证恢复和标准错误契约；前端联调期补页面 |
| 个人中心、使用帮助 | 无路由 | `/auth/me`、修改密码/资料、系统帮助内容接口 |
| 知识库新建/详情 | 配置抽屉部分覆盖，无真实详情路由 | 按完整资源 API 设计，前端可先复用抽屉 |
| 文档列表 | 导航“文档中心”直接进入一条静态文档详情 | 提供完整文档分页 API，前端后续补列表 |
| 处理任务中心 | 导航“任务中心”错误指向上传页 | 提供任务 API 和 SSE/轮询，前端后续补列表 |
| 用户详情 | 无路由 | 提供用户详情、登录记录、角色、使用概览 API |
| 系统设置、操作日志 | AppShell 有设置占位；无页面 | 仅超级管理员 API；前端后续补页面 |
| 通知中心 | 顶栏只有图标 | 提供未读数、列表、已读操作 |

### 3.3 页面之间的业务关系

1. 登录成功后加载当前用户、角色、权限和可访问知识库。
2. 首页选择知识范围并发送问题，创建会话和首条消息，进入对话详情。
3. 对话详情持续发送消息；助手消息通过 SSE 接收阶段、文本增量、引用和完成事件。
4. 引用从消息跳转到来源预览；预览必须再次校验文档预览/下载权限。
5. 对话自动进入历史记录；用户可检索、重命名、收藏或删除自己的会话。
6. 管理员创建知识库、配置 ACL，上传文档并启动异步入库。
7. 任务按解析、OCR、切片、向量化、索引发布推进；成功文档才参与问答。
8. 用户对回答评价或报告来源过期，形成反馈；管理员处理后可关联修复文档并通知用户。
9. 问答、反馈、文档和任务数据聚合为管理概览和质量分析。

## 4. 已发现的 API、mock 与未实现行为

### 4.1 真实 API 扫描结果

- 未发现 `/api/*` URL。
- 未发现 Axios、`fetch()`、`XMLHttpRequest`、`EventSource` 或 `WebSocket`。
- `package.json` 没有请求库或 mock 框架依赖。
- `vite.config.js` 没有代理配置。
- 未发现 MSW、json-server、fixture 或独立 JSON mock。

结论：现有前端的所有业务数据均是“组件内 mock”，而不是“已约定但后端未实现的 API”。

### 4.2 mock/假交互清单

- `LoginView.vue`：固定验证码、模拟本地登录、模拟 SSO。
- `HomeView.vue`：分类、热门问题、检索范围、常用文件、性能指标。
- `ChatView.vue`：会话、问题、回答、RAG 分数、切片、引用、追问和生成过程。
- `HistoryView.vue`：会话列表、洞察、引用文件、咨询分布；删除未持久化。
- `SourceView.vue`：问答、文档、页码、切片、权限和下载信息。
- `OverviewView.vue`：全部指标、图表、未命中和异常任务。
- `KnowledgeBaseView.vue`：知识库列表、统计、配置、ACL 和测试结果。
- `UploadView.vue`：文件、进度、预估切片和提交结果。
- `DocumentView.vue`：文档、处理时间线、切片与检索结果。
- `UsersView.vue`：用户、统计、导入校验和冲突。
- `FeedbackView.vue`：问答记录、反馈、处理状态和质量指标。
- `AppShell.vue`：当前用户、引擎状态、全局搜索、通知/消息。

## 5. 后端功能模块

以下模块均可由现有页面或 PRD 直接追溯，不增加无关业务：

1. 系统启动配置：系统名称、功能开关、登录方式、上传限制、问题长度、服务状态。
2. 认证与会话：本地登录、验证码、CAS/OIDC SSO、access token、refresh session、退出、当前用户。
3. 用户、组织与 RBAC：用户、部门、角色、权限、账号状态、导入/导出、角色分配。
4. 知识库与 ACL：知识库 CRUD、状态、检索参数、负责人、角色/部门/用户级可见范围。
5. 文件与文档：安全上传、哈希去重、元数据、版本、预览、下载、启停/过期。
6. 文档处理任务：解析、OCR、切片、Embedding、索引发布、失败重试、任务事件。
7. 检索与 RAG：权限过滤后的混合检索、重排、无答案阈值、引用生成、检索测试。
8. 会话与消息：会话 CRUD、多轮消息、SSE 流、停止、重生成、收藏、历史搜索。
9. 引用溯源：消息引用、文档位置、上下文、相邻引用、预览/下载权限。
10. 评价与反馈：点赞/点踩、原因、引用过期报告、分配/处理/解决、用户通知。
11. 运营分析：概览指标、趋势、热门问题、未命中、异常任务、质量报告。
12. 全局搜索与推荐：首页推荐、热门问题、受权知识库/文档搜索。
13. 通知：任务完成/失败、导入结果、反馈处理和系统公告。
14. 系统设置与审计：上传/问答规则、服务配置状态、高风险操作日志。

## 6. 推荐技术方案

### 6.1 结构

采用“模块化单体 + 独立后台 worker”，暂不拆微服务：

- API：Python 3.12、FastAPI、Pydantic 2。
- ORM/迁移：SQLAlchemy 2、Alembic。
- 主数据库：PostgreSQL 16，保存业务数据、切片正文、权限和任务状态，不保存向量列。
- 向量检索：固定使用 Milvus，通过 `pymilvus` 接入；不提供 pgvector、Qdrant 或其他向量库后端作为运行时替代方案。
- 稀疏检索：PostgreSQL 全文/`pg_trgm` 与中文分词后的检索字段；结果与 Milvus 稠密向量分数融合。
- 缓存与队列：Redis 7 + Celery；任务状态同时持久化到 PostgreSQL。
- 文件：开发环境本地目录，生产环境 S3/MinIO 兼容对象存储，通过统一 storage adapter 切换。
- 实时问答：SSE；停止生成使用普通 POST。当前交互是一方向服务端推送，不需要 WebSocket。
- 文档解析：PyMuPDF/PDF 解析、python-docx、TXT/Markdown；扫描件通过可配置 OCR provider。
- 密码：Argon2id。
- 测试：pytest、pytest-asyncio、httpx；PostgreSQL/Redis 可使用 testcontainers，Milvus 集成测试使用固定版本的 Docker Compose 环境。

### 6.2 Milvus 部署与数据边界

- 开发和自动化集成测试使用 Milvus Standalone，并在 `docker-compose.yml` 中固定镜像版本；生产首期也采用独立的 Milvus Standalone 服务，容量或可用性要求达到阈值后再切换 Milvus Distributed。
- Milvus 所需的 etcd 和内部对象存储随 Milvus 部署管理，不与校园业务文件共用 bucket、访问密钥或生命周期策略。
- PostgreSQL 是切片、权限和索引状态的事实来源；Milvus 只保存可由 PostgreSQL 与原始文件重建的派生向量及必要过滤字段。
- Milvus 连接地址、认证信息、数据库名、collection 前缀和超时通过环境变量配置；认证信息不得写入代码或普通日志。
- embedding 模型或维度变更时创建新 collection，完成全量构建和校验后通过 alias 切换；禁止在同一 collection 中混用不同维度或模型版本。

### 6.3 为什么不直接拆微服务

当前只有一个前端、一个权限域和一条文档入库流水线。模块化单体可以共享事务、权限判断、审计和 DTO；Celery worker 已足够隔离耗时任务。后续只有在处理吞吐或组织边界明确后再拆解析、检索或模型网关。

## 7. API 统一约定

### 7.1 基础约定

- Base URL：`/api/v1`。
- JSON 字段：统一 `camelCase`，避免同一字段出现 `userName`/`username` 两套命名。
- 资源 ID：后端生成 UUID；学号/工号使用独立字段 `studentOrStaffNo`，不能与资源 ID 混用。
- 时间：数据库保存 UTC，API 返回带时区的 ISO 8601，例如 `2026-10-08T08:30:00Z`；前端按 Asia/Shanghai 展示。
- 数字：计数、大小、百分比均返回数值，千分位和单位由前端格式化。
- 幂等：创建消息、提交入库、重建索引、批量导入接受 `Idempotency-Key`。
- 删除：知识库、文档、会话默认软删除；原始文件物理清理由保留策略异步执行。

### 7.2 成功响应

单对象：

```json
{
  "data": {},
  "requestId": "req_..."
}
```

分页列表：

```json
{
  "data": [],
  "pagination": {
    "page": 1,
    "pageSize": 20,
    "total": 28468,
    "totalPages": 1424
  },
  "requestId": "req_..."
}
```

### 7.3 错误响应

```json
{
  "error": {
    "code": "AUTH_INVALID_CREDENTIALS",
    "message": "账号或密码错误",
    "fieldErrors": {
      "account": "请检查账号"
    },
    "details": null
  },
  "requestId": "req_..."
}
```

HTTP 状态码：`200/201/202/204` 成功；`400` 格式或业务参数错误；`401` 未登录/会话失效；`403` 权限不足；`404` 不存在或为了防泄露而隐藏；`409` 重复/状态冲突；`413` 文件过大；`415` 格式不支持；`422` 字段校验；`429` 限流；`500/503` 服务异常。

### 7.4 分页、搜索、排序

- 通用 query：`page`、`pageSize`、`q`、`sortBy`、`sortOrder=asc|desc`。
- 默认 `page=1`、`pageSize=20`，上限 100。
- 列表均返回 `pagination`，不能在前端硬编码总数。
- 仅允许后端白名单字段排序，禁止把任意用户输入拼接到 SQL。

## 8. 初步 API 设计

以下 Response 列只列核心 `data`，均包在统一响应中。权限代码在第 11 节定义。

### 8.1 系统、认证与个人信息

| Method | URL | Request | Response | 登录/权限 | 对应前端 |
| --- | --- | --- | --- | --- | --- |
| GET | `/system/bootstrap` | 无 | 系统名称、登录方式、验证码策略、问题/上传限制、功能开关 | 否 | 登录页、首页、上传页 |
| GET | `/auth/captcha` | 无 | `captchaId`、`imageDataUrl`、`expiresAt` | 否 | 登录页验证码 |
| POST | `/auth/login` | `account,password,captchaId,captchaCode,remember` | `accessToken,expiresIn,user`；设置 refresh cookie | 否 | 本地登录 |
| GET | `/auth/sso/authorize` | `channel=student|staff,returnTo` | 302 到 CAS/OIDC | 否 | 两个 SSO 按钮 |
| GET | `/auth/sso/callback` | SSO 回调参数 | 建立会话并 302 回前端 | 否 | SSO 回调 |
| POST | `/auth/refresh` | HttpOnly refresh cookie | 新 access token，并轮换 refresh token | 否 | 静默续期 |
| POST | `/auth/logout` | 无 | 204，撤销当前 session/cookie | 是 | 个人菜单 |
| GET | `/auth/me` | 无 | 当前用户、角色、权限、可访问管理范围 | 是 | AppShell、路由、个人中心 |
| PATCH | `/me` | 可编辑手机号/邮箱等 | 更新后的用户 | 是 | PRD U05 |
| POST | `/me/password` | `oldPassword,newPassword` | 204；SSO 账号返回不可本地修改错误 | 是 | PRD U05 |

### 8.2 首页、全局搜索与推荐

| Method | URL | Request | Response | 登录/权限 | 对应前端 |
| --- | --- | --- | --- | --- | --- |
| GET | `/home` | 无 | `greeting,accessibleKnowledgeBases,categories,hotQuestions,quickDocuments,serviceStatus` | 是 | HomeView 全部动态内容 |
| GET | `/search` | `q,type,page,pageSize` | 受权的文档、知识库、问答/帮助结果 | 是 | AppShell 全局搜索 |
| GET | `/knowledge-bases/options` | 可选 `selectable=true` | 用户可检索的知识库简表 | 是 | 首页/对话选择器 |

### 8.3 会话、消息、流式回答与评价

| Method | URL | Request | Response | 登录/权限 | 对应前端 |
| --- | --- | --- | --- | --- | --- |
| POST | `/conversations` | `knowledgeBaseIds,title?` | 新会话 | 是 | 首页发起、新建对话 |
| GET | `/conversations` | `q,kbId,timeRange,status,favorite,page,pageSize` | 本人的会话摘要分页 | 是；只看本人 | HistoryView |
| GET | `/conversations/{conversationId}` | 无 | 会话头、知识范围、消息列表或首屏游标 | 是；所有者/审计权限 | ChatView |
| PATCH | `/conversations/{conversationId}` | `title?,favorite?` | 更新后的会话 | 是；所有者 | 重命名/收藏 |
| DELETE | `/conversations/{conversationId}` | 无 | 204 | 是；所有者 | 历史删除 |
| DELETE | `/conversations/{conversationId}/messages` | `confirm=true` | 清空结果 | 是；所有者 | 对话“清空记录” |
| GET | `/conversations/{conversationId}/messages` | `cursor,limit` | 消息列表、`nextCursor` | 是；所有者/审计权限 | 长会话加载 |
| POST | `/conversations/{conversationId}/messages` | `content,knowledgeBaseIds?,clientRequestId` | `userMessage,assistantMessage,streamUrl`，202 | 是；所有者 | 首问/追问 |
| GET | `/messages/{messageId}/events` | 支持 `Last-Event-ID` | SSE 事件流 | 是；消息可见 | ChatView 生成过程 |
| POST | `/messages/{messageId}/stop` | 无 | 当前消息状态 | 是；所有者 | 停止生成 |
| POST | `/messages/{messageId}/regenerations` | `replaceCurrent=false` | 新助手消息版本和 `streamUrl` | 是；所有者 | 重新生成 |
| PUT | `/messages/{messageId}/rating` | `value=helpful|unhelpful|none,reason?,comment?` | 当前评价/反馈 ID | 是；所有者 | 赞同/不满意 |
| GET | `/messages/{messageId}/citations` | 无 | 受权引用列表 | 是；消息可见 | 回答引用、重试引用 |

SSE 事件：

| event | 核心 data | 用途 |
| --- | --- | --- |
| `stage` | `messageId,status,stage,label,sequence` | 理解、检索、整理、生成、核对引用 |
| `delta` | `messageId,text,sequence` | 增量回答文本 |
| `citation` | `messageId,citation` | 引用可用时增量加入 |
| `suggestions` | `items[]` | 推荐追问 |
| `completed` | 完整消息、引用、耗时、检索摘要 | 原子确认完成状态 |
| `stopped` | 已保留内容和状态 | 用户停止 |
| `error` | 稳定错误码、可展示消息、可否重试 | 失败/中断 |
| `heartbeat` | 时间戳 | 维持连接，不更新 UI 文本 |

SSE 断线后使用 `Last-Event-ID` 续传；页面刷新先 GET 会话/消息状态，禁止重新提交问题。

### 8.4 引用、来源预览与文件下载

| Method | URL | Request | Response | 登录/权限 | 对应前端 |
| --- | --- | --- | --- | --- | --- |
| GET | `/citations/{citationId}` | 无 | 引用、文档元数据、命中位置、原文和上下文、相邻引用 ID、权限 | 是；可预览 | SourceView |
| GET | `/documents/{documentId}/preview` | `page?,chunkId?` | 按页/切片预览和定位信息 | 是；`document.preview` | 来源预览 |
| GET | `/documents/{documentId}/download` | 无 | 原文件流/短时签名 URL | 是；`document.download` | 下载官方原件 |
| POST | `/citations/{citationId}/reports` | `type=outdated|incorrect|other,comment?` | 反馈记录 | 是 | “条款已失效/过时” |

### 8.5 管理概览与报告

| Method | URL | Request | Response | 登录/权限 | 对应前端 |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/dashboard` | `range=today|7d|30d` | 指标、趋势、分类、满意度、部分失败信息 | `dashboard.read` | OverviewView |
| GET | `/admin/knowledge-gaps` | `range,status,page,pageSize` | 未命中聚类、次数、库、最高分、建议动作 | `qa.analytics` | 知识缺口 |
| GET | `/admin/anomalies` | `type,page,pageSize` | OCR/超时/失败任务和服务异常 | `task.read` | 异常任务 |
| POST | `/admin/reports` | `type,range,filters` | 导出任务 ID，202 | 对应导出权限 | 导出分析报告 |

### 8.6 知识库

| Method | URL | Request | Response | 登录/权限 | 对应前端 |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/knowledge-bases` | `q,status,departmentId,accessType,creatorId,updatedFrom,updatedTo,page,pageSize` | 知识库分页与统计摘要 | `kb.read` 且按管理范围 | 列表/筛选 |
| POST | `/admin/knowledge-bases` | 名称、code、描述、部门、分类、ACL、检索配置 | 新知识库，201 | `kb.create` | 新建 |
| GET | `/admin/knowledge-bases/{id}` | 无 | 完整信息、统计、ACL、检索配置 | `kb.read` 且在范围 | 配置抽屉/详情 |
| PATCH | `/admin/knowledge-bases/{id}` | 可编辑字段 + `version` | 更新结果 | `kb.update` 且在范围 | 保存配置 |
| POST | `/admin/knowledge-bases/{id}/status` | `status=enabled|disabled,reason` | 状态和影响摘要 | `kb.status` | 启停 |
| DELETE | `/admin/knowledge-bases/{id}` | `confirmName` | 204 或冲突原因 | `kb.delete` | 删除；默认软删除 |
| GET | `/admin/knowledge-bases/{id}/acl` | 无 | ACL 条目 | `kb.permission.read` | 可见范围 |
| PUT | `/admin/knowledge-bases/{id}/acl` | 完整 ACL + `version` | 新 ACL | `kb.permission.update` | ACL 保存 |
| POST | `/admin/knowledge-bases/{id}/rebuild-index` | `reason` | 任务 ID，202 | `kb.rebuild` | 批量重建索引 |
| POST | `/admin/knowledge-bases/{id}/retrieval-tests` | `query,topK?,threshold?,rerank?,hybrid?` | ranked chunks、分数、测试回答、耗时 | `kb.test` | 即时检索测试 |

### 8.7 上传、文档、版本与切片

| Method | URL | Request | Response | 登录/权限 | 对应前端 |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/document-upload-config` | `knowledgeBaseId?` | 格式、大小、数量、分类、默认解析参数 | `document.create` | UploadView 动态规则 |
| POST | `/admin/uploads` | 单文件 multipart + `clientFileId` | `uploadId,fileName,size,sha256,duplicate,status` | `document.create` | 每文件真实上传/进度 |
| DELETE | `/admin/uploads/{uploadId}` | 无 | 204 | 上传所有者 | 取消暂存 |
| POST | `/admin/document-ingestions` | `items[{uploadId,metadata}],processingConfig,saveAsDraft` | 文档和任务列表，201/202 | `document.create` 且库范围 | 批量提交/草稿 |
| GET | `/admin/documents` | PRD 全部筛选 + 分页 | 文档分页 | `document.read` 且范围 | 文档列表/后续 A05 |
| GET | `/admin/documents/{id}` | 无 | 元数据、版本、权限、统计、当前处理状态 | `document.read` | DocumentView |
| PATCH | `/admin/documents/{id}` | 元数据 + `version` | 更新后的文档 | `document.update` | 编辑信息 |
| POST | `/admin/documents/{id}/versions` | `uploadId,switchPolicy` | 新版本及任务，202 | `document.update` | 上传新版本 |
| POST | `/admin/documents/{id}/reprocess` | OCR/切片配置、原因 | 任务 ID，202 | `document.reprocess` | 重新切片与向量化 |
| POST | `/admin/documents/{id}/status` | `status=enabled|disabled,reason` | 状态 | `document.status` | 停用/启用 |
| DELETE | `/admin/documents/{id}` | `confirmName` | 204 | `document.delete` | 删除；默认软删除 |
| GET | `/admin/documents/{id}/timeline` | 无 | 上传到入库的阶段事件 | `document.read` | 生命周期时间线 |
| GET | `/admin/documents/{id}/chunks` | `q,pageNumber,section,page,pageSize` | 切片分页 | `document.read` | 切片清单/搜索 |
| PATCH | `/admin/chunks/{chunkId}` | `content,reason,version` | 切片与重向量任务 ID | `chunk.update` | 编辑切片 |
| POST | `/admin/documents/{id}/retrieval-tests` | `query,topK,minScore` | ranked chunks、测试答案、耗时 | `kb.test` | 文档内检索调试 |

上传采用“一文件一个请求 + 之后批量提交元数据”。这样可独立显示/重试每个文件的进度，单文件失败不影响其他文件，也能在正式入库前做哈希重复检查。

### 8.8 处理任务

| Method | URL | Request | Response | 登录/权限 | 对应前端 |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/tasks` | `taskNo,type,kbId,documentName,status,creatorId,createdFrom,createdTo,page,pageSize` | 任务分页 | `task.read` 且范围 | 任务中心/概览 |
| GET | `/admin/tasks/{id}` | 无 | 阶段、nullable 进度、事件、错误和关联对象 | `task.read` | 任务详情 |
| GET | `/admin/tasks/{id}/events` | `Last-Event-ID` | SSE 任务事件 | `task.read` | 实时更新；也可轮询详情 |
| POST | `/admin/tasks/{id}/retry` | `fromStage?` | 新任务 ID，202 | `task.retry` | 失败重试 |
| POST | `/admin/tasks/{id}/cancel` | 无 | 当前状态 | `task.cancel` | 等待中取消 |

### 8.9 用户、角色、部门和导入导出

| Method | URL | Request | Response | 登录/权限 | 对应前端 |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/users` | `q,userType,departmentId,role,status,createdFrom,lastLoginFrom,page,pageSize` | 用户分页 | `user.read` | UsersView |
| GET | `/admin/users/stats` | 同筛选范围 | 教师、学生、活跃、异常统计 | `user.read` | 用户指标卡 |
| POST | `/admin/users` | 姓名、账号、学工号、类型、部门、联系方式、角色、状态 | 新用户，201 | `user.create` | 新建管理员/用户 |
| GET | `/admin/users/{id}` | 无 | 基本资料、角色、知识库范围、登录概览 | `user.read` | PRD A10 |
| PATCH | `/admin/users/{id}` | 可编辑字段 + `version` | 更新用户 | `user.update` | 编辑 |
| POST | `/admin/users/{id}/status` | `status,reason` | 状态 | `user.status` | 启用/禁用 |
| PUT | `/admin/users/{id}/roles` | `roleCodes,reason,version` | 角色列表 | `role.assign`（超级管理员） | 分配角色 |
| POST | `/admin/users/{id}/password-reset` | `deliveryMethod` | 不返回明文密码的交付结果 | `user.password.reset` | 重置密码 |
| GET | `/admin/roles` | 无 | 角色和权限 | `user.read` | 角色筛选/编辑 |
| GET | `/admin/departments` | `q,parentId?` | 部门树/列表 | `user.read` 或 `kb.read` | 筛选、表单 |
| POST | `/admin/user-imports/validate` | XLSX/CSV multipart | `importId,summary,conflicts,errors` | `user.import` | 导入预校验 |
| POST | `/admin/user-imports/{importId}/commit` | 每个冲突的 `exclude|update|edit` 决策 | 导入任务 ID，202 | `user.import` | 确认导入 |
| POST | `/admin/user-exports` | 当前筛选 | 导出任务 ID，202 | `user.export` | 导出表格 |

### 8.10 管理问答、反馈、通知、设置与审计

| Method | URL | Request | Response | 登录/权限 | 对应前端 |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/qa-records` | PRD A11 筛选 + 分页 | 脱敏问答摘要分页 | `qa.read` 且库范围 | FeedbackView 问答记录 |
| GET | `/admin/qa-records/{id}` | 无 | 完整问答、检索、引用、错误、反馈 | `qa.content.read`；写审计 | 问答详情 |
| GET | `/admin/feedbacks` | `status,type,kbId,userType,assigneeId,submittedFrom,submittedTo,page,pageSize` | 反馈分页 | `feedback.read` 且库范围 | 用户反馈表 |
| GET | `/admin/feedbacks/{id}` | 无 | 反馈、消息、引用、处理历史 | `feedback.read` | 反馈详情 |
| POST | `/admin/feedbacks/{id}/claim` | 无 | 状态/处理人 | `feedback.manage` | 领取处理 |
| POST | `/admin/feedbacks/{id}/resolve` | `resolution,note,relatedDocumentIds,notifyUser` | 已解决反馈 | `feedback.manage` | 保存并标记完成 |
| GET | `/notifications` | `unreadOnly,page,pageSize` | 本人通知分页与未读数 | 是 | 顶栏通知 |
| POST | `/notifications/{id}/read` | 无 | 204 | 本人 | 标记已读 |
| POST | `/notifications/read-all` | 无 | 204 | 本人 | 全部已读 |
| GET | `/admin/settings` | 无 | 非敏感设置和服务配置状态 | `system.read` | PRD A13 |
| PATCH | `/admin/settings` | 设置项 + `version` | 新设置 | `system.update` | 系统设置 |
| GET | `/admin/audit-logs` | 操作人、模块、动作、对象、结果、时间、分页 | 只读审计日志 | `audit.read` | PRD A14 |

## 9. 核心响应对象与前端契约

### 9.1 User

```json
{
  "id": "uuid",
  "account": "zhangwei",
  "name": "张伟",
  "avatarUrl": null,
  "studentOrStaffNo": "20180045",
  "email": "zhangwei@univ.edu.cn",
  "phoneMasked": "138****0000",
  "userType": "faculty",
  "userTypeLabel": "专任教师",
  "subtitle": "教务助理",
  "department": { "id": "uuid", "name": "计算机学院" },
  "roles": [{ "code": "knowledge_admin", "name": "知识库管理员" }],
  "knowledgeBaseScopes": [{ "id": "uuid", "name": "全校教务规章" }],
  "status": "active",
  "statusLabel": "正常",
  "authSource": "sso",
  "lastActiveAt": "2026-10-08T03:24:00Z",
  "createdAt": "2025-09-01T00:00:00Z",
  "updatedAt": "2026-10-08T03:24:00Z"
}
```

### 9.2 KnowledgeBase

核心字段固定为：`id,code,name,description,department,owners,businessCategory,accessSummary,selectable,retrievalConfig,status,statusLabel,documentCount,chunkCount,createdBy,createdAt,updatedAt`。

`retrievalConfig`：`chunkSize,chunkOverlap,topK,similarityThreshold,rerankEnabled,hybridSearchEnabled,promptInstruction`。

### 9.3 Document/Version/Chunk

- Document：`id,knowledgeBase,displayName,category,publishDepartment,documentNo,publishedAt,effectiveAt,expiresAt,tags,accessSummary,currentVersion,status,statusLabel,enabled,chunkCount,uploadedBy,createdAt,updatedAt`。
- Version：`id,versionNo,originalFileName,mimeType,fileSize,sha256,isCurrent,processStatus,processStage,failure,createdAt`。
- Chunk：`id,index,title,content,pageStart,pageEnd,section,charCount,tokenCount,vectorStatus,updatedAt`。

### 9.4 Conversation/Message/Citation

- Conversation：`id,title,knowledgeBases,messageCount,lastMessagePreview,status,favorite,createdAt,updatedAt`。
- Message：`id,conversationId,role,content,status,statusLabel,rating,citations,retrievalSummary,error,createdAt,completedAt`。
- Citation：`id,index,document,knowledgeBase,publishDepartment,documentStatus,pageStart,pageEnd,section,quote,contextBefore,contextAfter,chunkId,similarityScore,canPreview,canDownload`。

### 9.5 Task/Feedback

- Task：`id,taskNo,type,relatedObject,stage,status,progress`（无法计算时为 `null`）、`progressText,error,createdBy,createdAt,startedAt,completedAt`。
- Feedback：`id,messageId,conversationId,type,comment,knowledgeBase,relatedDocuments,status,assignee,resolution,resolutionNote,notifyUser,createdAt,updatedAt,resolvedAt`。

### 9.6 当前 mock 字段到正式契约的核对

当前数组不是既有接口契约，其中部分字段把展示文本、业务编号和资源 ID 混在一起。正式契约按下表固定，联调时只做必要的前端字段替换：

| 当前 mock | 正式字段 | 原因 |
| --- | --- | --- |
| 用户 `id: "20180045"` | `id: UUID` + `studentOrStaffNo` | 资源标识不能与可变业务号混用 |
| `mail` | `email` | 全站统一标准名 |
| `type/sub/dept` | `userType/userTypeLabel/subtitle/department` | 枚举与展示文案分离，部门保留 ID |
| `status: "正常"`、`tone` | `status: "active"` + `statusLabel` | 后端返回稳定枚举；颜色由前端映射 |
| `active: "今天 11:24"` | `lastActiveAt` ISO 时间 | 相对时间由前端按时区生成 |
| 知识库 `docs/chunks` 字符串 | `documentCount/chunkCount` 数字 | 可排序和统计，不返回格式化数字 |
| 知识库 `update/access/desc` | `updatedAt/accessSummary/description` | 避免缩写和歧义 |
| 会话 `count/source/time` | `messageCount/citationCount/updatedAt` | 数值与时间可计算 |
| 文档 `meta/token` 拼接文本 | 独立的页码、字数、token、score 字段 | 不让前端解析展示字符串 |

除这组必要纠正外，后端和前端都统一使用本节字段，不再增加同义别名。

## 10. 状态与枚举契约

| 对象 | API 枚举 |
| --- | --- |
| 用户 | `active`, `pending_activation`, `locked`, `disabled`, `departed` |
| 知识库 | `enabled`, `disabled`, `rebuilding`, `error` |
| 文档处理 | `draft`, `uploading`, `upload_failed`, `queued`, `parsing`, `ocr`, `chunking`, `embedding`, `indexing`, `ready`, `failed`, `disabled`, `expired` |
| 任务 | `queued`, `running`, `succeeded`, `partially_succeeded`, `failed`, `cancelled` |
| 问答 | `submitted`, `retrieving`, `generating`, `completed`, `no_answer`, `stopped`, `failed`, `blocked` |
| 反馈 | `pending`, `processing`, `resolved`, `closed` |
| 负面反馈类型 | `incorrect`, `incomplete`, `irrelevant_source`, `outdated`, `unclear`, `other` |
| 处理结论 | `answer_correct`, `missing_document`, `document_outdated`, `document_conflict`, `irrelevant_retrieval`, `generation_error`, `insufficient_question`, `other` |

后端返回 `statusLabel` 方便中文 UI，但业务判断只使用枚举值。未知枚举前端显示“未知状态”，不能默认当作成功。

## 11. 鉴权与权限设计

### 11.1 登录会话

- access token：短期 JWT，建议 15 分钟，仅保存在前端内存，请求用 `Authorization: Bearer`。
- refresh token：随机高熵值，只在服务端保存哈希；通过 `HttpOnly + Secure + SameSite=Lax` cookie 传输。
- `remember=true`：refresh session 最长 30 天；否则建议 24 小时。
- refresh token 每次使用都轮换；发现旧 token 重用时撤销整个 token family。
- 退出、禁用、密码重置、角色高风险变更可撤销相关 session。
- 本地密码使用 Argon2id；不记录明文密码或临时密码。
- 连续失败限流和锁定由 Redis + 数据库账号状态共同执行；错误不泄露账号是否存在。
- 生产 SSO 优先接 OIDC；若学校只提供 CAS，则由 CAS adapter 处理，不让本系统接触统一认证密码。

### 11.2 角色和权限代码

角色：`normal_user`、`knowledge_admin`、`user_admin`、`super_admin`。同一用户可有多个角色。

权限按动作拆分，例如：

- `dashboard.read`、`qa.read`、`qa.content.read`、`qa.analytics`。
- `kb.read/create/update/status/delete/rebuild/test`、`kb.permission.read/update`。
- `document.read/create/update/status/delete/download/reprocess`、`chunk.update`。
- `task.read/retry/cancel`。
- `feedback.read/manage`。
- `user.read/create/update/status/import/export/password.reset`、`role.assign`。
- `system.read/update`、`audit.read`。

前端可依据权限隐藏操作，但后端路由和 service 层必须再次校验。

### 11.3 数据范围 ACL

- ACL subject 支持：全校公开、角色、部门、指定用户。
- 知识库动作拆分为 `canSearch/canPreview/canDownload/canManage`。
- 文档默认继承知识库；文档 ACL 只能收窄，不能扩大。
- policy service 先计算用户有效检索范围和 ACL token；PostgreSQL 稀疏查询与 Milvus 向量查询都必须在召回前应用对应过滤条件，禁止先召回再仅在输出阶段过滤。
- 无权资源统一返回 404 或受控 403，不在错误文本中泄露标题、摘要或命中情况。
- 非超级管理员的概览、问答记录、反馈和导出仅统计其管理范围。
- 查看完整用户问答、下载、导出、角色变更、ACL 修改均写审计日志。

## 12. 数据库初步设计

数据库使用 PostgreSQL。所有业务表默认包含 `created_at timestamptz`、`updated_at timestamptz`；需软删除的表增加 `deleted_at timestamptz null`。主键默认 UUID。

### 12.1 用户与权限

| 表 | 关键字段 | 约束/索引 | 用途 |
| --- | --- | --- | --- |
| `departments` | `id,parent_id,name,code,status` | `code` 唯一；`parent_id` FK；索引 parent | 院系/职能部门树 |
| `users` | `id,account,password_hash,name,student_or_staff_no,user_type,subtitle,department_id,email,phone,avatar_url,status,auth_source,external_subject,last_login_at,last_active_at,row_version` | `account` 唯一；学工号可空唯一；department/status/last_login 索引 | 用户主数据 |
| `roles` | `id,code,name,description,is_system` | `code` 唯一 | 四类角色及后续扩展 |
| `permissions` | `id,code,name,module` | `code` 唯一 | 原子权限 |
| `role_permissions` | `role_id,permission_id` | 联合主键和双 FK | 角色-权限多对多 |
| `user_roles` | `user_id,role_id,assigned_by,assigned_at` | 联合主键和双 FK | 用户-角色多对多 |
| `auth_sessions` | `id,user_id,refresh_token_hash,token_family,expires_at,revoked_at,last_used_at,ip,user_agent` | token hash 唯一；user/expiry 索引 | refresh token 轮换与撤销 |
| `login_events` | `id,account,user_id,result,error_code,ip,user_agent,occurred_at` | account/time、user/time 索引 | 登录审计和安全分析 |

### 12.2 知识库、ACL 与文档

| 表 | 关键字段 | 约束/索引 | 用途 |
| --- | --- | --- | --- |
| `knowledge_bases` | `id,code,name,description,department_id,business_category,selectable,status,chunk_size,chunk_overlap,top_k,similarity_threshold,rerank_enabled,hybrid_search_enabled,prompt_instruction,created_by,row_version,deleted_at` | `code` 唯一；同部门名称唯一；status/department/update 索引 | 知识库与检索配置 |
| `knowledge_base_owners` | `knowledge_base_id,user_id` | 联合主键、双 FK | 多负责人 |
| `knowledge_base_acl_entries` | `id,knowledge_base_id,is_public,role_id,department_id,user_id,can_search,can_preview,can_download,can_manage` | CHECK：public/role/department/user 恰好一个；主体唯一索引 | 知识库数据范围 |
| `stored_files` | `id,storage_key,original_name,mime_type,size_bytes,sha256,scan_status,scan_detail,uploaded_by` | `storage_key` 唯一；sha256 索引 | 原始文件与安全扫描 |
| `documents` | `id,knowledge_base_id,display_name,category,publish_department_id,document_no,published_at,effective_at,expires_at,notes,status,enabled,current_version_id,uploaded_by,row_version,deleted_at` | kb/status、document_no、expiry、updated 索引 | 逻辑文档及业务元数据 |
| `document_versions` | `id,document_id,version_no,file_id,is_current,process_status,process_stage,ocr_enabled,chunk_size,chunk_overlap,failure_code,failure_message,processed_at` | `(document_id,version_no)` 唯一；每文档仅一个 current；状态索引 | 文件版本和处理状态 |
| `document_acl_entries` | 与 KB ACL 相同，外键改为 `document_id` | 同样 CHECK；服务层校验只可收窄 | 文档级预览/下载限制 |
| `tags` | `id,name` | name 唯一 | 文档标签字典 |
| `document_tags` | `document_id,tag_id` | 联合主键 | 文档-标签多对多 |
| `document_chunks` | `id,document_version_id,chunk_index,title,section,page_start,page_end,char_start,char_end,content,token_count,search_tokens,vector_id,embedding_model,embedding_dimension,vector_status,content_hash` | `(version,chunk_index)` 唯一；`vector_id` 可空唯一；version/page 索引；search GIN | 切片正文、稀疏检索字段及 Milvus 索引状态 |

PostgreSQL 不保存 embedding。初期将 embedding 模型固定为 1024 维；变更模型需创建新的 Milvus collection 并执行明确的全量重建、校验和 alias 切换任务，不能静默混用不同维度。

### 12.3 Milvus collection 设计

首期使用按 embedding 模型版本划分的 collection，例如 `edurag_chunks_<modelVersion>`，并通过稳定 alias `edurag_chunks_current` 提供查询入口。核心字段：

| 字段 | Milvus 类型/约束 | 用途 |
| --- | --- | --- |
| `chunk_id` | `VARCHAR` 主键 | 对应 PostgreSQL `document_chunks.id`，也是引用回查键 |
| `embedding` | `FLOAT_VECTOR(1024)` | 稠密向量；使用 COSINE 度量并建立 HNSW 索引 |
| `knowledge_base_id` | `VARCHAR` | 知识库范围过滤 |
| `document_id` | `VARCHAR` | 文档范围过滤和失效清理 |
| `document_version_id` | `VARCHAR` | 版本过滤和重建核对 |
| `is_active` | `BOOL` | 仅当前、已发布、启用版本可参与召回 |
| `expires_at` | 时间戳/整数时间 | 过期过滤；无失效日期使用约定空值 |
| `search_acl_tokens` | 可过滤的标量/数组字段 | 展开的全校、角色、部门、用户检索权限 token |
| `embedding_model` | `VARCHAR` | 索引校验、审计和重建诊断 |

Milvus 不保存正文、文件名或完整 ACL 规则。检索返回 `chunk_id` 和分数后，服务从 PostgreSQL 批量读取受权正文与引用元数据，并再次验证当前状态；Milvus 数据丢失时通过处理任务全量重建。

### 12.4 任务与处理事件

| 表 | 关键字段 | 约束/索引 | 用途 |
| --- | --- | --- | --- |
| `processing_tasks` | `id,task_no,type,knowledge_base_id,document_id,document_version_id,status,stage,progress_percent,progress_text,error_code,error_message,created_by,idempotency_key,started_at,completed_at,cancel_requested_at` | `task_no` 唯一；`(created_by,idempotency_key)` 唯一；status/type/time 索引 | 入库、重建、导入、导出任务 |
| `task_items` | `id,task_id,object_type,object_id,status,error_code,error_message` | task/status 索引 | 批量任务逐项结果 |
| `task_events` | `id,task_id,sequence,event_type,stage,payload,occurred_at` | `(task_id,sequence)` 唯一 | 恢复进度、SSE 续传、时间线 |
| `vector_index_outbox` | `id,operation,chunk_id,document_version_id,collection_name,payload,status,retry_count,next_retry_at,last_error` | operation/chunk/status 索引；事件幂等键唯一 | PostgreSQL 与 Milvus 间的可靠、可重试索引同步 |

`progress_percent` 可空；解析器不能可靠估算时只写 `stage/progress_text`，符合 PRD“不得伪造百分比”。

### 12.5 会话、消息、引用与反馈

| 表 | 关键字段 | 约束/索引 | 用途 |
| --- | --- | --- | --- |
| `conversations` | `id,user_id,title,status,is_favorite,message_count,last_message_at,deleted_at` | user/deleted/updated 索引 | 本人历史会话 |
| `conversation_knowledge_bases` | `conversation_id,knowledge_base_id` | 联合主键 | 会话检索范围多对多 |
| `messages` | `id,conversation_id,parent_message_id,role,content,status,generation_version,error_code,error_message,retrieval_ms,first_token_ms,total_ms,model_name,idempotency_key,created_at,completed_at` | conversation/time；`(conversation,idempotency_key)` 唯一；status 索引 | 用户/助手消息和生成状态 |
| `message_events` | `id,message_id,sequence,event_type,payload,occurred_at` | `(message_id,sequence)` 唯一 | SSE 断线续传和刷新恢复 |
| `message_citations` | `id,message_id,citation_index,document_id,document_version_id,chunk_id,quote,context_before,context_after,page_start,page_end,section,similarity_score,rerank_score` | `(message_id,citation_index)` 唯一；document/message 索引 | 引用与原文定位 |
| `message_ratings` | `id,message_id,user_id,value,reason,comment` | `(message_id,user_id)` 唯一 | 点赞/点踩可修改 |
| `feedbacks` | `id,message_id,citation_id,conversation_id,knowledge_base_id,created_by,type,comment,status,assignee_id,resolution,resolution_note,notify_user,resolved_at` | status/kb/assignee/time 索引 | 用户反馈和来源过期报告 |
| `feedback_events` | `id,feedback_id,actor_id,from_status,to_status,note,related_document_id,occurred_at` | feedback/time 索引 | 处理轨迹 |

### 12.6 运营、通知与审计

| 表 | 关键字段 | 约束/索引 | 用途 |
| --- | --- | --- | --- |
| `recommended_questions` | `id,category,title,question,knowledge_base_id,sort_order,enabled,valid_from,valid_to` | enabled/order 索引 | 首页运营推荐/热门问题兜底 |
| `notifications` | `id,user_id,type,title,body,target_type,target_id,batch_key,read_at,created_at` | user/read/time；batch_key 索引 | 站内通知和合并任务结果 |
| `system_settings` | `key,value_json,is_secret,updated_by,row_version` | key 主键 | 上传、问答、公告等设置；密钥只存引用/密文 |
| `audit_logs` | `id,actor_id,module,action,target_type,target_id,result,ip,user_agent,request_id,before_json,after_json,created_at` | actor/time、target、module/action/time 索引 | 高风险、受限查看和导出审计；只读 |

管理概览首期直接从业务表聚合；数据量达到瓶颈后再增加日汇总表或物化视图，当前不提前创建无使用场景的数据仓库结构。

## 13. 数据模型关系

- Department 1:N User；Department 1:N KnowledgeBase。
- User M:N Role，通过 `user_roles`；Role M:N Permission，通过 `role_permissions`。
- KnowledgeBase M:N owner User；KnowledgeBase 1:N Document。
- KnowledgeBase/Document 各自 1:N ACL entry；ACL entry 可指向一个 Role、Department、User 或 public。
- Document 1:N DocumentVersion；Document 当前指向一个 current version。
- DocumentVersion 1:N DocumentChunk；StoredFile 1:N DocumentVersion（同哈希文件可复用存储对象，但业务版本独立）。
- DocumentChunk 通过 `chunk_id/vector_id` 与 Milvus 中一条向量记录对应；该关系不是数据库外键，使用 outbox、幂等写入和周期核对保持一致。
- KnowledgeBase M:N Conversation；User 1:N Conversation；Conversation 1:N Message。
- Assistant Message 1:N Citation；Citation N:1 DocumentVersion/Chunk。
- Message 1:0..1 当前用户 Rating；Message/Citation 1:N Feedback。
- ProcessingTask 1:N TaskItem、1:N TaskEvent，并可关联 KnowledgeBase、Document 或 Version。
- Feedback 1:N FeedbackEvent，且可在处理事件中关联修复后的 Document。
- User 1:N Notification、AuthSession、AuditLog（作为 actor）。

## 14. 关键业务逻辑

### 14.1 问答

1. 校验用户状态、问题长度、幂等键和会话所有权。
2. 计算用户当前可搜索且启用的知识库/文档集合；前端传入范围只能是该集合的子集。
3. 持久化用户消息和状态为 `submitted`，创建助手消息。
4. policy service 计算有效知识库、文档范围和 ACL token；Milvus 查询在召回前使用标量表达式过滤权限、启用状态、当前版本和失效时间。
5. Milvus 执行稠密召回，PostgreSQL 执行稀疏召回；按稳定算法融合并可选重排，低于阈值则进入 `no_answer`。
6. 仅把受权切片传给模型，提示词要求引用编号与来源一一对应。
7. 通过 SSE 写入阶段和增量，同时把事件持久化，最终原子写入完整回答和引用。
8. 停止时保留已生成内容，状态为 `stopped`；重新生成创建新版本消息，不在完成前删除旧答案。

### 14.2 文档入库

1. 前后端都校验数量/大小/扩展名；后端进一步校验 MIME、魔数、空/损坏/加密文件和安全扫描。
2. 计算 SHA-256，返回重复信息；无权查看重复对象时不返回其详情。
3. 文件上传完成只代表暂存成功；提交入库后创建 Document/Version/Task。
4. worker 依次解析、按需 OCR、切片、Embedding，并通过 outbox 幂等写入 Milvus；每阶段持久化事件。
5. 只有当前版本且状态 `ready`、文档启用、知识库启用、未过期的切片可检索。
6. 任一阶段失败记录稳定错误码和管理员可理解的建议，可从安全阶段重试。
7. 新版本成功前旧版本按 `switchPolicy` 保持可用；发布事务同时写入 current 切换和 outbox 事件，worker 更新 Milvus 的 `is_active` 状态，查询结果仍回 PostgreSQL 校验，避免同步窗口内返回旧版或越权内容。

### 14.3 权限与越权防护

- 列表查询、详情查询、下载、检索、统计和导出使用同一个 policy service，禁止各模块自行拼权限。
- 会话路径中的 `conversationId` 必须校验所有者，不能只因已登录就访问。
- 管理员的角色权限与资源管理范围都要满足；有 `kb.update` 但不在该库范围仍返回 404/403。
- 文档 ACL 更新时验证其允许集合是知识库允许集合的子集。
- 批量操作逐项鉴权并返回成功/失败明细，不能因一个无权对象导致越权处理其他对象。

## 15. 后端项目结构建议

实施阶段建议新增 `backend/`，不改动 `frontend/` 目录结构：

```text
backend/
  app/
    main.py
    api/
      router.py
      deps.py
      v1/
    core/
      config.py
      security.py
      errors.py
      logging.py
    db/
      session.py
      base.py
      migrations/
    modules/
      auth/
      users/
      knowledge_bases/
      documents/
      tasks/
      conversations/
      rag/
      feedback/
      analytics/
      notifications/
      settings/
      audit/
    integrations/
      storage/
      sso/
      llm/
      embedding/
      milvus/
      ocr/
    workers/
      celery_app.py
      document_pipeline.py
    tests/
      api/
      services/
      integration/
  alembic.ini
  pyproject.toml
  Dockerfile
  .env.example
  README.md
docker-compose.yml  # PostgreSQL、Redis、Milvus Standalone 及其依赖
```

每个 `modules/*` 内按规模使用 `router.py / schemas.py / service.py / repository.py / models.py / policies.py`。不额外设置只做转发的 controller 层，避免过度分层。

## 16. 前后端联调前必须修正的契约点

本阶段不修改前端；实施完成后再做以下最小改动：

1. `/chat` 需要携带 `conversationId`，建议 `/chat/:conversationId`。
2. `/source` 需要 `messageId/citationId`，建议 `/source/:citationId`。
3. `/admin/document` 需要 `documentId`，建议 `/admin/documents/:documentId`。
4. 首页提交必须先创建会话/首问，不能只丢失问题后跳路由。
5. 当前用户姓名在 AppShell、首页、对话分别是“李明/王同学/李明”，统一从 `/auth/me` 获取。
6. 路由增加 `requiresAuth` 和 permission meta；404 不再重定向首页。
7. `tone` 不作为后端字段；前端按稳定状态枚举映射样式。
8. 所有静态 `model-value` 筛选改为 `v-model` 并作为 query 发送。
9. 历史分页、用户分页、反馈分页改用服务端总数。
10. 上传改成真实 per-file request，并保留 `clientFileId` 关联 UI 队列。
11. 任务中心导航不能继续指向上传页；文档中心不能直接指向固定文档详情。
12. Markdown 回答渲染必须使用安全过滤器；不得直接 `v-html` 注入模型输出。

## 17. 建议默认决策

为避免实施阶段因 PRD 待确认项停滞，建议先采用：

- 开发环境开启本地登录，生产可配置 CAS/OIDC；两个入口可并存。
- 上传格式 PDF/DOCX/TXT/MD；单文件 50 MB；单次最多 20 个，与当前 UI 一致。
- 文档版本管理开启；新版本成功后事务切换，旧版本保留但不参与召回。
- 失效日期到达后自动排除检索并标记 `expired`。
- ACL 支持全校、角色、部门、用户；文档只可收窄。
- 稠密向量检索固定使用 Milvus；PostgreSQL 只负责业务数据和稀疏检索，不启用 pgvector，也不提供 Qdrant 回退。
- 模型不得用无引用的通用知识补充校方结论；无可靠依据返回 `no_answer`。
- 预览和下载权限分离；默认管理员可预览其范围内文档，下载由 ACL 控制。
- 会话、文档、知识库使用软删除；审计日志不可由普通管理 API 删除。
- 问答和任务实时状态使用 SSE，同时保留 GET 详情供刷新恢复与轮询兜底。
- 站内通知首期启用；邮件/短信/企业 IM 暂不实现，只保留 provider 接口。

## 18. 实施顺序（评审通过后）

1. 建立 `backend/`、配置、统一错误/响应、PostgreSQL、Milvus Standalone、迁移和测试基座。
2. 实现用户、认证、session、RBAC、ACL policy 和种子管理员。
3. 实现知识库、文件暂存、文档/版本、任务状态与本地解析流水线。
4. 实现 Milvus 稠密检索、PostgreSQL 稀疏检索、融合重排、权限过滤、RAG provider、会话/消息和 SSE。
5. 实现引用预览/下载、历史会话、评价/反馈和通知。
6. 实现用户导入、管理问答、概览、审计和设置。
7. 最小修改前端请求层、路由参数、状态映射和 SSE；逐接口核对。
8. 补正常、缺参、错误参数、未登录、权限不足、不存在、重复、幂等、数据库/外部服务异常测试。
9. 输出根目录/后端 README、`.env.example`、迁移/启动命令和联调说明。

## 19. 原规划阶段未做项（后端部分现已完成）

- 已创建 `backend/` 服务端源码并注册完整 `/api/v1` 路由。
- 已创建认证/RBAC与核心业务表的数据库迁移。
- 未修改任何前端页面、路由、样式或 mock 数据。
- 已固化依赖和 Docker Compose；真实外部模型调用需部署方填写 Qwen/DeepSeek API Key 后启用。
- 未对 PRD 候选功能（语音、临时附件、多语言、知识图谱、多租户）设计实现接口。

