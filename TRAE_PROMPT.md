
---

# Trae 项目提示词文档：多 Agent 协同 RAG 知识库问答系统（低配本轻量化版）

## 一、你的角色与总目标

你是本项目的主程 AI。你的任务不是一次性生成全部代码，而是按阶段、可验证地搭建一个可在 8G 内存、无独显轻薄本上离线运行的私有知识库问答系统。

项目核心目标：
- 离线优先，文档不上传第三方。
- 本地 Qwen2.5-1.5B GGUF 模型，CPU 推理。
- Chroma 本地向量库，SQLite 本地数据库。
- LangGraph 多 Agent 协同。
- 支持文档上传、检索溯源、流式问答、内容审核、Agent 链路可视化、本地示意图生成。
- 模型独立子进程，空闲自动卸载，新请求自动加载。
- 前端 Vue3，后端 FastAPI。

初步完成范围：阶段 0 到阶段 4 必须完成；阶段 5 只做对话页、知识库页、Agent 链路页、系统状态页；阶段 6 作为可选加分，不要一开始就做。

## 二、硬性约束（必须遵守）

1. 内存优先：全局只允许一个模型实例，模型必须放在独立子进程，禁止在 FastAPI 主进程直接加载模型。
2. 模型空闲 180 秒自动卸载，卸载后内存必须明显下降；新请求异步拉起模型，前端显示“模型加载中”。
3. 模型使用 Qwen2.5-1.5B-Instruct GGUF Q4_K_M，llama-cpp-python，纯 CPU，n_ctx=2048，n_threads 为 CPU 核心数减一，n_gpu_layers=0，mmap=True，max_tokens=256。
4. Embedding 使用 bge-small-zh-v1.5 ONNX + onnxruntime，禁止使用 PyTorch、sentence-transformers 默认模型。
5. 向量库用 Chroma 本地持久化，不用外部向量数据库服务。
6. 检索用 BM25 + 向量 + RRF 融合，中文分词用 jieba，最终取 Top4，返回来源文档、页码、段落索引。
7. 数据库用 SQLite + SQLAlchemy，不用 MySQL；Redis 默认关闭，仅可选；Docker 仅可选，不作为唯一启动方式。
8. 审核 Agent 使用 DFA 敏感词 + 正则，不要用 1.5B 大模型做审核，避免抢模型实例。
9. 绘图优先本地 Mermaid / Graphviz 生成示意图，云端文生图仅可选，失败必须降级。
10. 前端用 Vue3 + Vite + Element Plus + ECharts + mermaid.js，不要引入大型 UI 框架。
11. 所有路径、参数必须写入配置文件，不要硬编码。
12. models、chroma_db、logs、static/images、__pycache__ 必须加入 .gitignore。
13. 不要一次性生成所有阶段代码；每阶段完成后输出报告并等待确认。
14. 不要伪造测试结果；无法验证时明确说明。
15. 如信息不足，先提问，不要自行假设。

## 三、技术栈

后端：Python 3.10/3.11、FastAPI、Uvicorn、SSE、SQLAlchemy、SQLite、LangGraph、Chroma、rank_bm25、jieba、onnxruntime、PyMuPDF、python-docx、psutil。
前端：Vue3、Vite、Element Plus、ECharts、mermaid.js。
模型：Qwen2.5-1.5B-Instruct GGUF Q4_K_M、bge-small-zh-v1.5 ONNX。
部署：本地脚本优先，Docker 可选。

## 四、项目目录结构

根目录包含：
models、backend、frontend、chroma_db、docs、evaluation、test、scripts、README.md、requirements.txt、.gitignore、TRAE_PROMPT.md。

backend 包含：
agent、rag、llm_core、database、api、config、logs、static/images、main.py。
agent 下包含 state、graph、dispatch_agent、audit_agent、retrieval_agent、llm_gen_agent、image_agent。
rag 下包含 parser、chunker、embedding、retriever、bm25_index、vector_store。
llm_core 下包含 model_manager、model_worker。
database 下包含 models、crud。
api 下包含 auth、kb、chat、agent_log、stats、model。

## 五、配置项

配置文件中至少包含：
模型路径、Embedding 模型目录、Chroma 持久化目录、SQLite 路径、上传目录、静态图片目录、模型空闲超时秒数、检索 TopK、生成 max_tokens、n_ctx、n_threads、是否启用 Redis、是否启用云端绘图、云端绘图 API 地址与超时。

## 六、分阶段任务

### 阶段 0：项目初始化
目标：创建目录结构、虚拟环境说明、requirements、README、.gitignore、配置文件。
不实现业务逻辑。
验收：目录正确，依赖可安装，README 说明环境要求、模型下载位置、启动方式。

