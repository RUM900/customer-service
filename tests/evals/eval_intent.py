"""
意图识别评估 — 测量 Triage Agent 的意图分类准确率

运行方式:
    # 快速测试（前 10 条）
    python -m tests.evals.eval_intent --limit 10

    # 完整评估（需要真实 LLM 调用）
    python -m tests.evals.eval_intent

    # 只看统计不调用 LLM（用于验证脚本）
    python -m tests.evals.eval_intent --dry-run

输出:
    - 意图识别准确率
    - 路由准确率
    - 混淆矩阵
    - 分类别准确率
"""
import asyncio
import json
import argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime


# ============================================================
# 数据加载
# ============================================================

def load_test_samples(limit: int = None) -> list[dict]:
    """加载测试样本"""
    data_path = Path(__file__).parent / "test_data" / "intent_samples.json"
    with open(data_path, "r", encoding="utf-8") as f:
        samples = json.load(f)

    if limit:
        samples = samples[:limit]

    return samples


# ============================================================
# 评估逻辑
# ============================================================

async def evaluate_single(sample: dict, triage_agent) -> dict:
    """评估单个样本"""
    try:
        result = await triage_agent.triage(
            user_message=sample["message"],
            history=None,
        )

        # 提取预测结果
        predicted_intent = result.primary_intent.value
        predicted_agent = result.recommended_agent
        confidence = result.intent_confidence

        return {
            "id": sample["id"],
            "message": sample["message"],
            "expected_intent": sample["expected_intent"],
            "predicted_intent": predicted_intent,
            "expected_agent": sample["expected_agent"],
            "predicted_agent": predicted_agent,
            "confidence": confidence,
            "intent_correct": predicted_intent == sample["expected_intent"],
            "agent_correct": predicted_agent == sample["expected_agent"],
            "error": None,
        }
    except Exception as e:
        return {
            "id": sample["id"],
            "message": sample["message"],
            "expected_intent": sample["expected_intent"],
            "predicted_intent": None,
            "expected_agent": sample["expected_agent"],
            "predicted_agent": None,
            "confidence": None,
            "intent_correct": False,
            "agent_correct": False,
            "error": str(e),
        }


async def run_evaluation(samples: list[dict], dry_run: bool = False) -> list[dict]:
    """运行完整评估"""
    if dry_run:
        # Dry run 模式：返回模拟结果
        print("[Dry Run] 跳过 LLM 调用，返回模拟数据")
        return [
            {
                "id": s["id"],
                "message": s["message"],
                "expected_intent": s["expected_intent"],
                "predicted_intent": s["expected_intent"],  # 假设全对
                "expected_agent": s["expected_agent"],
                "predicted_agent": s["expected_agent"],
                "confidence": 0.9,
                "intent_correct": True,
                "agent_correct": True,
                "error": None,
            }
            for s in samples
        ]

    # 真实评估
    from src.agents.triage import TriageAgent

    triage_agent = TriageAgent()
    results = []

    for i, sample in enumerate(samples):
        print(f"[{i+1}/{len(samples)}] 评估: {sample['message'][:30]}...")
        result = await evaluate_single(sample, triage_agent)
        results.append(result)

        # 打印即时结果
        status = "✓" if result["intent_correct"] else "✗"
        print(f"  {status} 预测: {result['predicted_intent']} (期望: {result['expected_intent']})")

        # 避免 API 限流
        await asyncio.sleep(0.5)

    return results


# ============================================================
# 统计分析
# ============================================================

