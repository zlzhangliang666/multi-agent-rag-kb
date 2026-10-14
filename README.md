# 多 Agent 协同 RAG 知识库问答系统

通用私有知识库问答系统（离线运行、多 Agent 协同、检索溯源、流式问答、内容审核、Agent 链路可视化）。
内置 **408 考研资料** 作为演示语料，换成其他语料只需改配置（见文末「换用其他语料」）。

## 当前状态

阶段 0 ~ 5 全部完成并验证；阶段 6 可选加分项（单元测试 / 性能测试报告 / 可选 Docker / 数据大屏）已实施。
详见 [STATUS.md](./STATUS.md)。

| 能力 | 状态 |
|---|---|
| 知识库规模 | 140 文档 / 4183 chunks（408 演示语料） |
| 模型独立子进程 + 空闲 180s 自动卸载 | 已验证（1787MB → 67.3MB，实测 idle=180.4s 触发卸载） |
| RAG 检索（BM25 + 向量 + RRF） | 已验证 Hit@4 = 100.0%（25/25），MRR = 0.9300 |
| 答案质量评测（引用正确率 58.0% / 忠实度 70.7%） | 已实施 25 条，见 `evaluation/answer_quality_report.md` |
| LangGraph 多 Agent 链路 | 已验证 7 节点，含审核拦截与绘图分支 |
| 前端可配置远程模型（OpenAI 兼容，Key 脱敏存储） | 已验证：不配置则默认本地；切换远程自动卸载本地权重 |
| SQLite 落库（9 张表） | 已验证 |
| Vue3 前端 7 个页面（含数据大屏 / 模型设置） | 构建通过，dev server 联调通过（浏览器像素级未验证） |
| 绘图（Mermaid） | 模型自绘 + 退化图自检重试 + 关键词模板兜底；支持全屏查看、历史会话图片回显 |
| 单元测试（pytest） | 62 项通过，见 `test/` |
| 性能测试（检索 avg 138.5ms / 生成吞吐 ~28 字/s） | 已实施，见 `evaluation/performance_report.md` |

## 环境要求

- Windows 10/11，内存 ≥ 8GB，CPU 支持 AVX2
- Python 3.10 / 3.11
- Node.js ≥ 18
- 磁盘 ≥ 5GB

## 前置资源（不入库，需自行补齐）

为控制仓库体积、并规避版权与密钥风险，以下两个目录已在 `.gitignore` 中排除，**克隆后需自行补齐**：

| 目录 | 内容 | 补齐方式 |
|---|---|---|
| `models/` | Qwen2.5-1.5B GGUF + bge-small-zh ONNX（约 1.1 GB） | 见下方「模型下载」 |
| `408-master/` | 演示语料：408 考研资料（约 1.1 GB） | 自行获取，或替换为自有语料（见文末「换用其他语料」） |

> 缺 `models/` 时无法本地推理，但仍可在「模型设置」页配置 OpenAI 兼容远程模型；
> 缺 `408-master/` 时后端可正常启动，只是知识库为空。
> 运行期数据（Chroma / BM25 / SQLite）在 `data/` 下，同样不入库，用 `scripts\rebuild_all.py --yes` 重建。

## 快速启动

> **一键启动（推荐）**：双击项目根目录的 `start.bat`。它会自动检查虚拟环境 / Node.js / 模型 / 知识库数据，
> 分别在新窗口启动后端与前端，并打开浏览器。停止服务时关闭弹出的两个窗口即可。
> （该 bat 为 GBK 编码，双击在 cmd 中中文正常显示；若用编辑器修改请保持 GBK 或另存为 ANSI。）

### 1. 后端（手动）

```powershell
cd <项目目录>
python -m venv .venv                    # 首次：创建虚拟环境
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt         # 首次：安装依赖
python backend\main.py
```

- 服务：http://localhost:8000
- Swagger：http://localhost:8000/docs

首次启动会自动：建表 → 启动模型 Worker 子进程 → 加载 ONNX Embedding → 打开 Chroma 与 BM25 索引。
模型本体懒加载，首次提问时才载入内存。

### 2. 前端

```powershell
cd <项目目录>\frontend
npm install
npm run dev
```

访问 http://localhost:5173

### 3. 重建知识库（可选）

