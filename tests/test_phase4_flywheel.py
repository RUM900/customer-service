"""
Phase 4 运营飞轮测试 — 知识盲区闭环 / Copilot / 矛盾政策检测

覆盖:
- record_gap 记录盲区（内存兜底）
- get_gap_analysis 聚类生成建议 FAQ
- /admin/knowledge/copilot/assist 端点
- /admin/knowledge/conflict-check 端点
"""
import pytest
from unittest.mock import patch, AsyncMock


# ============================================================
# 知识盲区闭环
# ============================================================

class TestKnowledgeGap:
    @pytest.mark.asyncio
    async def test_record_and_cluster(self):
        """记录盲区 → 聚类分析应聚合相同 query（强制内存队列）"""
        from src.api.knowledge_gap import record_gap, get_gap_analysis, _gaps

        _gaps.clear()
        # 模拟 DB 不可用 → record_gap / get_gap_analysis 回落内存队列
        with patch(
            "src.memory.database.get_session_factory",
            side_effect=RuntimeError("db down"),
        ):
            try:
                await record_gap("sess_1", "退货地址在哪里？", "faq_answer", "未命中")
                await record_gap("sess_2", "退货地址在哪里？", "faq_answer", "未命中")
                await record_gap("sess_3", "怎么申请开发票？", "faq_answer", "未命中")

                suggestions = await get_gap_analysis(min_count=1)
                # 两条相同退货地址被聚合
                assert any("退货" in s["query"] and s["count"] >= 2 for s in suggestions)
                assert len(suggestions) >= 2
            finally:
                _gaps.clear()

    @pytest.mark.asyncio
    async def test_gap_analysis_min_count_filter(self):
        """min_count 过滤低频盲区"""
        from src.api.knowledge_gap import record_gap, get_gap_analysis, _gaps

        _gaps.clear()
        try:
            await record_gap("sess_1", "罕见问题A", "faq_answer", "")
            suggestions = await get_gap_analysis(min_count=2)
            assert suggestions == []
        finally:
            _gaps.clear()

    def test_normalize_query(self):
        """norm化：去掉语气词/标点后聚合"""
        from src.api.knowledge_gap import _normalize_query

        assert _normalize_query("请问退货地址是哪里？") == _normalize_query("退货地址是哪里")
        assert _normalize_query("您好，怎么退款？") == _normalize_query("怎么退款")


# ============================================================
# Copilot 端点
# ============================================================

class TestCopilot:
    @pytest.mark.asyncio
    async def test_copilot_assist_parses_json(self):
        """Copilot 应解析 LLM 返回的 JSON 并返回建议"""
        from src.api.admin_routes import CopilotRequest, copilot_assist

        req = CopilotRequest(
            session_id="sess_copilot_1",
            customer_message="我的订单还没到，很着急！",
        )

        fake_raw = (
            '{"suggested_reply": "别着急，我马上为您核实订单进度",'
            ' "context_summary": "客户订单延迟，情绪着急",'
            ' "suggested_tools": ["order_lookup"]}'
        )

        with patch(
            "src.agents.supervisor.SupervisorAgent.call_chat",
            new=AsyncMock(return_value=fake_raw),
        ):
            resp = await copilot_assist(req, _auth="admin_authenticated")

        assert resp.suggested_reply
        assert "订单" in resp.suggested_reply
        assert "order_lookup" in resp.suggested_tools
        assert resp.context_summary


# ============================================================
# 矛盾政策检测
# ============================================================

class TestConflictCheck:
    @pytest.mark.asyncio
    async def test_conflict_check_detects(self):
        """矛盾政策检测应返回冲突列表"""
        from src.api.admin_routes import conflict_check

        fake_raw = (
            '{"conflicts": [{"faq_question": "退货期是多久",'
            ' "conflict_detail": "新政策改为14天，但FAQ写7天"}]}'
        )

        # 注入现有 FAQ（不连真实 DB）
        class FakeFactory:
            def __call__(self):
                return self
            async def __aenter__(self):
                return None
            async def __aexit__(self, *a):
                return False

        from src.models.knowledge import FAQEntry
        sample_faq = FAQEntry(
            question="退货期是多久", answer="支持7天无理由退货", category="general",
        )

        with patch(
            "src.memory.database.get_session_factory", FakeFactory(),
        ), patch(
            "src.memory.faq_store.FaqStore.list_all",
            new=AsyncMock(return_value=[sample_faq]),
        ), patch(
            "src.agents.triage.TriageAgent.call_chat",
            new=AsyncMock(return_value=fake_raw),
        ):
            resp = await conflict_check(
                policy_text="自本通知发布之日起，支持14天无理由退货。",
                _auth="admin_authenticated",
            )

        assert resp["status"] == "ok"
        assert len(resp["conflicts"]) == 1