# EduRAG

## Docker 一键启动

在项目根目录执行：

```powershell
docker compose up -d --build
```

启动完成后访问：

- 系统前端：<http://localhost:8080>
- 后端 API 文档：<http://localhost:8080/docs>
- 后端健康检查：<http://localhost:8080/api/v1/system/health/ready>

前端 Nginx 会把 `/api` 同源代理到后端。Compose 会启动 PostgreSQL、Redis、Milvus 2.6.22（独立 etcd/MinIO）、FastAPI 和 Celery worker；这些后端服务仅在 Docker 网络中开放，不占用宿主机的数据库或 API 端口。

查看状态和日志：

```powershell
docker compose ps
docker compose logs -f backend frontend
```

停止服务：

```powershell
docker compose down
```

如需修改默认端口或密码：

```powershell
Copy-Item .env.docker.example .env
```

随后修改 `.env`。至少填写 `QWEN_API_KEY` 和 `DEEPSEEK_API_KEY`；生产环境还必须替换 `SECRET_KEY`、数据库密码，并通过 HTTPS 启用 `COOKIE_SECURE=true`。模型 Key 分别只提供给后端和 worker，不会进入前端构建。

默认模型链路：

- Qwen `text-embedding-v4`（1024 维）负责文档和查询向量化；
- Qwen `qwen-vl-ocr-latest` 识别 PDF、PPTX、DOCX 中的图片文字，之后再进入文本切分和向量化；
- LibreOffice 无界面转换兼容 DOC、XLS、PPT、RTF、XLSB 与 OpenDocument 格式，原始上传文件仍作为下载原件保留；
- Milvus alias `edurag_chunks_current` 保存/查询派生向量；
- DeepSeek `deepseek-flash` 负责基于受权切片生成带引用回答；
- PostgreSQL 始终保存正文、权限、任务事件和回答事实数据。

## 创建初始管理员

容器运行后执行：

```powershell
docker compose exec backend python -m app.scripts.seed_admin --account admin --name "系统管理员"
```

命令会交互式读取初始密码。