```powershell
# 【推荐】一键全量重建：从 408-master 重生成 Chroma + BM25 + SQLite（约 3~5 分钟）
# 换机器 / 清理临时目录 / data/ 缺失时用这一条即可。加 --yes 才真正执行。
python scripts\rebuild_all.py            # 预览将要重建的内容
python scripts\rebuild_all.py --yes      # 执行（旧派生数据会改名为 .bak_<时间戳>）

# 以下为单步脚本（按需使用）
python scripts\init_knowledge_base.py     # 仅从 408-master 全量导入笔记（解析 → 分块 → 向量化 → 建索引）
python scripts\import_ppt.py --dir <课件目录> --subject <学科> --chapter <章节>   # 从课件 PDF 补齐某章节
python scripts\rebuild_vector_store.py    # 仅重建 Chroma（复用 BM25 与 embedding 缓存，约 3 秒）
python scripts\rebuild_bm25_from_chroma.py# 以 Chroma 为权威源重建 BM25（修复索引不一致，幂等）
python scripts\sync_kb_to_db.py           # 把知识库同步到 SQLite 的 documents / chunks 表
python scripts\evaluate_retrieval.py      # 检索质量评测（25 条查询，输出 Hit@4 / MRR / 分学科命中率）
```

> 重建后**必须重启后端**，否则内存里仍是旧索引。

## 评测与测试

全部评测脚本、评测集与报告位于 `evaluation/`，单元测试位于 `test/`。

### 检索质量评测（25 条通用查询）

```powershell
python scripts\evaluate_retrieval.py
```

输出 Hit@4 / MRR / 分学科命中率。评测集 `evaluation/queries.json` 结构为
`id / subject / query / keywords / answer_keywords`，换成自己的语料后按同一结构重写即可复用全部脚本。

### 答案质量评测（端到端）

需后端已启动且模型可加载。脚本通过 `POST /api/agent/chat` 跑真实 SSE 链路，
并在本地复算检索上下文用于忠实度判定。

```powershell
python scripts\evaluate_answer_quality.py              # 全量（25 条）
python scripts\evaluate_answer_quality.py --limit 2    # 冒烟
```

- 指标：**引用正确率**（来源 doc_name/section 命中查询关键词）、**引用覆盖率**、
  **答案忠实度**（句级词面证据覆盖启发式，阈值 0.6，非严格 NLI）、**答案覆盖度**（命中 answer_keywords）。
- 报告：`evaluation/answer_quality_report.md`

### 性能测试

```powershell
python scripts\benchmark.py               # 检索延迟 + 模型加载/卸载内存 + 端到端 TTFT/吞吐
python scripts\benchmark.py --skip-model  # 仅检索延迟（不占用模型）
python scripts\benchmark.py --e2e 5       # 端到端采样 5 条
```

- 报告：`evaluation/performance_report.md`（机器信息 / 检索 avg·p50·p95 / 模型内存 / 端到端耗时 / TTFT·吞吐）
- 注：`/api/agent/chat`（多 Agent 全链路）在链路跑完后**一次性推送整段回答**，
  因此只统计端到端总耗时与整体吞吐；真实的首帧延迟 TTFT 与生成吞吐由
  `/api/chat/stream`（逐批 token 流）测得。

### 单元测试

```powershell
python -m pytest        # 62 项
python -m pytest -v     # 明细
```

覆盖 doc_id 规范化、解析器（TXT/MD）、自适应分块、DFA 敏感词审核、RRF 融合检索、绘图判定、基础 API，
以及 LLM Provider 层（配置脱敏、base_url 规整、OpenAI 兼容 SSE 解析、本地/远程选择与失败回退）。

### 可选：容器化部署

```powershell
docker compose up --build
# 前端 http://localhost:5173  →  nginx 反代 /api 到 backend:8000（SSE 关闭缓冲）
```

`models/` 与 `408-master/` 只读挂载，运行数据用命名卷 `kb_data` 持久化。
本地 `start.bat` 仍是主推启动方式，Docker 仅为可选加分项。

## 模型下载

模型已就位于 `models/`，换机器时按以下方式重新下载（国内用 hf-mirror 镜像）：

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"
pip install huggingface_hub

# 大模型 Qwen2.5-1.5B-Instruct GGUF Q4_K_M（约 1.04 GB）
huggingface-cli download Qwen/Qwen2.5-1.5B-Instruct-GGUF `
  qwen2.5-1.5b-instruct-q4_k_m.gguf --local-dir models

# Embedding bge-small-zh-v1.5 ONNX（约 100 MB）
huggingface-cli download onnx-community/bge-small-zh-v1.5-ONNX `
  --local-dir models/bge-small-zh-v1.5-onnx
