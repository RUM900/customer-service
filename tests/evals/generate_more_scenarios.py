"""
评估场景生成器 — 扩充端到端评估集

用法（在项目根目录）:
    python tests/evals/generate_more_scenarios.py

将追加生成的场景写入 e2e_scenarios.json（幂等：跳过已存在的 id）。
目标：把评估集从基础的 20 条扩充到 120+ 条，覆盖更广的业务变体。
"""
import json
import itertools
from pathlib import Path

DATA_PATH = Path(__file__).parent / "e2e_scenarios.json"


def _scenario(sc_id, scenario, customer_id, message, route, tools=None,
              escalate=False, tone="clear", forbidden=None, extra=None):
    expectations = {
        "expected_route": route,
        "should_use_tools": tools or [],
        "should_escalate": escalate,
        "expected_tone": tone,
        "forbidden": forbidden or [],
    }
    if extra:
        expectations.update(extra)
    return {
        "id": sc_id,
        "scenario": scenario,
        "customer_id": customer_id,
        "conversation": [{"role": "user", "message": message}],
        "expectations": expectations,
    }


def generate():
    scenarios = []

    # ---------- FAQ 路由变体 ----------
    faq_q = [
        ("怎么改收货地址？", "faq_021"),
        ("退货要自己出运费吗？", "faq_022"),
        ("发票抬头可以改吗？", "faq_023"),
        ("你们周末发货吗？", "faq_024"),
        ("支持货到付款吗？", "faq_025"),
        ("优惠券可以叠加使用？", "faq_026"),
        ("破损件怎么申请补发？", "faq_027"),
    ]
    for i, (q, sc_id) in enumerate(faq_q):
        scenarios.append(_scenario(
            sc_id, f"FAQ 变体 {i + 1}", f"cust_00{3 + i % 5}", q, "faq_answer",
        ))
        scenarios.append(_scenario(
            f"{sc_id}b", f"FAQ 变体 {i + 1}（加礼貌用语）", f"cust_00{4 + i % 5}",
            f"您好，{q}", "faq_answer",
        ))

    # ---------- Technical 路由变体 ----------
    tech_q = [
        ("App 一直闪退怎么办？", "tech_031"),
        ("设备连不上 WiFi", "tech_032"),
        ("软件更新后卡在登录页", "tech_033"),
        ("蓝牙配对失败", "tech_034"),
        ("界面显示乱码", "tech_035"),
        ("摄像头无法调用", "tech_036"),
    ]
    for i, (q, sc_id) in enumerate(tech_q):
        scenarios.append(_scenario(
            sc_id, f"技术问题变体 {i + 1}", f"cust_00{5 + i % 5}", q, "technical",
            tools=["knowledge_search"],
        ))

    # ---------- Billing 路由变体 ----------
    bill_q = [
        ("我的账户余额怎么变负数了？", "bill_041", "crm_lookup"),
        ("怎么解绑自动扣款？", "bill_042", "crm_lookup"),
        ("上月账单多扣了一笔", "bill_043", "order_lookup"),
        ("积分为什么一直没到账？", "bill_044", "crm_lookup"),
        ("会员续费价格不对", "bill_045", "crm_lookup"),
    ]
    for i, (q, sc_id, tool) in enumerate(bill_q):
        scenarios.append(_scenario(
            sc_id, f"账务问题变体 {i + 1}", f"cust_00{6 + i % 5}", q, "billing",
            tools=[tool],
        ))

    # ---------- Product 路由变体 ----------
    prod_q = [
        ("A款和B款有什么区别？", "prod_051"),
        ("这款支持无线充电吗？", "prod_052"),
        ("套餐里都包含什么配件？", "prod_053"),
        ("新品什么时候发售？", "prod_054"),
    ]
    for i, (q, sc_id) in enumerate(prod_q):
        scenarios.append(_scenario(
            sc_id, f"产品咨询变体 {i + 1}", f"cust_00{7 + i % 5}", q, "product",
            tools=["knowledge_search"],
        ))

    # ---------- Complaint 路由变体 ----------
    comp_q = [
        ("客服电话打不通，太不像话了！", "comp_061", True),
        ("送来的东西是坏的，态度还差", "comp_062", True),
        ("等了10天没发货，我要投诉", "comp_063", True),
        ("包装破损严重，收货体验极差", "comp_064", True),
    ]
    for i, (q, sc_id, ang) in enumerate(comp_q):
        tone = "calm" if not ang else "soothe"
        scenarios.append(_scenario(
            sc_id, f"客诉变体 {i + 1}", f"cust_00{8 + i % 5}", q, "complaint",
            tools=["crm_lookup", "order_lookup"], tone=tone,
            extra={"should_block": False},
        ))

    # ---------- 转人工路由变体 ----------
    handoff_q = [
        ("我要找人工客服！", "hand_071"),
        ("你们系统搞不定，直接给我转人工", "hand_072"),
        ("别啰嗦了，让人工来处理", "hand_073"),
    ]
    for i, (q, sc_id) in enumerate(handoff_q):
        scenarios.append(_scenario(
            sc_id, f"转人工变体 {i + 1}", f"cust_00{9 + i % 5}", q, "human_handoff",
            tone="handoff",
        ))

    # ---------- 高危 HITL / 大额退款场景 ----------
    hitl_q = [
        ("要求全额退款600元，钱到现在没到账", "hitl_081", "billing"),
        ("账户被莫名扣了800，立即退回来", "hitl_082", "complaint"),
        ("退款900元，不然就曝光你们", "hitl_083", "complaint"),
        ("充值1000元不能用了，要求原路退还", "hitl_084", "billing"),
    ]
    for i, (q, sc_id, route) in enumerate(hitl_q):
        scenarios.append(_scenario(
            sc_id, f"HITL 大额退款场景 {i + 1}", f"cust_00{2 + i % 5}", q, route,
            tools=["crm_lookup", "order_lookup"], escalate=True, tone="soothe",
            extra={"require_human_review": True},
        ))

    # ---------- 第二轮变体（扩充覆盖面） ----------
    wave2_faq = [
        ("多久能收到发票？", "faq_w2_01"),
        ("可以自提吗？", "faq_w2_02"),
        ("保修需要发票吗？", "faq_w2_03"),
        ("预售商品什么时候发货？", "faq_w2_04"),
        ("赠品可以换别的吗？", "faq_w2_05"),
        ("怎么查询订单物流？", "faq_w2_06"),
        ("退款到账需要多久？", "faq_w2_07"),
    ]
    for i, (q, sc_id) in enumerate(wave2_faq):
        scenarios.append(_scenario(
            sc_id, f"FAQ 第二轮变体 {i + 1}", f"cust_00{3 + i % 5}", q, "faq_answer",
        ))

    wave2_tech = [
        ("更新后功能不见了", "tech_w2_01"),
        ("设备间歇性断连", "tech_w2_02"),
        ("扫码没反应", "tech_w2_03"),
        ("电池耗电异常快", "tech_w2_04"),
    ]
    for i, (q, sc_id) in enumerate(wave2_tech):
        scenarios.append(_scenario(
            sc_id, f"技术第二轮变体 {i + 1}", f"cust_00{5 + i % 5}", q, "technical",
            tools=["knowledge_search"],
        ))

    wave2_bill = [
        ("自动续费忘记关，扣了两期", "bill_w2_01", "order_lookup"),
        ("账单地址怎么修改？", "bill_w2_02", "crm_lookup"),
        ("历史账单可以导出吗？", "bill_w2_03", "crm_lookup"),
    ]
    for i, (q, sc_id, tool) in enumerate(wave2_bill):
        scenarios.append(_scenario(
            sc_id, f"账务第二轮变体 {i + 1}", f"cust_00{6 + i % 5}", q, "billing",
            tools=[tool],
        ))

    wave2_prod = [
        ("Pro 和标准版差别大吗？", "prod_w2_01"),
        ("有儿童模式吗？", "prod_w2_02"),
        ("外壳材质是什么？", "prod_w2_03"),
    ]
    for i, (q, sc_id) in enumerate(wave2_prod):
        scenarios.append(_scenario(
            sc_id, f"产品第二轮变体 {i + 1}", f"cust_00{7 + i % 5}", q, "product",
            tools=["knowledge_search"],
        ))

    wave2_comp = [
        ("赠品没发还态度敷衍，差评！", "comp_w2_01", True),
        ("说好的补偿一直没兑现", "comp_w2_02", True),
        ("客服来回踢皮球，气死了", "comp_w2_03", True),
    ]
    for i, (q, sc_id, ang) in enumerate(wave2_comp):
        scenarios.append(_scenario(
            sc_id, f"客诉第二轮变体 {i + 1}", f"cust_00{8 + i % 5}", q, "complaint",
            tools=["crm_lookup", "order_lookup"],
            tone="soothe" if ang else "calm",
        ))

    wave2_handoff = [
        ("这个问题你们处理不了，转人工吧", "hand_w2_01"),
        ("请尽快安排人工专员联系我", "hand_w2_02"),
    ]
    for i, (q, sc_id) in enumerate(wave2_handoff):
        scenarios.append(_scenario(
            sc_id, f"转人工第二轮变体 {i + 1}", f"cust_00{9 + i % 5}", q, "human_handoff",
            tone="handoff",
        ))

    # ---------- 多轮对话场景（更贴近真实 e2e） ----------
    multi_turn_scenarios = [
        {
            "id": "multi_091",
            "scenario": "多轮：询问发货后催物流",
            "customer_id": "cust_003",
            "conversation": [
                {"role": "user", "message": "我周二下的单什么时候发货？"},
                {"role": "user", "message": "刚查了昨天的日志，还没发货啊，帮我催一下！"},
            ],
            "expectations": {
                "expected_route": "technical",
                "should_use_tools": ["order_lookup"],
                "should_escalate": False,
                "expected_tone": "clear",
                "forbidden": [],
                "multi_turn": True,
            },
        },
        {
            "id": "multi_092",
            "scenario": "多轮：先问政策后要求补偿",
            "customer_id": "cust_005",
            "conversation": [
                {"role": "user", "message": "你们支持退货吗？"},
                {"role": "user", "message": "那我直接退了吧，运费你们得承担"},
            ],
            "expectations": {
                "expected_route": "faq_answer",
                "should_use_tools": [],
                "should_escalate": False,
                "expected_tone": "clear",
                "forbidden": [],
                "multi_turn": True,
            },
        },
        {
            "id": "multi_093",
            "scenario": "多轮：升级压缩后继续深挖",
            "customer_id": "cust_002",
            "conversation": [
                {"role": "user", "message": "App 无法登录，重置密码也没用"},
                {"role": "user", "message": "换手机和换网络都试过了，还是不行，帮我升级处理"},
            ],
            "expectations": {
                "expected_route": "technical",
                "should_use_tools": ["ticket_create"],
                "should_escalate": True,
                "expected_tone": "clear",
                "forbidden": [],
                "multi_turn": True,
            },
        },
    ]
    scenarios.extend(multi_turn_scenarios)

    return scenarios


def main():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        existing = json.load(f)

    existing_ids = {s["id"] for s in existing}
    new = [s for s in generate() if s["id"] not in existing_ids]

    if not new:
        print("无新场景需要追加（全部已存在）")
        return

    # 保持原有顺序 + 追加新场景
    merged = existing + new
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"评估集已扩充: {len(existing)} → {len(merged)} 条（新增 {len(new)} 条）")


if __name__ == "__main__":
    main()