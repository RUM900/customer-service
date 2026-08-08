"""
LLM 格式工具 — 避免循环导入

从 base.py 分离出来，供所有 Provider 使用。
"""
from typing import Any


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