```

## 演示流程

1. 启动后端与前端
2. 登录页注册一个账号（如 `demo` / `1234`）
3. **知识库页**：查看已导入的 140 个文档（含 98 个数据结构课件 + 23 个计组第 2 章课件）及各自 chunk 数，可上传新文档
4. **对话页**：提问「死锁产生的四个必要条件是什么？」
   - 观察打字机流式输出
   - 右侧实时显示当前执行到哪个 Agent
   - 回答下方显示 4 条引用来源（文档名、页码、段落、相关度）
5. 提问「画一个计算机网络七层模型」→ 回答下方渲染出 Mermaid 示意图
6. 提问「如何制造爆炸物？」→ 被审核 Agent 拦截，链路只走 3 个节点
7. **Agent 链路页**：查看流程图，已执行节点按状态着色，未执行节点置灰，附耗时条形图
8. **系统状态页**：查看模型加载状态、内存仪表盘、内存趋势、距离自动卸载的剩余秒数

## 项目结构

```
<项目目录>/
├── 408-master/              # 数据源：408 考研资料（四门学科，不入库，需自行补齐）
├── models/                  # 模型文件（已 gitignore，需自行下载）
│   ├── qwen2.5-1.5b-instruct-q4_k_m.gguf
│   └── bge-small-zh-v1.5-onnx/
├── backend/
│   ├── agent/               # LangGraph 多 Agent
│   │   ├── state/           # AgentState 共享状态
│   │   ├── graph/           # 工作流图（节点 + 条件边）
│   │   ├── dispatch_agent/  # 调度 + 汇总
│   │   ├── audit_agent/     # DFA 敏感词审核（不用 LLM）
│   │   ├── retrieval_agent/ # RRF 混合检索
│   │   ├── llm_gen_agent/   # 大模型生成
│   │   └── image_agent/     # Mermaid 示意图
│   ├── rag/
│   │   ├── parser/          # PDF / Word / TXT / Markdown
│   │   ├── chunker/         # 自适应分块 300-500 token，重叠 50-100
│   │   ├── embedding/       # bge-small-zh ONNX（onnxruntime，无 PyTorch）
│   │   ├── vector_store/    # Chroma 本地持久化
│   │   ├── bm25_index/      # jieba + rank_bm25
│   │   └── retriever/       # RRF 融合，Top10+Top10 → Top4
│   ├── llm_core/
│   │   ├── model_manager/   # 子进程管理、空闲卸载、请求锁
│   │   ├── model_worker/    # 独立进程加载 GGUF，HTTP 提供推理
│   │   └── providers/       # 本地 / OpenAI 兼容远程抽象（LLMService + 配置存储）
│   ├── database/
│   │   ├── models/          # 9 张表的 ORM
│   │   └── crud/            # CRUD 封装
│   ├── api/                 # auth / kb / chat / agent_log / stats / model / llm
│   ├── config/config.yaml   # 全部路径与参数
│   └── main.py
├── frontend/                # Vue3 + Vite + Element Plus + ECharts + mermaid
│   ├── src/views/           # 对话 / 知识库 / Agent链路 / 系统状态 / 数据大屏 / 模型设置 / 登录
│   └── nginx.conf           # 前端容器反代 /api 用（Docker 可选）
├── scripts/                 # 导入、重建、同步、检索评测、答案质量评测、性能测试
├── evaluation/              # 评测集与报告（queries.json / *_report.md）
├── test/                    # pytest 单元测试
├── data/                    # 运行时数据根：Chroma / BM25 / SQLite / 上传 / 图片（自包含，随项目迁移）
├── Dockerfile / Dockerfile.frontend / docker-compose.yml  # 可选容器化部署
├── STATUS.md                # 详细状态与实测数据
├── TRAE_PROMPT.md           # 原始需求
└── requirements.txt
```

## 接口清单

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/login` | 登录 |
| GET | `/api/kb/list` | 文档列表（含 chunk 数） |
| POST | `/api/kb/upload` | 上传并自动入库 |
| DELETE | `/api/kb/{doc_id}` | 删除文档及其全部 chunk |
| POST | `/api/agent/chat` | 多 Agent 流式问答（SSE） |
| POST | `/api/chat/stream` | 纯模型流式问答（SSE） |
| GET | `/api/chat/history` | 会话列表 / 指定会话的消息与日志 |
| DELETE | `/api/chat/conversations/{conversation_id}` | 删除历史会话（连同其消息 / Agent 日志 / 绘图记录） |
| GET | `/api/agent/logs/{conversation_id}` | Agent 链路日志 |
| GET | `/api/model/status` | 模型状态、内存、空闲计时、当前生效 provider |
| POST | `/api/model/load` | 手动加载模型（远程模式下为空操作） |
| POST | `/api/model/unload` | 手动卸载模型 |
| GET | `/api/llm/config` | 读取 LLM 配置（API Key 脱敏，仅回显末 4 位） |
| PUT | `/api/llm/config` | 保存 LLM 配置（本地 / OpenAI 兼容远程切换 + 自动卸载本地） |
| POST | `/api/llm/test` | 测试远程模型连通性 |
| GET | `/api/stats/dashboard` | 统计面板 |
| GET | `/api/stats/overview` | 数据大屏总览（文档/消息/会话/Agent 调用/近7天趋势/模型事件） |
| GET | `/api/health` | 健康检查 |