### 阶段 1：模型独立进程与流式问答 MVP
目标：实现模型工作子进程、模型管理器、FastAPI 最小流式接口。
要求：模型懒加载、全局单实例、请求锁、空闲 180 秒卸载、手动加载卸载接口、模型状态接口。
验收：启动后端，调用流式问答接口，能流式返回中文回答；等待 3 分钟，日志显示模型卸载且内存下降；再次提问自动加载。

### 阶段 2：RAG 基础
目标：文档解析、自适应分块、ONNX Embedding、Chroma 增删改查、BM25 索引、RRF 混合检索。
要求：支持 PDF、Word、TXT、Markdown；分块目标 300-500 token，重叠 50-100 token；返回来源、页码、段落索引；中文分词用 jieba。
验收：上传文档，提问，检索命中正确片段；提供检索测试接口或脚本；记录 Hit@4、MRR、耗时。

### 阶段 3：LangGraph 多 Agent
目标：状态定义、节点、条件边、Agent 日志。
流程：用户提问 → 输入审核 → 不合规结束 → 检索 → 生成 → 输出审核 → 判断是否需要图片 → 需要则绘图 → 汇总结束。
要求：审核用 DFA；生成流式；图片判断用关键词或意图；绘图优先本地 Mermaid；日志记录 Agent 名、动作、输入摘要、输出摘要、耗时、状态。
验收：普通问题走完整链路；违规输入被拦截；图片问题进入绘图分支；前端能收到 agent_log 事件。

### 阶段 4：SQLite 与 API 完善
目标：建表、CRUD、历史对话、Agent 日志、模型状态、统计接口。
表：用户、文档、块、会话、消息、Agent 日志、模型日志、图片记录。
接口：登录注册、知识库上传/列表/删除、流式对话、历史、Agent 日志、模型状态/加载/卸载、统计。
验收：数据落库；Swagger 可见；重启后数据仍在。

### 阶段 5：前端核心
页面优先级：对话页 > 知识库管理 > Agent 链路 > 系统状态 > 登录。
对话页：SSE 流式解析，打字机，Markdown 渲染，图片展示，引用来源。
知识库页：上传、列表、删除、块数量。
Agent 链路：ECharts 图或时间线，展示调度、审核、检索、生成、绘图，按日志高亮。
系统状态：模型是否加载、内存占用、最近卸载时间。
验收：前端可完成上传、问答、看链路、看状态。

### 阶段 6：可选加分
数据大屏、本地 Mermaid 图渲染、云端文生图可选、Docker 可选、单元测试、性能测试报告。
不在初步完成范围内，除非前五阶段已稳定。

## 七、接口约定

后端至少提供：
POST /api/auth/register、POST /api/auth/login。
GET /api/kb/list、POST /api/kb/upload、DELETE /api/kb/{id}。
POST /api/chat/stream，SSE，事件类型：token、agent_log、image、done、error。
GET /api/chat/history、GET /api/agent/logs/{conversation_id}。
GET /api/model/status、POST /api/model/load、POST /api/model/unload。
GET /api/stats/dashboard。

## 八、Agent 日志与可视化

每条日志包含：会话 ID、Agent 名、动作、输入摘要、输出摘要、状态、耗时、创建时间。
前端根据实际经过的节点高亮链路，不要伪造未执行节点。

## 九、每阶段输出格式

每次完成一个阶段后，必须输出：
1. 阶段名称。
2. 完成内容。
3. 新增/修改文件清单。
4. 启动命令。
5. 验证步骤与预期结果。
6. 已知问题与风险。
7. 下一阶段建议。
然后停下，等待用户确认，不要自动进入下一阶段。

## 十、禁止事项

不要一次性生成全部代码。
不要跳过验证。
不要在 FastAPI 主进程加载模型。
不要用 PyTorch、CUDA、MySQL、Redis 必需服务。
不要用大模型做内容审核。
不要把模型和向量库提交到 git。
不要伪造测试结果。
不要引入需要付费或必须联网的核心组件。

## 十一、初步完成验收清单

1. 8G 内存机器可启动后端和前端。
2. 模型独立子进程，空闲 3 分钟卸载，内存下降。
3. 上传 PDF/Word/TXT，能检索并返回来源。
4. 流式问答可用，前端打字机显示。
5. 违规输入被审核拦截。
6. 图片问题触发本地 Mermaid 示意图。
7. Agent 链路可视化能显示实际节点和耗时。
8. SQLite 保存用户、文档、对话、日志。
9. Swagger 可访问。
10. README 写清模型下载、启动方式、演示流程。

## 十二、初始指令

请先阅读以上全部内容，确认你理解项目目标、硬性约束和阶段划分。
确认后，只执行阶段 0：项目初始化。
阶段 0 完成后，按“每阶段输出格式”输出报告，然后等待我的确认。
不要提前实现阶段 1 及以后的内容。