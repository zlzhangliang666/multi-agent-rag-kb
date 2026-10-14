# HANDOFF — 继续本项目的交接说明

> **用途**：切换到新工作区 / 新对话后，先读本文件即可无缝继续。
> **提示**：这是临时工作文件，答辩 / 交付前可删除。
> 生成时间：2026-10-14

## 0. 新对话怎么用
在新对话第一条消息里直接说：

```
读 E:\thesis\HANDOFF.md，继续之前的工作。
```

前置条件：新会话的**工作区请指向 `E:\thesis`**（旧工作区 `e:\毕业设计` 不存在，
曾导致内置 Edit/Write 工具报 `PathScopeExceed`；若再次出现，改用 Shell 写入即可）。

---

## 1. 项目是什么
- 路径：`E:\thesis`
- 定位：**通用「知识库问答系统」**（RAG + 多 Agent，本地化部署）。
  用户上传 PDF / Word / TXT / Markdown → 解析 → 自适应分块 → 向量化 → 建索引 → 检索问答。
- **408 考研资料只是内置演示语料**，换成其他语料只需改配置（见第 5 节）。
- 技术栈：
  - 前端：Vue3 + Vite + Element Plus（对话 / 知识库 / Agent 链路 / 系统状态 / 数据大屏 / 模型设置 / 登录）
  - 后端：FastAPI + SQLAlchemy + SQLite
  - 检索：Chroma 向量库 + BM25 关键词，RRF 混合召回
  - 向量化：bge-small-zh-v1.5 ONNX（本地）
  - 生成：本地 Qwen2.5-1.5B-Instruct GGUF（子进程 + 空闲 180s 卸载）；可选 OpenAI 兼容远程模型（前端「模型设置」页配 Key，未配则默认本地）
  - 编排：LangGraph 五 Agent（调度 / 审核 / 检索 / 生成 / 绘图）
  - 输出：SSE 流式，答案带引用来源

## 2. 环境与运行
- 后端：`cd E:\thesis` 后 `.\.venv\Scripts\python.exe backend\main.py`，端口 **8000**（当前运行中）。
- 前端：`cd E:\thesis\frontend` 后 `npm run dev`，端口 **5173**。
- 一键启动：双击 `E:\thesis\start.bat`（自动起前后端并打开浏览器）。
- 运行时数据**全部在项目内** `data/`：
  `chroma_db/`、`bm25_index.pkl`、`kb_qa.db`、`uploads/`、`static_images/`、`sensitive_words.txt`。
- 健康检查：`GET http://127.0.0.1:8000/api/health`。

## 3. 已完成（按时间倒序）
1. **前端可配置远程模型（最新一轮，2026-10-14）** —— 详见 `STATUS.md` 第二十节：
   - 新增 `backend/llm_core/providers/`（local / openai 兼容 provider + `LLMService` + 配置存储与脱敏）；
   - `LLMService` 与 `ModelManager` 同签名，`main.py` 与 `/api/chat/stream` 零改动替换；
   - 新增接口 `GET/PUT /api/llm/config`、`POST /api/llm/test`；`/api/model/status` 增加 `active_provider`/`remote`；
   - 未配置 Key → 默认本地；切远程 → 自动卸载本地权重；远程失败默认报错，可选自动回退本地；
   - 新增「模型设置」页 `frontend/src/views/ModelSettingsView.vue`（第 7 页）+ API 封装 + 路由/菜单；
   - 新增 `test/test_provider.py`（18 项），pytest 合计 **43 项通过**。
2. **阶段 6 可选加分项 + 评测补齐（2026-10-14）** —— 详见 `STATUS.md` 第十九节：
   - 评测集通用化并扩到 **25 条**（`evaluation/queries.json` 新增 `answer_keywords` 字段）；
   - 新增答案质量评测 `scripts/evaluate_answer_quality.py`（引用正确率 / 覆盖率 / 忠实度 / 答案覆盖度，走真实 SSE 链路）；
   - 新增性能测试 `scripts/benchmark.py`（检索延迟 / 模型加载·卸载内存 / 端到端 TTFT·吞吐）；
   - 新增 pytest 单元测试 **25 项**（`test/` + `pytest.ini`）；
   - 新增数据大屏页 `frontend/src/views/DataDashboardView.vue` + 接口 `GET /api/stats/overview`；
   - 新增可选容器化部署（`Dockerfile` / `Dockerfile.frontend` / `docker-compose.yml` / `frontend/nginx.conf`）。
