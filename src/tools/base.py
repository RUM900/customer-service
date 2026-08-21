"""
工具基类 — 所有工具的抽象基类

提供:
- 统一的工具接口（name, description, parameters, execute）
- JSON Schema 自动生成（用于 LLM Function Calling）
"""
from abc import ABC, abstractmethod


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