### 模型设置（本地 / 远程）

前端「模型设置」页可切换模型来源，无需改配置文件：

- **本地模型（默认）**：未配置 API Key 时始终使用本地 Qwen2.5，离线、CPU 推理、空闲 180s 自动卸载。
- **远程模型**：仅支持 OpenAI 兼容协议（`/chat/completions` 流式），可覆盖 OpenAI / DeepSeek / Moonshot / 智谱 / Ollama / vLLM / LM Studio 等，Base URL 填到 `…/v1` 即可。

行为约定：

- API Key 仅存后端 `llm_settings` 表；读取接口只回显末 4 位掩码，接口日志不记录明文。
- 切换到远程时后端自动卸载本地模型权重（释放约 1.3 GB 内存）。
- 远程模型未产出任何内容时：默认直接报错；勾选「失败回退本地」则自动改用本地模型继续作答。
- 本地 / 远程由 `LLMService` 统一抽象，`llm_gen_agent` 与 `/api/chat/stream` 均零改动复用同一套签名。

### SSE 事件类型

`/api/agent/chat` 依次推送：

| status | 载荷 | 说明 |
|---|---|---|
| `agent_log` | `{log:{agent_name,action,status,elapsed,...}}` | 每个节点执行完推一条 |
| `token` | `{token}` | 生成的文本（已批量合并） |
| `image` | `{image_type,code}` | Mermaid 源码 |
| `sources` | `{sources:[{doc_name,page,section,score}]}` | 引用来源 |
| `done` | `{conversation_id,agent_logs_count}` | 结束 |
| `error` | `{message}` | 出错 |

## 技术栈

| 层面 | 选型 |
|---|---|
| 后端 | FastAPI + Uvicorn + SSE |
| 多 Agent | LangGraph（StateGraph + 条件边） |
| 大模型 | 本地：Qwen2.5-1.5B-Instruct GGUF Q4_K_M，llama-cpp-python，纯 CPU；可选 OpenAI 兼容远程 |
| Embedding | bge-small-zh-v1.5 ONNX，onnxruntime CPUExecutionProvider |
| 向量库 | Chroma 本地持久化 |
| 检索 | BM25（jieba + rank_bm25）+ 向量 + RRF(k=60) |
| 数据库 | SQLite + SQLAlchemy |
| 审核 | DFA 敏感词 + 正则（不占用模型实例） |
| 绘图 | 模型自绘 Mermaid + 退化图自检重试 + 关键词模板兜底 |
| 前端 | Vue3 + Vite + Element Plus + ECharts + mermaid.js |

## 运行时数据目录

所有运行期产生的数据（Chroma 向量库 / BM25 索引 / SQLite 数据库 / 上传文件 / 示意图）
统一放在 `${KB_DATA_DIR:-data}/` 下：

- **默认** = 项目内 `data/`，项目自包含，**换机器直接拷贝项目即可**；
- 需要时用环境变量 `KB_DATA_DIR` 指向别处（例如受限沙箱指向工作区外的可写目录）：
  ```powershell
  $env:KB_DATA_DIR = "C:/Users/xxx/.trae-cn/work/xxx"
  ```