3. **通用化改造 + 健壮性修复** —— 详见 `STATUS.md` 第十八节：
   - `rebuild_all.py` 课件清单改为从 `config.yaml` 的 `rebuild.courseware` 读取（去硬编码）；
   - 提示词与服务名去 408 绑定（worker / llm_gen / main）；
   - 上传：强制 50MB 上限（413）、落库失败回滚检索侧并返回 500、空文本提示需 OCR；
   - 评测集外置到 `evaluation/queries.json`，脚本支持 `--queries-file`。
4. **前端删除历史对话**：`DELETE /api/chat/conversations/{id}`（级联删消息 / 日志 / 绘图记录）+ 会话列表删除按钮（二次确认）。
5. **doc_id 统一 + 原地重建**：`backend/rag/doc_id.py` 为唯一权威实现；重建后 140 文档 / 4183 chunks。
6. **运行时数据根统一 + 可复现**：数据统一到项目内 `data/`，`scripts/rebuild_all.py` 幂等重建，`start.bat` 一键启动。
7. **新会话首问 HTTP 422 修复**：`conversation_id: Optional[str]`（Pydantic v2 显式 null 校验问题），覆盖 chat 与 agent_log 两个接口。

## 4. 改动关键文件
- 配置：`backend/config/config.yaml`（含 `datasource` 与 `rebuild.courseware`）、`backend/config/__init__.py`
- 重建 / 导入：`scripts/rebuild_all.py`、`scripts/init_knowledge_base.py`、`scripts/import_ppt.py`、`scripts/sync_kb_to_db.py`
- 评测 / 测试：`scripts/evaluate_retrieval.py`、`scripts/evaluate_answer_quality.py`、`scripts/benchmark.py`、`evaluation/queries.json`、`test/`（含 `test_provider.py`）+ `pytest.ini`
- 后端接口：`backend/api/kb/routes.py`、`backend/api/chat/routes.py`、`backend/api/model/routes.py`、`backend/api/agent_log/routes.py`、`backend/api/stats/routes.py`（新增 `/overview`）、`backend/api/llm/routes.py`（新增）
- LLM Provider（本地 / OpenAI 兼容远程）：`backend/llm_core/providers/`（`service.py` / `local_provider.py` / `openai_provider.py` / `settings_store.py`）、`backend/database/models/__init__.py`（新增 `LLMSettings` 表）
- 提示词 / 服务名：`backend/llm_core/model_worker/worker.py`、`backend/agent/llm_gen_agent/agent.py`、`backend/main.py`
- doc_id：`backend/rag/doc_id.py`
- 前端：`frontend/src/views/ChatView.vue`、`frontend/src/views/DataDashboardView.vue`（新）、`frontend/src/views/ModelSettingsView.vue`（新）、`frontend/src/api/index.js`、`frontend/src/router/index.js`、`frontend/src/App.vue`
- 容器化（可选）：`Dockerfile`、`Dockerfile.frontend`、`docker-compose.yml`、`frontend/nginx.conf`
- 文档：`README.md`、`STATUS.md`、`HANDOFF.md`

## 5. 关键约定（改动务必遵守）
- **doc_id 唯一权威实现**：`backend/rag/doc_id.py`。规则：
  - 笔记 `kb_<相对数据源根的路径>`；课件 `kb_<学科>_ppt_<章节>_<文件名>`；
  - 路径分隔符与空白字符统一转下划线 `_`，去扩展名。
- **幂等导入**：同一 doc_id 先删后加，重复执行不产生重复文档。
- **三端一致性**：Chroma / BM25 / SQLite 无跨存储事务，必须靠脚本顺序同步
  （`rebuild_all.py` → `sync_kb_to_db.py`）；上传侧失败会回滚检索侧。