def compute_metrics(results: list[dict]) -> dict:
    """计算评估指标"""
    total = len(results)
    errors = sum(1 for r in results if r["error"])
    valid = total - errors

    if valid == 0:
        return {"error": "所有样本都失败了"}

    # 准确率
    intent_correct = sum(1 for r in results if r["intent_correct"])
    agent_correct = sum(1 for r in results if r["agent_correct"])

    intent_accuracy = intent_correct / valid
    agent_accuracy = agent_correct / valid

    # 分类别统计
    category_stats = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in results:
        if r["error"]:
            continue
        cat = r["expected_intent"]
        category_stats[cat]["total"] += 1
        if r["intent_correct"]:
            category_stats[cat]["correct"] += 1

    category_accuracy = {
        cat: stats["correct"] / stats["total"] if stats["total"] > 0 else 0
        for cat, stats in category_stats.items()
    }

    # 混淆矩阵（简化版：只统计错误分类）
    confusion = defaultdict(lambda: defaultdict(int))
    for r in results:
        if r["error"]:
            continue
        expected = r["expected_intent"]
        predicted = r["predicted_intent"]
        confusion[expected][predicted] += 1

    # 平均置信度
    confidences = [r["confidence"] for r in results if r["confidence"] is not None]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0

    # 错误样本
    wrong_samples = [
        {
            "id": r["id"],
            "message": r["message"],
            "expected": r["expected_intent"],
            "predicted": r["predicted_intent"],
            "confidence": r["confidence"],
        }
        for r in results
        if not r["intent_correct"] and not r["error"]
    ]

    return {
        "total_samples": total,
        "valid_samples": valid,
        "errors": errors,
        "intent_accuracy": intent_accuracy,
        "agent_accuracy": agent_accuracy,
        "avg_confidence": avg_confidence,
        "category_accuracy": dict(category_accuracy),
        "confusion_matrix": {k: dict(v) for k, v in confusion.items()},
        "wrong_samples": wrong_samples,
    }


def print_report(metrics: dict):
    """打印评估报告"""
    print("\n" + "=" * 60)
    print("意图识别评估报告")
    print("=" * 60)

    print(f"\n总样本数: {metrics['total_samples']}")
    print(f"有效样本: {metrics['valid_samples']}")
    print(f"失败样本: {metrics['errors']}")

    print(f"\n【核心指标】")
    print(f"  意图识别准确率: {metrics['intent_accuracy']:.2%}")
    print(f"  路由准确率:     {metrics['agent_accuracy']:.2%}")
    print(f"  平均置信度:     {metrics['avg_confidence']:.2f}")

    print(f"\n【分类别准确率】")
    for cat, acc in sorted(metrics["category_accuracy"].items()):
        bar = "█" * int(acc * 20) + "░" * (20 - int(acc * 20))
        print(f"  {cat:20s} {bar} {acc:.2%}")

    if metrics["wrong_samples"]:
        print(f"\n【错误样本】({len(metrics['wrong_samples'])} 个)")
        for w in metrics["wrong_samples"][:10]:  # 最多显示 10 个
            print(f"  [{w['id']}] \"{w['message'][:30]}...\"")
            print(f"       期望: {w['expected']}, 预测: {w['predicted']} (置信度: {w['confidence']:.2f})")

    print("\n" + "=" * 60)


def save_report(metrics: dict, results: list[dict], output_dir: Path):
    """保存评估报告到文件"""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 保存详细结果
    results_path = output_dir / f"eval_results_{timestamp}.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": timestamp,
            "metrics": metrics,
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n详细报告已保存: {results_path}")


# ============================================================
# 主函数
# ============================================================

async def main():
    parser = argparse.ArgumentParser(description="意图识别评估")
    parser.add_argument("--limit", type=int, help="限制评估样本数量")
    parser.add_argument("--dry-run", action="store_true", help="不调用 LLM，只验证脚本")
    parser.add_argument("--save", action="store_true", help="保存评估报告到文件")
    args = parser.parse_args()

    print("加载测试样本...")
    samples = load_test_samples(limit=args.limit)
    print(f"共 {len(samples)} 个样本")

    print("\n开始评估...")
    results = await run_evaluation(samples, dry_run=args.dry_run)

    print("\n计算指标...")
    metrics = compute_metrics(results)

    print_report(metrics)

    if args.save:
        output_dir = Path(__file__).parent / "reports"
        save_report(metrics, results, output_dir)


if __name__ == "__main__":
    asyncio.run(main())
