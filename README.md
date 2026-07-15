# Multi-Tier Customer Service System

多层级智能客服协同系统 — 基于多 Agent 协作的生产级客服系统。

## 架构概览

```
Client → FastAPI → LangGraph Workflow
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
      Triage      Specialist    Supervisor
      Agent        Agents         Agent
         │         (4 domain)       │
         │             │             │
         └─────────────┼─────────────┘
                       ▼
                 Tool Layer
         (FAQ / CRM / Order / Ticket)
```

### Agent 层级

| 层级 | Agent | 职责 |
|------|-------|------|
| Tier 1 | **TriageAgent** | 意图识别、情感分析、紧急度判断、路由决策 |
| Tier 2 | **TechnicalAgent** | 技术问题诊断、排查指导、Bug 报告 |
| Tier 2 | **BillingAgent** | 账单查询、退款处理、账户管理 |
| Tier 2 | **ProductAgent** | 产品咨询、对比推荐、使用指导 |
| Tier 2 | **ComplaintAgent** | 投诉处理、情绪安抚、补偿方案 |
| Tier 3 | **SupervisorAgent** | 升级审核、终局决策、人工转接判定 |

### 对话流程

```
用户: "我订单 #12345 还没收到！"
  │
  ▼
[Triage] → intent=ORDER_STATUS, sentiment=ANGRY, urgency=HIGH → route to complaint
  │
  ▼
[Complaint] → CRM lookup → order status → 补偿方案
  │  用户不接受 → 升级
  ▼
[Supervisor] → 审核 → 批准退款+优惠券 → 回复
  │
  ▼
[END] resolution="已退款+补偿100元优惠券"
```

## 快速启动

### 1. 环境准备

```bash
# 克隆/进入项目
cd customer-service

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -e .
```

### 2. 配置

```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env，填入你的 API Key
# LLM_PROVIDER=dashscope     # dashscope | openai | claude
# DASHSCOPE_API_KEY=sk-xxx   # 阿里云 DashScope
# OPENAI_API_KEY=sk-xxx      # OpenAI
# ANTHROPIC_API_KEY=sk-ant-xxx  # Claude
```

### 3. 启动服务

```bash
# 开发模式（热重载）
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# 或直接运行
python src/main.py
```

### 4. 访问

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **健康检查**: http://localhost:8000/health

## API 使用

### 创建会话 → 对话

```bash
# 1. 创建新会话
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"customer_id": "cust_001"}'

# 返回: {"session_id": "sess_abc123...", ...}

# 2. 发起对话
curl -X POST http://localhost:8000/chat/sess_abc123 \
  -H "Content-Type: application/json" \
  -d '{"message": "我的订单还没收到，已经等了5天了！"}'

# 返回: {"session_id": "...", "reply": "...", "agent_name": "complaint", "intent": "order_status", ...}

# 3. 获取对话历史
curl http://localhost:8000/chat/sess_abc123/history
```

### SSE 流式对话

```bash
curl -N http://localhost:8000/chat/sess_abc123/stream?message=我的App一直闪退
```

## Docker 部署

```bash
# 启动 PostgreSQL + App
docker-compose up -d

# 查看日志
docker-compose logs -f app

# 停止
docker-compose down
```

## 项目结构

```
customer-service/
├── config.py                 # 配置中心
├── .env.example              # 环境变量模板
├── pyproject.toml            # 依赖管理
├── docker-compose.yml        # Docker 编排
├── Dockerfile                # 应用镜像
├── src/
│   ├── main.py               # FastAPI 入口
│   ├── state.py              # LangGraph 状态定义
│   ├── llm/                  # LLM 抽象层（3个 Provider）
│   ├── models/               # Pydantic 数据模型
│   ├── agents/               # Agent 层（6个 Agent）
│   ├── tools/                # 工具层（注册中心 + 5个工具）
│   ├── graph/                # LangGraph 工作流
│   ├── memory/               # PostgreSQL 持久化
│   ├── knowledge/            # ChromaDB 知识库
│   └── api/                  # FastAPI 路由/中间件
├── tests/                    # 48 个测试用例
└── data/                     # FAQ 示例数据
```

## 运行测试

```bash
pytest tests/ -v              # 全部测试
pytest tests/test_llm.py -v   # LLM 层测试
pytest tests/test_workflow.py -v  # 集成测试
```

## 技术栈

| 层 | 技术 |
|---|---|
| 编排 | LangGraph (StateGraph) |
| LLM | DashScope / OpenAI / Claude |
| API | FastAPI + SSE |
| 存储 | PostgreSQL + asyncpg |
| 向量检索 | ChromaDB |
| 测试 | pytest + pytest-asyncio |

## 设计原则

- **低耦合**: Agent 只通过 State 和 Tool Registry 交互
- **高内聚**: 每个 Agent 自包含（prompt + tools + schema），独立可测
- **可扩展**: 新增 Agent = 写类 → 注册节点 → 添加路由
- **可观测**: 全链路日志 + Agent 错误追踪
- **生产就绪**: 异步全链路、连接池、优雅关闭、健康检查
