"""
端到端多 Agent 对话评估 — LLM-as-Judge

流程:
1. 加载 e2e_scenarios.json 场景集（每个场景 = 多轮对话 + 期望行为）
2. 用 run_customer_service 逐轮驱动完整工作流（Triage→Specialist→工具→Supervisor）
3. 记录每轮的执行轨迹（路由/工具/回复/升级决策）
4. 用强模型 Judge 对完整轨迹打分（5 维度）
5. 汇总指标、输出报告

用法:
    python -m tests.evals.eval_end_to_end --save
    python -m tests.evals.eval_end_to_end --limit 5
    python -m tests.evals.eval_end_to_end --concurrency 3
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

# 确保项目根目录在 path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ============================================================
# 场景加载
# ============================================================

def load_scenarios(limit: int = None) -> list[dict]:
    data_path = Path(__file__).parent / "e2e_scenarios.json"
    with open(data_path, "r", encoding="utf-8") as f:
        scenarios = json.load(f)
    if limit:
        scenarios = scenarios[:limit]
    return scenarios


# ============================================================
# 执行一条场景（驱动完整工作流）
# ============================================================

async def _extract_trajectory(final_state: dict) -> dict:
    """从最终 state 提取用于评估的关键轨迹"""
    triage = final_state.get("triage_result") or {}
    routing = final_state.get("routing_decision") or {}
    specialist = final_state.get("specialist_response") or {}
    supervisor = final_state.get("supervisor_decision") or {}
    resolution = final_state.get("resolution") or {}

    return {
        "status": final_state.get("status"),
        "active_agent": final_state.get("active_agent"),
        "triage": {
            "intent": (triage.get("primary_intent") or ""),
            "sentiment": (triage.get("sentiment") or ""),
            "urgency": (triage.get("urgency") or ""),
            "recommended_agent": (triage.get("recommended_agent") or ""),
        },
        "routing_target": routing.get("target_node"),
        "specialist": {
            "agent": final_state.get("specialist_agent"),
            "resolved": specialist.get("is_resolved"),
            "needs_escalation": specialist.get("needs_escalation"),
            "tools_to_use": specialist.get("tools_to_use") or [],
            "diagnosis": specialist.get("diagnosis", ""),
        },
        "supervisor_decision": {
            "action": supervisor.get("action"),
            "reasoning": supervisor.get("reasoning", ""),
            "require_human_review": supervisor.get("require_human_review"),
        },
        "tool_results": [
            {
                "tool": r.get("tool"),
                "ok": "result" in r and not r.get("error"),
                "error": r.get("error", ""),
                "result_summary": str(r.get("result", ""))[:200],
            }
            for r in final_state.get("tool_results", [])
        ],
        "final_reply": (final_state.get("final_reply") or "")[:500],
        "resolution_type": resolution.get("resolution_type"),
        "errors": final_state.get("errors", []),
    }


async def execute_scenario(scenario: dict, graph) -> dict:
    """执行一条场景的全部对话轮次，返回完整轨迹"""
    from src.graph.workflow import run_customer_service

    customer_id = scenario.get("customer_id", "")
    history: list[dict] = []
    turns = []

    for i, msg in enumerate(scenario.get("conversation", [])):
        final_state = await run_customer_service(
            session_id=f"e2e_{scenario['id']}",
            user_message=msg["message"],
            customer_id=customer_id,
            history_messages=history,
            graph=graph,
            thread_id=f"e2e_{scenario['id']}_turn{i}",
        )

        # 记录本轮轨迹
        traj = await _extract_trajectory(final_state)
        traj["user_message"] = msg["message"]
        turns.append(traj)

        # 追加到历史（用于下一轮）
        history.append({"role": "user", "content": msg["message"]})
        if final_state.get("final_reply"):
            history.append({"role": "assistant", "content": final_state["final_reply"]})

    return {"turns": turns, "final_reply": turns[-1]["final_reply"] if turns else ""}


# ============================================================
# 评估主流程
# ============================================================

async def run_evaluation(
    scenarios: list[dict],
    concurrency: int = 3,
    judge_model: str = "st/deepseek-v4-pro",
) -> list[dict]:
    from src.api.deps import get_graph
    from tests.evals.judge import LLMJudge

    # 初始化 DB + seed 演示数据（确保工具读真实 DB，而非降级内存）
    try:
        from src.memory.database import init_db
        await init_db()
    except Exception as e:
        print(f"[WARN] DB 初始化失败: {e}")

    graph = await get_graph()
    judge = LLMJudge(model=judge_model)
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_one(i: int, scenario: dict) -> dict:
        async with semaphore:
            print(f"[{i+1}/{len(scenarios)}] 场景: {scenario['scenario']}")
            try:
                trajectory = await execute_scenario(scenario, graph)
                judge_result = await judge.evaluate(scenario, trajectory)

                overall = judge_result.overall
                print(
                    f"  得分: {overall:.1f} | 路由预期: {scenario['expectations'].get('expected_route')} "
                    f"→ 实际: {trajectory['turns'][-1]['routing_target']}"
                )
                return {
                    "id": scenario["id"],
                    "scenario": scenario["scenario"],
                    "expectations": scenario["expectations"],
                    "trajectory": trajectory,
                    "judge": judge_result.model_dump(),
                    "error": None,
                }
            except Exception as e:
                print(f"  [X] 执行失败: {e}")
                return {
                    "id": scenario["id"],
                    "scenario": scenario["scenario"],
                    "expectations": scenario["expectations"],
                    "trajectory": {},
                    "judge": None,
                    "error": str(e),
                }

    # 并发执行，每个场景完成后立即增量落盘（中断不丢进度）
    results: list[dict] = []
    pending = [asyncio.create_task(_run_one(i, s)) for i, s in enumerate(scenarios)]
    for task in asyncio.as_completed(pending):
        result = await task
        results.append(result)
        _append_incremental([result])

    # 恢复原始顺序
    results.sort(key=lambda r: r["id"])
    return list(results)


def _append_incremental(results: list[dict]) -> None:
    """将已完成的场景结果增量写入 running 文件（JSONL，每条一行）"""
    report_dir = Path(__file__).parent / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "e2e_running.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


# ============================================================
# 指标汇总
# ============================================================

def compute_metrics(results: list[dict]) -> dict:
    valid = [r for r in results if r["judge"] is not None]
    failed = [r for r in results if r["error"]]

    if not valid:
        return {"error": "所有场景都失败了", "valid": 0, "failed": len(failed)}

    # 平均分
    avg_overall = sum(r["judge"]["overall"] for r in valid) / len(valid)

    # 各维度平均
    dims = ["route_correctness", "tool_usage", "reply_quality", "compliance", "tone"]
    dim_avg = {}
    for dim in dims:
        vals = [r["judge"]["scores"].get(dim, 0) for r in valid]
        dim_avg[dim] = round(sum(vals) / len(vals), 2)

    # 路由一致性：期望路由 vs 实际
    route_match = 0
    route_total = 0
    for r in valid:
        exp = r["expectations"].get("expected_route")
        actual = None
        for turn in reversed(r["trajectory"].get("turns", [])):
            if turn.get("routing_target"):
                actual = turn["routing_target"]
                break
        route_total += 1
        if exp and actual and exp == actual:
            route_match += 1

    route_accuracy = route_match / route_total if route_total else 0

    # 高分段/低分段
    good = sum(1 for r in valid if r["judge"]["overall"] >= 7)
    bad = sum(1 for r in valid if r["judge"]["overall"] < 5)

    return {
        "total": len(results),
        "valid": len(valid),
        "failed": len(failed),
        "avg_overall": round(avg_overall, 2),
        "dimension_avg": dim_avg,
        "route_accuracy": round(route_accuracy, 4),
        "good_count": good,
        "bad_count": bad,
    }


# ============================================================
# 报告输出
# ============================================================

def print_report(metrics: dict):
    print("\n" + "=" * 60)
    print("端到端多 Agent 对话评估报告")
    print("=" * 60)

    if "error" in metrics:
        print(f"\n[FAIL] {metrics['error']}")
        return

    print(f"\n总场景数: {metrics['total']}")
    print(f"有效场景: {metrics['valid']}")
    print(f"失败场景: {metrics['failed']}")

    print(f"\n【核心指标】")
    print(f"  综合评分:      {metrics['avg_overall']:.2f} / 10")
    print(f"  路由一致性:    {metrics['route_accuracy']:.2%}")
    print(f"  优秀场景(≥7):  {metrics['good_count']} 个")
    print(f"  不合格(<5):    {metrics['bad_count']} 个")

    print(f"\n【各维度平均分】")
    for dim, score in metrics["dimension_avg"].items():
        bar = "#" * int(score) + "." * (10 - int(score))
        print(f"  {dim:20s} {bar} {score:.2f}")


def save_report(metrics: dict, results: list[dict], output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"e2e_report_{timestamp}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": timestamp,
            "metrics": metrics,
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n详细报告已保存: {path}")


# ============================================================
# 主函数
# ============================================================

async def main():
    parser = argparse.ArgumentParser(description="端到端多 Agent 对话评估")
    parser.add_argument("--limit", type=int, help="限制场景数量")
    parser.add_argument("--save", action="store_true", help="保存报告到文件")
    parser.add_argument("--concurrency", type=int, default=3, help="并发场景数")
    parser.add_argument("--judge-model", default="st/deepseek-v4-pro", help="Judge 模型")
    args = parser.parse_args()

    print("加载端到端场景集...")
    scenarios = load_scenarios(limit=args.limit)
    print(f"共 {len(scenarios)} 个场景")
    print(f"Judge 模型: {args.judge_model}")

    print("\n开始评估...")
    results = await run_evaluation(
        scenarios,
        concurrency=args.concurrency,
        judge_model=args.judge_model,
    )

    print("\n汇总指标...")
    metrics = compute_metrics(results)
    print_report(metrics)

    if args.save:
        save_report(metrics, list(results), Path(__file__).parent / "reports")


if __name__ == "__main__":
    asyncio.run(main())
