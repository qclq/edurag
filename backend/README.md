# EduRAG Backend

`docs/BACKEND_PLAN.md` 的完整模块化单体实现。API 前缀为 `/api/v1`，JSON 字段统一使用 `camelCase`，业务数据使用 PostgreSQL，向量索引使用 Milvus，异步流水线使用 Redis + Celery。

## 已实现

- 系统启动、统一响应/错误、分页、健康检查和请求 ID。
- 本地认证、验证码、OIDC/CAS、refresh token 轮换、RBAC 和个人资料。
- 知识库 CRUD、ACL、检索配置、检索测试和索引重建任务。
- 安全暂存上传、文档/版本/切片、解析流水线、Qwen Embedding、Milvus outbox 与预览下载。
- 支持 PDF、DOC/DOCX/DOCM/RTF、XLS/XLSX/XLSM/XLSB/CSV、PPT/PPTX/PPTM、ODT/ODS/ODP、Markdown 和 TXT；旧版与开放式 Office 文件先由 LibreOffice 无界面转换，Excel 按工作表与行区间预切分，PDF/演示文稿/Word 图片可先经 Qwen OCR 再切分向量化。
- 会话、消息幂等、DeepSeek RAG、持久化 SSE、停止/重生成、引用和评价。
- 任务中心、反馈处理、通知、首页/全局搜索、管理概览、问答审计、系统设置和审计日志。
- 用户/角色/部门管理、CSV/XLSX 导入校验与导入、导出任务。

- `GET /system/bootstrap`：系统名称、登录方式、验证码策略、问答/上传限制、功能开关和服务状态。
- `GET /system/health/live`、`GET /system/health/ready`：存活与数据库就绪检查。
- `GET /auth/captcha`：带过期时间和最大尝试次数的一次性 SVG 验证码。
- `POST /auth/login`：本地账号登录、Argon2id 密码校验、失败限流/锁定、登录审计。
- `GET /auth/sso/authorize`、`GET /auth/sso/callback`：可配置 OIDC + PKCE 或 CAS，带持久化防重放 state；默认关闭。
- `POST /auth/refresh`：HttpOnly refresh cookie 轮换；检测旧 token 重放后撤销整个 token family。
- `POST /auth/logout`：撤销当前会话并清理 cookie。
- `GET /auth/me`：当前用户、部门、角色、权限、管理入口能力和最近登录信息。
- `PATCH /me`：更新可编辑邮箱、手机号和头像；统一认证同步字段只读。
- `POST /me/password`：验证原密码并修改本地密码，随后撤销该用户全部会话。

错误响应、字段校验响应和成功响应都包含 `requestId`。手机号和邮箱在读取响应中默认脱敏。

## 本地启动

要求 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和 Docker。

```powershell
Copy-Item .env.example .env
docker compose -f ../docker-compose.yml up -d postgres redis
uv sync --group dev
uv run alembic upgrade head
uv run python -m app.scripts.seed_admin --account admin --name "系统管理员"
uv run fastapi dev app/main.py
```

种子命令会交互式读取初始密码，不通过命令行参数或日志暴露明文。开发文档位于 `http://localhost:8000/docs`。

运行测试：

```powershell
uv run pytest
```

本机 Python 3.12/3.13 如需直接连接 Milvus，可安装可选 extra：

```powershell
uv sync --group dev --extra milvus
```

Python 3.14 环境建议不安装该 extra 并通过 Docker 运行 Milvus；Docker 镜像使用 Python 3.12，默认包含 extra。

如果希望全部组件都在 Docker 中运行，请在项目根目录执行：

```powershell
docker compose up -d --build
```

统一入口为 `http://localhost:8080`，API 文档为 `http://localhost:8080/docs`。

## 关键配置

复制 `.env.example` 后至少修改 `SECRET_KEY`。生产环境会拒绝默认密钥和非 Secure cookie。

- OIDC：设置 `SSO_PROVIDER=oidc`、client ID/secret、authorize/token/userinfo URL。
- CAS：设置 `SSO_PROVIDER=cas` 和 `SSO_CAS_BASE_URL`。
- `SSO_AUTO_PROVISION=false` 为安全默认值；关闭时须先由管理员创建账号。
- SSO 回调建立 refresh 会话后重定向前端；前端再调用 `/auth/refresh` 获取仅存于内存的 access token。
- 生产部署需要在身份提供方登记实际回调地址 `/api/v1/auth/sso/callback`。

### Qwen Embedding 与 DeepSeek

两套模型使用独立的 Base URL、API Key 和模型配置：

```dotenv
QWEN_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_API_KEY=你的百炼密钥
EMBEDDING_MODEL=text-embedding-v4
QWEN_OCR_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_OCR_MODEL=qwen-vl-ocr
EMBEDDING_DIMENSION=1024

DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_API_KEY=你的DeepSeek密钥
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_THINKING=disabled
```

百炼工作空间专属部署应把 `QWEN_EMBEDDING_BASE_URL` 替换为工作空间的 `/compatible-mode/v1` 地址。`text-embedding-v4` 按 10 条/批调用，并严格校验 1024 维；改变模型或维度时必须新建 Milvus collection，全量构建/校验后再切换 alias。

DeepSeek 使用 Chat Completions 流式响应。回答提示词要求只依据召回的校园资料、逐项标注引用，依据不足时返回 `no_answer`。当前官方推荐模型可通过 `DEEPSEEK_MODEL` 调整；密钥不会写入数据库、普通设置或日志。

## 目录

```text
app/
  api/                 # API 汇总与认证依赖
  core/                # 配置、安全、统一错误
  db/                  # SQLAlchemy 元数据与会话
  integrations/sso/    # OIDC/CAS 适配
  integrations/        # storage、Qwen embedding、DeepSeek、Milvus
  modules/knowledge_bases/
  modules/documents/
  modules/tasks/
  modules/conversations/
  modules/rag/
  modules/feedback/
  modules/operations/
  workers/              # Celery 文档流水线、回答生成、索引重建
  modules/auth/         # 验证码、登录、会话与 SSO 路由
  modules/system/       # bootstrap 与健康检查
  modules/users/        # 用户、RBAC、个人资料与密码
  scripts/              # 初始管理员种子命令
alembic/                # PostgreSQL 迁移
tests/                  # 隔离数据库 API 测试
```

完整接口清单以运行时 OpenAPI `/docs` 为准。测试环境使用隔离 SQLite、local embedding 和 extractive answer，避免把外部服务可用性混入单元/API 测试；生产默认使用 Qwen + DeepSeek + Milvus。
