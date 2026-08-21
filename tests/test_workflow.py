"""
集成测试 — 测试完整的工作流

测试多轮对话、升级流程、路由逻辑等端到端场景。
"""
import pytest

from src.models.conversation import ConversationStatus
from src.models.routing import IntentType, Sentiment, Urgency, TriageResult, RoutingDecision
from src.state import create_initial_state
from src.graph.routing import (
    route_after_triage,
    route_after_specialist,
    route_after_supervisor,
)


# ============================================================
# State 测试
# ============================================================

class TestState:
    """测试状态创建和管理"""

    def test_create_initial_state(self):
        state = create_initial_state(
            session_id="sess_test_001",
            customer_id="cust_001",
        )
        assert state["session_id"] == "sess_test_001"
        assert state["customer_id"] == "cust_001"
        assert state["status"] == ConversationStatus.ACTIVE.value
        assert state["current_tier"] == "triage"
        assert state["messages"] == []
        assert state["escalation_count"] == 0
        assert state["errors"] == []

    def test_initial_state_defaults(self):
        state = create_initial_state(session_id="sess_test_002")
        assert state["customer_id"] is None
        assert state["max_escalation_rounds"] == 2


# ============================================================
# Routing 测试
# ============================================================

class TestRouting:
    """测试路由决策逻辑"""

    def test_route_faq(self):
        """FAQ 意图 → faq_answer"""
        state = {
            "triage_result": {
                "primary_intent": IntentType.FAQ.value,
                "intent_confidence": 0.9,
                "recommended_agent": "faq_answer",
                "requires_immediate_human": False,
            },
        }
        result = route_after_triage(state)
        assert result == "faq_answer"

    def test_route_technical(self):
        """技术支持 → technical"""
        state = {
            "triage_result": {
                "primary_intent": IntentType.TECHNICAL_SUPPORT.value,
                "intent_confidence": 0.85,
                "recommended_agent": "technical",
                "requires_immediate_human": False,
            },
        }
        result = route_after_triage(state)
        assert result == "technical"

    def test_route_billing(self):
        """账单 → billing"""
        state = {
            "triage_result": {
                "primary_intent": IntentType.BILLING_ACCOUNT.value,
                "intent_confidence": 0.9,
                "recommended_agent": "billing",
                "requires_immediate_human": False,
            },
        }
        result = route_after_triage(state)
        assert result == "billing"

    def test_route_complaint(self):
        """投诉 → complaint"""
        state = {
            "triage_result": {
                "primary_intent": IntentType.COMPLAINT.value,
                "intent_confidence": 0.95,
                "recommended_agent": "complaint",
                "requires_immediate_human": False,
            },
        }
        result = route_after_triage(state)
        assert result == "complaint"

    def test_route_immediate_human(self):
        """立即人工 → human_handoff"""
        state = {
            "triage_result": {
                "primary_intent": IntentType.COMPLAINT.value,
                "requires_immediate_human": True,
                "recommended_agent": "complaint",
            },
        }
        result = route_after_triage(state)
        assert result == "human_handoff"

    def test_route_low_confidence_faq(self):
        """低置信度 FAQ → 兜底到 technical"""
        state = {
            "triage_result": {
                "primary_intent": IntentType.FAQ.value,
                "intent_confidence": 0.3,
                "recommended_agent": "faq_answer",
                "requires_immediate_human": False,
            },
        }
        result = route_after_triage(state)
        assert result == "technical"

    def test_route_triage_none(self):
        """Triage 失败 → 直接结束（不浪费 LLM 调用）"""
        state = {"triage_result": None}
        result = route_after_triage(state)
        assert result == "__end__"


