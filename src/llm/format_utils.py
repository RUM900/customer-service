"""
LLM 格式工具 — 避免循环导入

从 base.py 分离出来，供所有 Provider 使用。
"""
from typing import Any


def extract_json(raw: str) -> str:
    """从 LLM 输出中提取 JSON 文本（去除 markdown 代码围栏与多余空白）"""
    if not raw:
        return raw
    text = raw.strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 或 ``` ... ```
        if "\n" in text:
            text = text.split("\n", 1)[-1]
        else:
            text = text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    return text


def inject_format_guide(messages: list[dict], schema: dict) -> None:
    """
    将 JSON Schema 格式指南注入 system message

    供所有 Provider 的结构化输出方法使用。
    在 system message 末尾追加目标 JSON 格式说明。
    如果没有 system message，则在开头插入一条。

    Args:
        messages: 消息列表（原地修改）
        schema: JSON Schema dict
    """
    fields_desc = []
    for name, prop in schema.get("properties", {}).items():
        ptype = prop.get("type", "string")
        desc = prop.get("description", "")
        if "enum" in prop:
            options = " | ".join(f'"{v}"' for v in prop["enum"])
            fields_desc.append(f'  "{name}": {options}  // {desc}')
        elif ptype == "array":
            items = prop.get("items", {}).get("type", "string")
            fields_desc.append(f'  "{name}": [{"... (string)" if items == "string" else "{...}"}]  // {desc}')
        elif ptype in ("integer", "number"):
            fields_desc.append(f'  "{name}": 0  // {desc}')
        elif ptype == "boolean":
            fields_desc.append(f'  "{name}": true  // {desc}')
        else:
            fields_desc.append(f'  "{name}": "..."  // {desc}')

    format_text = "{\n" + ",\n".join(fields_desc) + "\n}"

    for msg in messages:
        if msg.get("role") == "system":
            msg["content"] = (
                f"{msg['content']}\n\n"
                f"你必须只返回纯 JSON，格式如下：\n{format_text}\n"
                f"注意：只返回 JSON 本身，不要包含 ``` 标记或任何解释文字。"
            )
            return

    # 没有 system message => 在开头插入一条
    messages.insert(0, {
        "role": "system",
        "content": (
            f"你必须只返回纯 JSON，格式如下：\n{format_text}\n"
            f"注意：只返回 JSON 本身，不要包含 ``` 标记或任何解释文字。"
        ),
    })
