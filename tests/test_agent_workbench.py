"""
坐席工作台测试 — 会话队列 / 坐席回复 / Copilot / 角色鉴权

覆盖:
- require_staff 角色鉴权（admin / agent 可访问，customer 拒绝）
- /agent/conversations 会话队列
- /agent/conversations/{id}/reply 坐席回复（写入消息）
- /agent/conversations/{id}/copilot Copilot 建议
- /agent/conversations/{id}/stream SSE 订阅
"""
import pytest
from unittest.mock import patch, AsyncMock

from fastapi import HTTPException


# ============================================================
# require_staff 角色鉴权
# ============================================================

class TestRequireStaff:
    @pytest.mark.asyncio
    async def test_agent_role_allowed(self):
        """agent 角色 JWT 应通过 require_staff"""
        from src.api.auth import create_token, require_staff
        from starlette.requests import Request
        from starlette.datastructures import Headers

        token = create_token("u1", "agent1", "agent")
        class FakeRequest:
            headers = Headers({"authorization": f"Bearer {token}"})
            query_params = {}
            client = type("C", (), {"host": "127.0.0.1"})()
        result = await require_staff(FakeRequest())
        assert result.startswith("staff_authenticated:agent")

    @pytest.mark.asyncio
    async def test_admin_role_allowed(self):
        """admin 角色 JWT 应通过 require_staff"""
        from src.api.auth import create_token, require_staff
        from starlette.datastructures import Headers

        token = create_token("u2", "boss", "admin")
        class FakeRequest:
            headers = Headers({"authorization": f"Bearer {token}"})
            query_params = {}
            client = type("C", (), {"host": "127.0.0.1"})()
        result = await require_staff(FakeRequest())
        assert result.startswith("staff_authenticated:admin")

    @pytest.mark.asyncio
    async def test_customer_role_rejected(self):
        """customer 角色 JWT 应被拒绝"""
        from src.api.auth import create_token, require_staff
        from starlette.datastructures import Headers

        token = create_token("u3", "cust1", "customer")
        class FakeRequest:
            headers = Headers({"authorization": f"Bearer {token}"})
            query_params = {}
            client = type("C", (), {"host": "127.0.0.1"})()
        with pytest.raises(HTTPException) as exc:
            await require_staff(FakeRequest())
        assert exc.value.status_code == 401


# ============================================================
# 坐席回复
# ============================================================

class TestAgentReply:
    @pytest.mark.asyncio
    async def test_agent_reply_writes_message(self):
        """坐席回复应写入一条 human 消息"""
        from src.api.agent_routes import AgentReplyRequest, agent_reply
        from src.models.conversation import Session, ConversationStatus, Tier

        mock_store = AsyncMock()
        mock_store.get_session.return_value = Session(
            session_id="sess_a1",
            status=ConversationStatus.HANDOFF,
            current_tier=Tier.HUMAN,
        )
        mock_store.save_message.return_value = None
        mock_store.update_session.return_value = None

        with patch("src.api.agent_routes.get_storage", return_value=mock_store), \
             patch("src.api.agent_routes.publish_session_event", new=AsyncMock()) as pub:
            req = AgentReplyRequest(content="您好，我来帮您处理。", mark_resolved=True)
            resp = await agent_reply("sess_a1", req, _auth="staff_authenticated:agent")

        assert resp["status"] == "sent"
        # 消息写入校验：agent_name 应为 human
        saved_msg = mock_store.save_message.call_args[0][1]
        assert saved_msg.agent_name == "human"
        assert saved_msg.content == "您好，我来帮您处理。"
        # 标记解决后应广播
        pub.assert_called_once()


# ============================================================
# 会话队列
# ============================================================

class TestConversationQueue:
    @pytest.mark.asyncio
    async def test_list_conversations(self):
        """会话队列应返回 handoff 会话与角标"""
        from src.api.agent_routes import list_conversations
        from src.models.conversation import Session, ConversationStatus, Tier

        sessions = [
            Session(session_id="sess_h1", status=ConversationStatus.HANDOFF, current_tier=Tier.HUMAN, updated_at="2026-08-28T12:00:00"),
            Session(session_id="sess_h2", status=ConversationStatus.AWAITING_REVIEW, current_tier=Tier.SUPERVISOR, updated_at="2026-08-28T12:05:00"),
        ]

        # 模拟 get_session_factory()() 为 async 上下文管理器，yield 一个可用 db
        fake_db = object()
        class FakeFactory:
            def __call__(self):
                return self
            async def __aenter__(self):
                return fake_db
            async def __aexit__(self, *a):
                return False

        mock_storage = AsyncMock()
        mock_storage.get_history.return_value = [{"role": "user", "content": "我要找人工"}]

        with patch("src.memory.database.get_session_factory", FakeFactory()), \
             patch("src.memory.session.SessionStore.list_by_status", new=AsyncMock(return_value=sessions)), \
             patch("src.memory.session.SessionStore.count_by_status", new=AsyncMock(return_value=2)), \
             patch("src.api.agent_routes.get_storage", return_value=mock_storage):
            resp = await list_conversations(status=None, limit=50, _auth="staff_authenticated:agent")

        assert resp["total"] == 2
        assert resp["badges"]["handoff"] == 2
        assert resp["conversations"][0]["session_id"] == "sess_h1"


# ============================================================
# Copilot（坐席侧）
# ============================================================

class TestAgentCopilot:
    @pytest.mark.asyncio
    async def test_agent_copilot_delegates(self):
        """坐席 Copilot 应复用管理员 Copilot 能力"""
        from src.api.agent_routes import CopilotRequest, agent_copilot

        with patch(
            "src.api.admin_routes.copilot_assist",
            new=AsyncMock(return_value=type("R", (), {
                "suggested_reply": "别着急，我马上为您核实订单进度",
                "context_summary": "客户订单延迟",
                "suggested_tools": ["order_lookup"],
                "risk_flags": [],
                "log_id": "cop_legacy",
            })()),
        ):
            req = CopilotRequest(session_id="sess_c1", customer_message="订单没到")
            resp = await agent_copilot("sess_c1", req, _auth="staff_authenticated:agent")

        assert resp.suggested_reply
        assert "order_lookup" in resp.suggested_tools
        assert resp.log_id == "cop_legacy"

    @pytest.mark.asyncio
    async def test_agent_copilot_passthrough_risk_and_log(self):
        """坐席 Copilot 应透传 risk_flags 与 log_id（回归: 曾漏字段）"""
        from src.api.agent_routes import CopilotRequest, agent_copilot

        from src.api.admin_routes import CopilotRiskFlag
        core = type("Core", (), {
            "suggested_reply": "已登记",
            "context_summary": "退款诉求",
            "suggested_tools": ["order_lookup"],
            "risk_flags": [CopilotRiskFlag(type="high_value", level="high", message="大额退款需审批")],
            "log_id": "cop_passthrough123",
        })()

        with patch(
            "src.api.admin_routes.copilot_assist",
            new=AsyncMock(return_value=core),
        ):
            req = CopilotRequest(session_id="sess_p1", customer_message="退款800")
            resp = await agent_copilot("sess_p1", req, _auth="staff_authenticated:agent")

        assert resp.risk_flags and resp.risk_flags[0].type == "high_value"
        assert resp.log_id == "cop_passthrough123"