class TestSpecialistRouting:
    """测试 Specialist 后的路由"""

    def test_resolved(self):
        """已解决 → END"""
        state = {
            "specialist_response": {
                "is_resolved": True,
                "needs_escalation": False,
            },
        }
        result = route_after_specialist(state)
        assert result == "__end__"

    def test_escalate(self):
        """需要升级 → supervisor"""
        state = {
            "specialist_response": {
                "is_resolved": False,
                "needs_escalation": True,
            },
            "escalation_count": 0,
            "max_escalation_rounds": 2,
        }
        result = route_after_specialist(state)
        assert result == "supervisor"

    def test_escalate_max_rounds(self):
        """超过最大升级轮次 → human_handoff"""
        state = {
            "specialist_response": {
                "is_resolved": False,
                "needs_escalation": True,
            },
            "escalation_count": 2,
            "max_escalation_rounds": 2,
        }
        result = route_after_specialist(state)
        assert result == "human_handoff"

    def test_no_response(self):
        """无 specialist response → END"""
        state = {"specialist_response": None}
        result = route_after_specialist(state)
        assert result == "__end__"


class TestSupervisorRouting:
    """测试 Supervisor 后的路由"""

    def test_resolve(self):
        """主管解决 → END"""
        state = {
            "supervisor_decision": {
                "action": "resolve",
            },
        }
        result = route_after_supervisor(state)
        assert result == "__end__"

    def test_escalate_to_human(self):
        """主管决定转人工 → human_handoff"""
        state = {
            "supervisor_decision": {
                "action": "escalate_to_human",
            },
        }
        result = route_after_supervisor(state)
        assert result == "human_handoff"

    def test_coordinate(self):
        """跨域协调 → 回到 specialist"""
        state = {
            "supervisor_decision": {
                "action": "coordinate",
            },
            "specialist_agent": "billing",
        }
        result = route_after_supervisor(state)
        assert result == "billing"

    def test_no_decision(self):
        """无 decision → END"""
        state = {"supervisor_decision": None}
        result = route_after_supervisor(state)
        assert result == "__end__"


# ============================================================
# 模型测试
# ============================================================

class TestModels:
    """测试 Pydantic 模型序列化/反序列化"""

    def test_triage_result(self):
        result = TriageResult(
            primary_intent=IntentType.TECHNICAL_SUPPORT,
            secondary_intents=[IntentType.PRODUCT_INQUIRY],
            intent_confidence=0.85,
            sentiment=Sentiment.NEGATIVE,
            sentiment_detail="客户对产品故障感到沮丧",
            urgency=Urgency.HIGH,
            urgency_reason="影响正常使用",
            recommended_agent="technical",
            routing_reason="技术问题需要专业支持",
            summary="客户报告 App 闪退",
        )
        d = result.model_dump()
        assert d["primary_intent"] == "technical_support"
        assert d["sentiment"] == "negative"
        assert d["urgency"] == "high"

        # 反序列化
        restored = TriageResult(**d)
        assert restored.primary_intent == IntentType.TECHNICAL_SUPPORT

    def test_routing_decision(self):
        decision = RoutingDecision(
            target_node="technical",
            reason="intent=technical_support, confidence=0.85",
        )
        d = decision.model_dump()
        assert d["target_node"] == "technical"
        restored = RoutingDecision(**d)
        assert restored.target_node == "technical"

    def test_triage_result_serializable(self):
        """TriageResult 可以 JSON 序列化（兼容 LangGraph state）"""
        import json
        result = TriageResult(
            primary_intent=IntentType.FAQ,
            intent_confidence=0.92,
            sentiment=Sentiment.NEUTRAL,
            urgency=Urgency.LOW,
            recommended_agent="faq_answer",
        )
        json_str = json.dumps(result.model_dump(), ensure_ascii=False)
        assert "faq" in json_str


# ============================================================
# 工具测试
# ============================================================

