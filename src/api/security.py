"""
输入安全防护

提供:
- 用户输入消毒（截断超长、移除控制字符）
- Prompt Injection 检测（模式匹配 + 启发式规则）
- 客户 ID 格式校验
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================
# 配置
# ============================================================

# 最大输入长度（字符数，超过则截断）
MAX_INPUT_LENGTH = 5000

# 可疑注入模式列表
_INJECTION_PATTERNS = [
    # 英文注入模式
    r"(?i)ignore\s+(all\s+)?(previous|above|the\s+above)\s+(instructions?|prompts?|directives?|context)",
    r"(?i)(you\s+are|act\s+as|pretend\s+to\s+be|roleplay\s+as)\s+(now|from\s+now\s+on)",
    r"(?i)(system\s+prompt|system\s+message|system\s+instruction)",
    r"(?i)(forget|disregard|override)\s+(everything|all)\s+(before|above|you\s+know)",
    r"(?i)(your\s+(new\s+)?instructions?\s+(are|is|:))",
    r"(?i)(DAN|do\s+anything\s+now|jailbreak)",
    r"(?i)(reveal\s+(your\s+)?(prompt|instructions?|system))",
    r"(?i)(you\s+must|you\s+will)\s+(obey|follow|comply)",
    r"(?i)respond\s+only\s+with",
    # 中文注入模式
    r"忽略(所有)?(之前的|上面的)(指令|提示|规则)",
    r"(你|你现在的?)(角色|身份)是",
    r"(系统提示词|系统指令|系统消息)",
    r"(忘记|无视|忽略)(之前|上面)的?(一切|所有)",
    r"你的?(新)?指令是?[：:]",
    r"(你必须|你一定要)(服从|听从|遵守)",
    r"(揭示|泄露|告诉我)(你的)?(提示词|指令|系统)",
    r"只(回复|回答|输出)",
]

# 编译正则表达式（提高性能）
_COMPILED_PATTERNS = [re.compile(p) for p in _INJECTION_PATTERNS]

# 控制字符正则（保留常用空白字符）
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')

# 客户 ID 格式: cust_ 开头 + 字母数字
_CUSTOMER_ID_RE = re.compile(r'^cust_[a-zA-Z0-9_-]{1,64}$')


# ============================================================
# 输入消毒
# ============================================================

def sanitize_user_input(text: str, max_length: int = MAX_INPUT_LENGTH) -> str:
    """
    消毒用户输入

    - 移除控制字符（保留 \n, \t, \r）
    - 截断超长输入
    - 去除首尾空白

    Args:
        text: 原始用户输入
        max_length: 最大允许长度

    Returns:
        消毒后的文本
    """
    if not text:
        return ""

    # 移除控制字符（保留换行、制表符、回车）
    text = _CONTROL_CHARS_RE.sub('', text)

    # 规范化 Unicode（防止同形异义词攻击）
    # NFC 规范化：将组合字符转为预组合形式
    try:
        import unicodedata
        text = unicodedata.normalize('NFC', text)
    except Exception:
        pass

    # 截断超长输入
    if len(text) > max_length:
        original_len = len(text)
        text = text[:max_length]
        logger.warning(f"输入被截断: {original_len} → {max_length} 字符")

    # 去除首尾空白
    text = text.strip()

    return text


# ============================================================
# Prompt Injection 检测
# ============================================================

def detect_prompt_injection(text: str) -> dict:
    """
    检测 Prompt Injection 尝试

    使用模式匹配检测常见的注入攻击。返回检测结果，
    但不阻断请求——只标记和记录。

    Args:
        text: 用户输入文本

    Returns:
        {
            "suspicious": bool,       # 是否可疑
            "matched_patterns": [...], # 匹配到的模式
            "risk_level": "low" | "medium" | "high",
        }
    """
    if not text:
        return {"suspicious": False, "matched_patterns": [], "risk_level": "low"}

    matched = []
    text_lower = text.lower()

    for i, pattern in enumerate(_COMPILED_PATTERNS):
        if pattern.search(text):
            matched.append(_INJECTION_PATTERNS[i])

    # 风险评估
    risk_level = "low"
    if len(matched) >= 3:
        risk_level = "high"
    elif len(matched) >= 1:
        risk_level = "medium"

    if matched:
        logger.warning(
            f"检测到疑似 Prompt Injection: "
            f"risk={risk_level}, patterns={matched}, "
            f"input_preview='{text[:100]}'"
        )

    return {
        "suspicious": len(matched) > 0,
        "matched_patterns": matched,
        "risk_level": risk_level,
    }


# ============================================================
# 客户 ID 校验
# ============================================================

def validate_customer_id(customer_id: str) -> bool:
    """
    校验客户 ID 格式

    格式: cust_ + 字母数字，1-64 字符
    例如: cust_001, cust_abc123

    Args:
        customer_id: 待校验的客户 ID

    Returns:
        是否合法
    """
    if not customer_id:
        return True  # 允许空值（匿名客户）

    return bool(_CUSTOMER_ID_RE.match(customer_id))


def sanitize_customer_id(customer_id: Optional[str]) -> Optional[str]:
    """
    消毒客户 ID

    - 去空白
    - 截断过长 ID
    - 格式校验

    Args:
        customer_id: 原始客户 ID

    Returns:
        消毒后的客户 ID，格式不合法返回 None
    """
    if not customer_id:
        return None

    customer_id = customer_id.strip()[:64]

    if validate_customer_id(customer_id):
        return customer_id

    logger.warning(f"非法客户 ID 格式: {customer_id[:20]}...")
    return None
