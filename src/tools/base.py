"""
工具基类 — 所有工具的抽象基类和装饰器

提供:
- 统一的工具接口（name, description, parameters, execute）
- @tool 装饰器简化工具定义
- JSON Schema 自动生成（用于 LLM Function Calling）
"""
import json
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional


class BaseTool(ABC):
    """
    工具抽象基类

    所有工具必须实现:
    - name: 工具唯一标识
    - description: 工具描述（给 LLM 看的）
    - parameters: 参数 JSON Schema
    - execute(): 执行逻辑
    """

    name: str = ""
    description: str = ""

    @abstractmethod
    async def execute(self, **kwargs) -> dict:
        """执行工具逻辑，返回结果 dict"""
        ...

    @property
    def parameters(self) -> dict:
        """获取参数的 JSON Schema"""
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    def to_openai_function(self) -> dict:
        """
        转为 OpenAI Function Calling 格式

        Returns:
            {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_prompt_description(self) -> str:
        """转为人类可读的工具描述（注入 system prompt 用）"""
        return f"- **{self.name}**: {self.description}"

    def __repr__(self) -> str:
        return f"Tool({self.name})"


# ============================================================
# @tool 装饰器 — 简化工具定义
# ============================================================

def tool(
    name: str,
    description: str,
    parameters: Optional[dict] = None,
):
    """
    装饰器：将异步函数转为 BaseTool 实例

    Usage:
        @tool(
            name="crm_lookup",
            description="查询客户信息",
            parameters={
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string", "description": "客户ID"}
                },
                "required": ["customer_id"],
            }
        )
        async def crm_lookup(customer_id: str) -> dict:
            return {"name": "张三", "tier": "vip"}
    """
    def decorator(func: Callable) -> BaseTool:
        class DecoratedTool(BaseTool):
            """动态生成的工具类"""
            _name = name
            _description = description
            _parameters = parameters or {
                "type": "object",
                "properties": {},
                "required": [],
            }

            @property
            def name(self) -> str:
                return self._name

            @property
            def description(self) -> str:
                return self._description

            @property
            def parameters(self) -> dict:
                return self._parameters

            async def execute(self, **kwargs) -> dict:
                return await func(**kwargs)

        tool_instance = DecoratedTool()
        return tool_instance

    return decorator