class TestTools:
    """测试工具的注册和基本功能"""

    def test_tool_registry_register(self):
        from src.tools.registry import ToolRegistry
        from src.tools.crm import CRMLookupTool

        registry = ToolRegistry()
        tool = CRMLookupTool()
        registry.register(tool)

        assert "crm_lookup" in registry.list_all_tools()
        assert registry.tool_count == 1

    def test_tool_registry_bind(self):
        from src.tools.registry import ToolRegistry
        from src.tools.crm import CRMLookupTool
        from src.tools.order import OrderLookupTool

        registry = ToolRegistry()
        registry.register(CRMLookupTool())
        registry.register(OrderLookupTool())

        registry.bind_to_agent("billing", ["crm_lookup", "order_lookup"])
        tools = registry.get_tools_for_agent("billing")
        assert len(tools) == 2

    def test_tool_registry_all(self):
        from src.tools.registry import ToolRegistry
        from src.tools.crm import CRMLookupTool

        registry = ToolRegistry()
        registry.register(CRMLookupTool())
        registry.bind_to_agent("supervisor", ["all"])

        tools = registry.get_tools_for_agent("supervisor")
        assert len(tools) == 1

    def test_tool_to_openai_function(self):
        from src.tools.crm import CRMLookupTool

        tool = CRMLookupTool()
        schema = tool.to_openai_function()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "crm_lookup"

    @pytest.mark.asyncio
    async def test_crm_lookup_found(self):
        from src.tools.crm import CRMLookupTool

        tool = CRMLookupTool()
        result = await tool.execute(customer_id="cust_001")
        assert result["found"] is True
        assert result["customer"]["name"] == "张三"
        assert result["customer"]["tier"] == "vip"

    @pytest.mark.asyncio
    async def test_crm_lookup_not_found(self):
        from src.tools.crm import CRMLookupTool

        tool = CRMLookupTool()
        result = await tool.execute(customer_id="nonexistent")
        assert result["found"] is False

    @pytest.mark.asyncio
    async def test_order_lookup(self):
        from src.tools.order import OrderLookupTool

        tool = OrderLookupTool()
        result = await tool.execute(order_id="ord_001")
        assert result["found"] is True
        assert result["order"]["status"] == "shipped"

    @pytest.mark.asyncio
    async def test_order_lookup_by_customer(self):
        from src.tools.order import OrderLookupTool

        tool = OrderLookupTool()
        result = await tool.execute(customer_id="cust_001")
        assert result["found"] is True
        assert len(result["orders"]) >= 2

    @pytest.mark.asyncio
    async def test_human_handoff(self):
        from src.tools.human_handoff import HumanHandoffTool

        tool = HumanHandoffTool()
        result = await tool.execute(
            session_id="sess_test",
            reason="客户要求退款超出权限",
            summary="客户订单 ord_001 要求全额退款+赔偿",
        )
        assert result["handoff_requested"] is True
        assert "转接人工" in result["message"]


# ============================================================
# FAQ 知识库测试
# ============================================================

class TestKnowledgeBase:
    """测试知识库工具"""

    def test_load_default_faqs(self):
        from src.knowledge.loader import load_default_faqs

        faqs = load_default_faqs()
        assert len(faqs) >= 10
        # 验证结构
        for faq in faqs:
            assert "faq_id" in faq
            assert "question" in faq
            assert "answer" in faq
            assert "category" in faq

    @pytest.mark.asyncio
    async def test_knowledge_search_keyword(self):
        from src.tools.knowledge_search import KnowledgeSearchTool
        from src.knowledge.loader import load_default_faqs

        tool = KnowledgeSearchTool()
        tool.load_data(load_default_faqs())

        result = await tool.execute(query="退款")
        assert len(result["results"]) > 0
        # 退款相关应该在前面
        assert any("退款" in r.get("question", "") for r in result["results"])

    @pytest.mark.asyncio
    async def test_knowledge_search_category(self):
        from src.tools.knowledge_search import KnowledgeSearchTool
        from src.knowledge.loader import load_default_faqs

        tool = KnowledgeSearchTool()
        tool.load_data(load_default_faqs())

        result = await tool.execute(query="问题", category="technical")
        for r in result["results"]:
            assert r.get("category") == "technical"
