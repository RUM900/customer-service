# 多 Agent 智能客服与坐席辅助系统

面向客服咨询场景的多 Agent 协作演示项目。系统使用 LangGraph 编排分诊、领域处理、工具调用和主管升级流程，并提供知识库问答、人工审核与坐席工作台。

> 当前仓库展示的是可本地运行和评估的项目实现。CRM、订单等业务工具使用项目数据库和演示数据；没有证据表明已接入真实企业系统或经过生产流量验证。

## 项目概述

系统收到客户消息后，由分诊 Agent 识别意图、情绪和处理方向，再将请求路由到 FAQ 知识问答或相应领域 Agent。领域处理过程中可以调用工具；无法解决或需要升级的请求交由 Supervisor 处理。对于模型判断需要人工审核的决策，工作流通过 LangGraph interrupt 暂停，管理员审核后再恢复执行。坐席工作台提供会话队列、人工回复和 AI Copilot。

## 核心能力

- **多 Agent 工作流**：LangGraph StateGraph 编排分诊、FAQ、4 个领域 Agent、工具执行、Supervisor 和人工转接等节点，并通过条件路由控制流转。
- **知识库问答**：使用 ChromaDB 检索 FAQ；达到配置的置信度阈值时直接回答，未达到时转入后续处理。默认阈值为 0.75。
- **人工审核与恢复**：Supervisor 根据模型输出决定是否需要人工审核；工作流可通过 interrupt 暂停，管理员批准或驳回后恢复处理并生成后续回复。
- **坐席工作台与 Copilot**：提供独立的客服工作台、会话实时订阅（SSE）、人工回复和辅助话术能力。
- **模型与服务接口**：通过统一 LLM 抽象支持 DashScope、OpenAI 兼容接口和 Claude Provider；FastAPI 提供对话、管理、知识库和坐席相关接口。
- **基础安全与运维能力**：包含 JWT/API Key 认证配置、角色校验、请求限流和基于规则模式的 Prompt Injection 检测；提供数据库迁移、健康检查及 LLM 调用重试等实现。

## 架构与处理流程

项目采用“API 接入 + LangGraph 编排 + Agent/工具 + 数据与知识库”的结构。下图展示主要请求路径，具体分支由工作流状态和模型输出决定。

~~~mermaid
flowchart TD
    U[客户消息] --> API[FastAPI]
    API --> T[Triage 分诊]
    T -->|FAQ| F[FAQ 检索与回答]
    F --> E[结束]
    T -->|技术 / 账单 / 产品 / 客诉| S[领域 Agent]
    S <--> TOOLS[工具执行]
    S -->|解决| E
    S -->|升级| SUP[Supervisor]
    SUP -->|直接处理| E
    SUP -->|模型判断需审核| I[interrupt 暂停]
    I --> R[管理员审核]
    R -->|批准或驳回| RESUME[恢复工作流]
    RESUME --> SUP
    T -->|转人工| H[人工队列]
    SUP -->|转人工| H
    H --> E
~~~

- **分诊 Agent**：意图识别、情绪分析和路由决策。
- **领域 Agent**：Technical、Billing、Product、Complaint，分别处理技术、账单、产品和客诉问题。
- **FAQ 节点**：单独的知识检索与回答节点，不应与四个领域 Agent 混为一谈。
- **Supervisor**：处理升级请求、跨领域协调和转人工决策；人工审核是否触发由模型输出驱动，不能理解为所有指定金额都由确定性业务规则自动拦截。
- **状态与存储**：对话与业务数据使用 SQLAlchemy 支持的 SQLite/PostgreSQL；LangGraph Checkpointer 支持 MemorySaver/PostgresSaver；向量检索使用 ChromaDB。

## 快速启动

### 环境要求

- Python 3.10+
- 一个已配置的聊天模型 Provider 凭证
- FAQ 向量嵌入当前通过 DashScope API 调用，因此默认配置还需要 DashScope API Key

### 安装与配置

~~~bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1

pip install -e .
cp .env.example .env
~~~

编辑 .env，至少配置所选聊天模型的 API Key。默认聊天 Provider 是 DashScope；如改用 OpenAI 或 Claude，请同步设置 Provider 和对应凭证。FAQ 嵌入仍使用 DashScope 配置。

本地默认使用 SQLite 和内存 Checkpointer，无需先启动 PostgreSQL。内存 Checkpointer 的状态在进程重启后不会保留。若需要验证持久化流程，可使用 Docker Compose 提供的 PostgreSQL 环境，并检查其凭证和 Checkpointer 配置。

~~~bash
uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
~~~

启动后可访问：

| 页面 / 接口 | 地址 |
|---|---|
| 客户聊天页面 | http://127.0.0.1:8000/ |
| 管理后台 | http://127.0.0.1:8000/admin |
| 坐席工作台 | http://127.0.0.1:8000/agent |
| OpenAPI 文档 | http://127.0.0.1:8000/docs |
| 健康检查 | http://127.0.0.1:8000/health |