- 数据丢失/损坏时，一条命令从源码重建：`python scripts\rebuild_all.py --yes`。

## doc_id 约定与可复现性

知识库文档 ID 由 `backend/rag/doc_id.py` 唯一生成（`init_knowledge_base.py` 与 `import_ppt.py`
共用同一实现），规范化规则：**路径分隔符与空白字符统一转下划线 `_`、去掉扩展名**。

| 来源 | 形态 | 示例 |
|---|---|---|
| 笔记 `.md` | `kb_<相对 408-master 的路径>` | `kb_操作系统_第1章_计算机系统概述` |
| 课件 `.pdf` | `kb_<学科>_ppt_<章节>_<文件名>` | `kb_计算机组成原理_ppt_第2章_数据的表示和运算_2.1.1_进位计数制` |

- 同一份源码 → 同一套 doc_id：`rebuild_all.py` 幂等，重复重建不会产生重复文档。
- 当前库共 140 文档（19 笔记 + 121 课件），doc_id 中不含空格，且 Chroma / BM25 / SQLite 三端 doc_id 集合完全一致。

## 已知限制

- **计组第 2 章已补齐**：`第2章 数据的表示和运算.md` 是空文件（源仓库 README 已注明未整理），
  已从同章 23 个课件 PDF 导入补齐，IEEE754 等查询 Top1 命中。
- **数据结构靠课件补齐**：`408-master/数据结构/` 的章节笔记是手写扫描 PDF（提取 0 字符），
  已放开 `数据结构/ppt/` 路径过滤，导入其中 98 个文字版课件 PDF（2745 chunks），该学科检索命中率 5/5。
- **绘图以模型自绘为主、稳定性有限**：优先让模型在回答中自绘 Mermaid；后端会做退化图自检
  （涉及节点 < 3 或所有边均为自环）并重试一次，仍不合格则丢弃自绘图、退回关键词模板兜底
  （模板仅覆盖死锁、中断、调度、OS 层次、网络七层等少量主题）。模型自绘的语法笔误由
  `sanitize_mermaid_code` 清洗常见形式，极端情况仍可能渲染失败（前端会提示并展示源码）。
- **答案质量指标为启发式**：忠实度用句级词面证据覆盖（阈值 0.6），引用正确率用来源关键词命中，
  均为轻量代理指标，适合版本间回归对比，不等价于严格语义蕴含（NLI）评判。
- **敏感词政治类为占位符**：`data/sensitive_words.txt` 分类 6 共 15 条是占位标签，需自行替换。
- **登录无 token**：仅做用户名密码校验，未实现 JWT 会话。
- **后端在知识库变更后需重启**：后端启动时把 BM25 载入内存，批量导入/重建后若不重启，
  upload/delete 会用旧内存索引覆盖磁盘索引；用 `scripts/rebuild_bm25_from_chroma.py` 修复并重启。
- **远程模型仅支持 OpenAI 兼容协议**：单协议适配，未做多厂商差异化参数（如 `top_p`、`presence_penalty`）；
  远程模式的答案质量未做端到端评测（复现需自备 API Key，本项目交付时未提供）。
- **配置为全局单套**：`llm_settings` 只有一行，不区分用户；切换 provider 立即对全站生效。

## 协议

- **代码**：MIT License，见 [LICENSE](./LICENSE)。
- **演示语料**：`408-master/` 为第三方考研资料，版权归原作者所有，**不入库**，
  仅用于本地演示与学习，请勿再分发。


## 换用其他语料（通用性）

本系统的检索 / 问答 / 多 Agent 链路与语料无关，内置 408 考研资料仅作演示。换成其他资料：

1. 把新资料放入项目内某目录，或修改 `backend/config/config.yaml` 的 `datasource.path` 指向它；
2. 若新语料的课件需要额外补齐，编辑 `config.yaml` 的 `rebuild.courseware`
   （`subject` + 相对数据源的 `path`，支持通配符）；纯 `.md` 笔记语料无需登记；
3. （可选）按新资料重写 `evaluation/queries.json` 评测集；
4. 停掉后端后执行 `python scripts\rebuild_all.py --yes`，再重启后端。

> 注意：`.md` 笔记由 `init_knowledge_base.py` 全量扫描导入；扫描件 / 图片型 PDF 无 OCR，
> 会被跳过并给出提示。
