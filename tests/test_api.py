"""
API 集成测试 — 测试 FastAPI 端点

使用 TestClient 测试核心 API 端点，Mock LLM 调用。

运行方式:
    pytest tests/test_api.py -v
"""
import pytest
from unittest.mock import patch, AsyncMock


# ============================================================
# 延迟导入 app（避免模块级导入错误）
# ============================================================

@pytest.fixture(scope="module")
def app():
    """延迟导入 FastAPI app"""
    from src.main import app as fastapi_app
    return fastapi_app


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def client(app):
    """创建测试客户端"""
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """认证头（AUTH_ENABLED=false 时可省略，但保持一致性）"""
    return {"X-API-Key": "test-api-key"}


@pytest.fixture
def mock_workflow_result():
    """模拟 workflow 执行结果"""
    from src.models.conversation import ConversationStatus, Tier
    return {
        "session_id": "sess_test_001",
        "user_message": "我的订单到哪了",
        "messages": [],
        "triage_result": {
            "primary_intent": "order_status",
            "intent_confidence": 0.92,
            "sentiment": "neutral",
            "urgency": "medium",
            "recommended_agent": "technical",
        },
        "specialist_response": {
            "reply_to_customer": "您好，我来帮您查询订单状态。",
            "is_resolved": True,
            "needs_escalation": False,
        },
        "final_reply": "您好，我来帮您查询订单状态。",
        "status": ConversationStatus.RESOLVED.value,
        "active_agent": "technical",
        "current_tier": Tier.SPECIALIST.value,
        "errors": [],
    }


# ============================================================
# Health Check
# ============================================================