首次启动默认管理员和坐席账号分别为 admin/admin123、agent/agent123。它们仅适合本地演示，部署前应修改。客户端 API Key 未配置时，当前代码会跳过对应 API Key 校验；不要把这一默认配置当作生产认证方案。

## 关键接口

完整参数与响应以运行中的 OpenAPI 文档为准。常用能力包括：

| 能力 | 路由范围 |
|---|---|
| 客户会话与对话 | /sessions、/chat、/tickets |
| 管理与模型配置 | /admin |
| FAQ 与知识库 | /admin/faqs、/admin/knowledge |
| 坐席会话与 Copilot | /agent |
| 健康检查 | /health |

示例：创建会话并发送消息。

~~~bash
curl -X POST http://127.0.0.1:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"cust_001"}'

# 将 URL 中的 sess_abc123 替换为创建会话响应中的 session_id
curl -X POST http://127.0.0.1:8000/chat/sess_abc123 \
  -H "Content-Type: application/json" \
  -d '{"message":"我的订单还没收到，已经等了5天"}'
~~~

## 评估与验证

仓库包含 pytest 测试代码，以及意图评估集和端到端场景集。测试和评估脚本可用于本地验证；评估结果会受模型、提示词和运行配置影响。

~~~bash
pytest tests/ -v

# 意图离线评估：会调用配置的 LLM
python -m tests.evals.eval_intent --save

# 端到端评估
python -m tests.evals.eval_end_to_end --save
~~~

### 意图评估记录

| 运行日期 | Provider / 模型 | 样本数 | 意图准确率 | 路由准确率 | 平均置信度 | 报告 |
|---|---|---:|---:|---:|---:|---|
| 2026-10-14 | OpenAI-compatible API / DeepSeek deepseek-flash | 50 | 82.00% | 84.00% | 0.89 | [评估报告](tests/evals/reports/eval_results_20261014_200156.json) |
| 2026-08-24 | 模型信息未记录 | 50 | 82.00% | 82.00% | 0.90 | [历史报告](tests/evals/reports/eval_results_20260824_225016.json) |

以上为同一份 50 条标注样本上的离线单次运行记录，不代表线上效果，也不足以证明模型或系统整体性能提升。仓库目前没有说明样本来源和标注流程；最新报告记录了 Provider、模型、并发数和样本文件。

端到端场景文件 `tests/evals/e2e_scenarios.json` 当前包含 85 条场景。场景数量表示测试数据规模，不等于通过率或业务效果。

## 技术栈

| 模块 | 技术 |
|---|---|
| Agent 编排 | LangGraph StateGraph、Checkpointer、interrupt/resume |
| API 与事件推送 | FastAPI、SSE |
| LLM | DashScope、OpenAI 兼容接口、Claude Provider |
| 持久化 | SQLAlchemy、SQLite、PostgreSQL、Alembic |
| 知识检索 | ChromaDB、DashScope Embedding API |
| 数据校验 | Pydantic |
| 测试 | pytest、pytest-asyncio |

## 项目结构

~~~text
customer-service/
├── config.py                 # 环境与模型配置
├── pyproject.toml            # 依赖与项目配置
├── docker-compose.yml        # 本地 PostgreSQL + App 编排
├── src/
│   ├── agents/               # 分诊、领域处理与 Supervisor Agent
│   ├── api/                  # FastAPI 路由、认证与安全逻辑
│   ├── graph/                # LangGraph 工作流与 Checkpointer
│   ├── knowledge/            # 文档处理、向量化与检索
│   ├── llm/                  # Provider 抽象、工厂与重试
│   ├── memory/               # 会话及业务数据存储
│   ├── static/               # 客户端、管理端和坐席工作台页面
│   └── tools/                # CRM、订单、知识库和工单工具
├── migrations/               # Alembic 数据库迁移
└── tests/
    └── evals/                # 离线评估脚本、样本与场景
~~~

## 当前实现边界

- CRM 和订单查询使用本项目数据库及演示数据；代码注释将外部 CRM/订单 API 集成列为后续替换方向。
- Prompt Injection 检测采用规则模式匹配；它是基础检测逻辑，不代表覆盖所有攻击方式。高风险判定也不等于完整的安全防护体系。
- Docker Compose 配置用于本地联调和持久化验证；其中包含演示凭证/默认密钥，不应不经修改直接用于公开部署。
- Checkpointer 在 PostgreSQL 初始化失败时存在回退到 MemorySaver 的逻辑；配置 PostgreSQL 不等于已验证故障恢复或生产级持久化保障。
- 仓库未提供线上部署、真实客服业务接入、生产流量或业务收益数据，因此 README 不对这些结果作承诺。
