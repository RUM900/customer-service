"""
LangSmith 接入验证脚本

运行后会在 LangSmith 项目 customer-service 下产生一条 trace。
去 https://smith.langchain.com 查看是否上报成功。

用法:
    python tests/verify_langsmith.py
"""
import asyncio
import os
import sys
from pathlib import Path

# 确保项目根目录在 path
sys.path.insert(0, str(Path(__file__).parent.parent))

# 加载 .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import config


async def main():
    print("=" * 60)
    print("LangSmith 接入验证")
    print("=" * 60)
    print(f"LANGSMITH_TRACING = {config.LANGSMITH_TRACING}")
    print(f"LANGSMITH_PROJECT = {config.LANGSMITH_PROJECT}")
    print(f"LANGSMITH_API_KEY  = {config.LANGSMITH_API_KEY[:20]}...")
    print(f"LANGSMITH_ENDPOINT = {config.LANGSMITH_ENDPOINT}")
    print(f"LLM_PROVIDER       = {config.LLM_PROVIDER}")
    print(f"MODEL_TRIAGE       = {config.MODEL_TRIAGE}")
    print()

    if not config.LANGSMITH_TRACING or not config.LANGSMITH_API_KEY:
        print("[FAIL] LangSmith 未启用或缺少 API Key，请检查 .env")
        return

    if not config.DASHSCOPE_API_KEY and not config.OPENAI_API_KEY:
        print("[FAIL] 缺少 LLM API Key，无法跑真实对话")
        return

    # 把 LangSmith 配置写入进程环境变量（LangGraph 自动检测这些）
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = config.LANGSMITH_API_KEY
    os.environ["LANGSMITH_PROJECT"] = config.LANGSMITH_PROJECT
    os.environ["LANGSMITH_ENDPOINT"] = config.LANGSMITH_ENDPOINT

    # 验证 LangSmith client 能连通（项目不存在是正常的，首次上报会自动创建）
    try:
        from langsmith import Client
        client = Client()
        client.read_project(project_name=config.LANGSMITH_PROJECT)
        print("[OK] LangSmith 客户端连通，项目已存在")
    except Exception as e:
        print(f"[INFO] 项目尚未创建（首次上报会自动建）: {type(e).__name__}")

    # 跑一次真实图执行，看 trace 是否上报
    print("\n开始跑一次真实对话（会真实调用 LLM）...")
    from src.graph.workflow import build_customer_service_graph
    graph = await build_customer_service_graph()

    initial_state = {
        "session_id": "verify_langsmith",
        "customer_id": "cust_001",
        "user_message": "我的订单还没收到，已经等了5天了！",
        "messages": [],
    }
    thread_config = {"configurable": {"thread_id": "verify_langsmith_run1"}}

    final_state = await graph.ainvoke(initial_state, thread_config)

    reply = final_state.get("final_reply", "")
    agent = final_state.get("active_agent", "")
    print(f"\n[OK] 对话完成")
    print(f"   agent: {agent}")
    print(f"   reply: {reply[:100]}...")

    # 检查本地埋点是否也有记录（第一步的 telemetry）
    from src.llm.telemetry import get_recent_calls
    calls = get_recent_calls(20)
    print(f"\n本地埋点记录: {len(calls)} 条 LLM 调用")
    for c in calls:
        print(f"   [{c['agent_name']}] {c['call_type']} "
              f"{c['model']} tokens={c['total_tokens']} "
              f"latency={c['latency_ms']}ms success={c['success']}")

    print("\n" + "=" * 60)
    print("[OK] 现在去 https://smith.langchain.com 查看 trace")
    print(f"   项目名: {config.LANGSMITH_PROJECT}")
    print("   应该能看到这次对话的完整图执行 trace")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