- **换语料**：改 `config.yaml` 的 `datasource.path`；课件补齐改 `rebuild.courseware`；
  评测集改 `evaluation/queries.json`；纯 `.md` 语料无需登记课件清单。

## 6. 尚未解决 / 已知局限
- **无 OCR**：扫描件 / 图片型 PDF 会被跳过（现会显式提示，但无 OCR 能力）。
- **无 reranker**：RRF 融合后直接取 top-k。
- **答案质量指标为启发式**：忠实度（句级词面证据覆盖，阈值 0.6）与引用正确率（引用来源关键词命中，
  已纳入引用正文并保留旧口径对比）均为轻量代理指标，适合版本回归对比，非严格语义蕴含（NLI）评判；
  尚无 LLM-as-judge 评测。
- **引用归因为词面启发式**（`backend/agent/citation.py`）：句级词面覆盖判断来源是否被回答使用，
  会漏判「模型改写/概括」；阈值偏宽松 + 兜底保留最少条数，不是严格引用判定。
- **内容级去重缺失**：同一内容换文件名上传仍会重复。
- **三端无跨存储事务**：仅靠顺序写 + 上传侧回滚缓解，重建中途失败仍可能留脏数据（有 `.bak` 兜底）。
- **并发能力弱**：SQLite 单文件 + 模型单进程 + 请求锁，不适合高并发多用户；无速率限制。
- **绘图已改「模型自绘」为主（方案 B）**：模型在回答里输出 ` ```mermaid ` 块，后端 `extract_mermaid_blocks`
  抽为 `image_code`、`image_node` 短路直接采用；`sanitize_mermaid_code` 清洗常见语法笔误
  （`->` / `-- >` / `A --> B: 文字` 等）；`image_agent` 的模板（现仅作兜底）仍为旧语料主题，换语料时可自行调整。
- **远程模型仅支持 OpenAI 兼容单协议**：未做多厂商差异化参数（`top_p` / `presence_penalty`）；
  远程模式未做端到端答案质量评测（复现需自备真实 API Key）。
- **`llm_settings` 全局单行**：不区分用户，切换 provider 立即对全站生效。
- 历史聊天消息里内嵌的旧 `chunk_id` 在重建后失效（正文与文档名仍正常显示）。

## 7. 验证命令
```bash
cd E:\thesis
# 重建预览（不写库）
.\.venv\Scripts\python.exe scripts\rebuild_all.py
# 检索评测（外部评测集，25 条）
.\.venv\Scripts\python.exe scripts\evaluate_retrieval.py
# 答案质量评测（需后端运行）
.\.venv\Scripts\python.exe scripts\evaluate_answer_quality.py
# 性能测试（需后端运行）
.\.venv\Scripts\python.exe scripts\benchmark.py
# 单元测试（71 项）
.\.venv\Scripts\python.exe -m pytest
# 健康检查 / 数据大屏接口 / LLM 配置接口
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/stats/overview
curl http://127.0.0.1:8000/api/llm/config
```

## 8. 当前数据快照
- 文档：**140**（19 笔记 + 121 课件）；chunks：**4183**（Chroma = BM25 = SQLite）
- 检索：Hit@4 **100%（25/25）**，MRR **0.9300**；平均延迟 ~139ms（p50 138 / p95 149）
- 答案质量：引用正确率 **90.3%**（旧口径 71.3%）、引用覆盖率 **100.0%**、忠实度 **74.0%**、答案覆盖度 **72.0%**（25 条）
- 性能：模型加载 2.34s / 卸载 0.28s / 内存 1368MB → 65MB（降 1304MB）；生成 TTFT ~0.30s、吞吐 ~28 字/s
- 单元测试：pytest **71 项通过**
- 聊天历史：conversations 14 / messages 78；agent_logs 225；images 3
- LLM 配置：`llm_settings` 单行，默认 `provider=local`、无 API Key（远程需在「模型设置」页自行配置）