class TestHealth:
    """健康检查端点测试"""

    def test_health_ok(self, client):
        """健康检查应返回 200"""
        response = client.get("/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "ok"
        assert "llm_provider" in data
        assert "database" in data

    def test_health_returns_provider(self, client):
        """健康检查应返回当前 LLM Provider"""
        response = client.get("/health")
        data = response.json()
        # Provider 应该是配置的值之一
        assert data["llm_provider"] in ["dashscope", "openai", "claude"]


# ============================================================
# Session Management
# ============================================================

class TestSessions:
    """会话管理端点测试"""

    @patch("src.api.routes.get_storage")
    def test_create_session(self, mock_storage, client, auth_headers):
        """创建会话应返回 session_id"""
        from src.models.conversation import Session, ConversationStatus, Tier
        from datetime import datetime

        now_iso = datetime.utcnow().isoformat()
        mock_session = Session(
            session_id="sess_new_001",
            customer_id="cust_001",
            status=ConversationStatus.ACTIVE,
            current_tier=Tier.TRIAGE,
            turn_count=0,
            created_at=now_iso,
            updated_at=now_iso,
        )

        mock_store = AsyncMock()
        mock_store.create_session.return_value = mock_session
        mock_storage.return_value = mock_store

        response = client.post(
            "/sessions",
            json={"customer_id": "cust_001"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "sess_new_001"
        assert data["customer_id"] == "cust_001"
        assert data["status"] == "active"

    @patch("src.api.routes.get_storage")
    def test_create_session_without_customer_id(self, mock_storage, client, auth_headers):
        """无 customer_id 也能创建会话"""
        from src.models.conversation import Session, ConversationStatus, Tier
        from datetime import datetime

        now_iso = datetime.utcnow().isoformat()
        mock_session = Session(
            session_id="sess_anon_001",
            customer_id=None,
            status=ConversationStatus.ACTIVE,
            current_tier=Tier.TRIAGE,
            turn_count=0,
            created_at=now_iso,
            updated_at=now_iso,
        )

        mock_store = AsyncMock()
        mock_store.create_session.return_value = mock_session
        mock_storage.return_value = mock_store

        response = client.post(
            "/sessions",
            json={},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data

    @patch("src.api.routes.get_storage")
    def test_get_session_not_found(self, mock_storage, client, auth_headers):
        """获取不存在的会话应返回 404"""
        mock_store = AsyncMock()
        mock_store.get_session.return_value = None
        mock_storage.return_value = mock_store

        response = client.get(
            "/sessions/nonexistent_session",
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert "不存在" in response.json()["detail"]


# ============================================================
# Chat Endpoint
# ============================================================

class TestChat:
    """核心对话端点测试"""

    @patch("src.api.routes.get_storage")
    @patch("src.graph.workflow.run_customer_service")
    def test_chat_success(
        self, mock_workflow, mock_storage, client, auth_headers, mock_workflow_result
    ):
        """正常对话应返回回复"""
        mock_workflow.return_value = mock_workflow_result

        mock_store = AsyncMock()
        mock_store.get_history.return_value = []
        mock_store.get_session.return_value = None
        mock_store.save_message.return_value = None
        mock_store.update_session.return_value = None
        mock_storage.return_value = mock_store

        response = client.post(
            "/chat/sess_test_001",
            json={"message": "我的订单到哪了"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "sess_test_001"
        assert "reply" in data
        assert data["reply"] != ""
        assert data["status"] == "resolved"

    @patch("src.api.routes.get_storage")
    @patch("src.graph.workflow.run_customer_service")
    def test_chat_returns_intent(
        self, mock_workflow, mock_storage, client, auth_headers, mock_workflow_result
    ):
        """对话应返回识别的意图"""
        mock_workflow.return_value = mock_workflow_result

        mock_store = AsyncMock()
        mock_store.get_history.return_value = []
        mock_store.get_session.return_value = None
        mock_store.save_message.return_value = None
        mock_store.update_session.return_value = None
        mock_storage.return_value = mock_store

        response = client.post(
            "/chat/sess_test_001",
            json={"message": "我的订单到哪了"},
            headers=auth_headers,
        )

        data = response.json()
        assert data["intent"] == "order_status"

    @patch("src.api.routes.get_storage")
    @patch("src.graph.workflow.run_customer_service")
    def test_chat_returns_agent_name(
        self, mock_workflow, mock_storage, client, auth_headers, mock_workflow_result
    ):
        """对话应返回处理的 Agent 名称"""
        mock_workflow.return_value = mock_workflow_result

        mock_store = AsyncMock()
        mock_store.get_history.return_value = []
        mock_store.get_session.return_value = None
        mock_store.save_message.return_value = None
        mock_store.update_session.return_value = None
        mock_storage.return_value = mock_store

        response = client.post(
            "/chat/sess_test_001",
            json={"message": "我的订单到哪了"},
            headers=auth_headers,
        )

        data = response.json()
        assert data["agent_name"] == "technical"

    @patch("src.api.routes.get_storage")
    @patch("src.graph.workflow.run_customer_service")
    def test_chat_with_customer_id(
        self, mock_workflow, mock_storage, client, auth_headers, mock_workflow_result
    ):
        """对话可以带 customer_id"""
        mock_workflow.return_value = mock_workflow_result

        mock_store = AsyncMock()
        mock_store.get_history.return_value = []
        mock_store.get_session.return_value = None
        mock_store.save_message.return_value = None
        mock_store.update_session.return_value = None
        mock_storage.return_value = mock_store

        response = client.post(
            "/chat/sess_test_001",
            json={"message": "我的订单到哪了", "customer_id": "cust_001"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        # 验证 workflow 被正确调用
        mock_workflow.assert_called_once()
        call_kwargs = mock_workflow.call_args
        assert call_kwargs[1]["customer_id"] == "cust_001"

    @patch("src.api.routes.get_storage")
    @patch("src.graph.workflow.run_customer_service")
    def test_chat_error_handling(
        self, mock_workflow, mock_storage, client, auth_headers
    ):
        """workflow 异常应返回 500"""
        mock_workflow.side_effect = Exception("LLM 调用失败")

        mock_store = AsyncMock()
        mock_store.get_history.return_value = []
        mock_store.get_session.return_value = None
        mock_storage.return_value = mock_store

        response = client.post(
            "/chat/sess_test_001",
            json={"message": "测试"},
            headers=auth_headers,
        )

        assert response.status_code == 500


# ============================================================
# Chat History
# ============================================================

class TestChatHistory:
    """对话历史端点测试"""

    @patch("src.api.routes.get_storage")
    def test_get_history_empty(self, mock_storage, client, auth_headers):
        """空历史应返回空列表"""
        mock_store = AsyncMock()
        mock_store.get_history.return_value = []
        mock_storage.return_value = mock_store

        response = client.get(
            "/chat/sess_test_001/history",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "sess_test_001"
        assert data["messages"] == []
        assert data["total"] == 0

    @patch("src.api.routes.get_storage")
    def test_get_history_with_messages(self, mock_storage, client, auth_headers):
        """有消息的历史应正确返回"""
        mock_messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "您好，有什么可以帮您？"},
        ]

        mock_store = AsyncMock()
        mock_store.get_history.return_value = mock_messages
        mock_storage.return_value = mock_store

        response = client.get(
            "/chat/sess_test_001/history",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["messages"]) == 2


# ============================================================
# Authentication
# ============================================================

class TestAuthentication:
    """认证测试"""

    @patch("config.AUTH_ENABLED", True)
    @patch("config.API_KEY", "correct-api-key")
    def test_missing_api_key(self, client):
        """缺少 API Key 应返回 401"""
        # 注意：需要重新加载模块才能生效，这里用 patch 装饰器
        # 实际测试中可能需要更复杂的设置
        pass  # 这个测试需要特殊配置，标记为占位

    def test_health_no_auth_required(self, client):
        """健康检查不需要认证"""
        response = client.get("/health")
        assert response.status_code == 200


# ============================================================
# Input Validation
# ============================================================

class TestInputValidation:
    """输入验证测试"""

    @patch("src.api.routes.get_storage")
    @patch("src.graph.workflow.run_customer_service")
    def test_empty_message(
        self, mock_workflow, mock_storage, client, auth_headers, mock_workflow_result
    ):
        """空消息应该也能处理（由业务逻辑决定如何响应）"""
        mock_workflow.return_value = mock_workflow_result

        mock_store = AsyncMock()
        mock_store.get_history.return_value = []
        mock_store.get_session.return_value = None
        mock_store.save_message.return_value = None
        mock_store.update_session.return_value = None
        mock_storage.return_value = mock_store

        response = client.post(
            "/chat/sess_test_001",
            json={"message": ""},
            headers=auth_headers,
        )

        # 空消息不应该导致 500 错误
        assert response.status_code in [200, 400, 422]

    def test_invalid_json(self, client, auth_headers):
        """无效 JSON 应返回 422"""
        response = client.post(
            "/chat/sess_test_001",
            content="not a json",
            headers={**auth_headers, "Content-Type": "application/json"},
        )

        assert response.status_code == 422
