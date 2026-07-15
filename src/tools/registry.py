"""
工具注册中心 — 管理所有工具的注册、发现和 Agent 绑定

核心设计:
- 统一注册: 所有工具在系统启动时注册到 Registry
- Agent 绑定: 每个 Agent 只能访问分配给它的工具
- Schema 导出: 为 LLM Function Calling 生成工具列表
"""
import logging
from typing import Optional

from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    工具注册中心

    管理所有工具的注册和 Agent → Tool 的映射关系。

    Usage:
        registry = ToolRegistry()
        registry.register(knowledge_search_tool)
        registry.bind_to_agent("technical", ["knowledge_search", "ticket_create"])
        tools = registry.get_tools_for_agent("technical")
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._agent_bindings: dict[str, list[str]] = {}

    # ----------------------------------------------------------
    # 注册
    # ----------------------------------------------------------

    def register(self, tool: BaseTool) -> None:
        """注册一个工具"""
        if tool.name in self._tools:
            logger.warning(f"工具 '{tool.name}' 已存在，将被覆盖")
        self._tools[tool.name] = tool
        logger.debug(f"工具已注册: {tool.name}")

    def register_many(self, tools: list[BaseTool]) -> None:
        """批量注册工具"""
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> bool:
        """注销工具"""
        if name in self._tools:
            del self._tools[name]
            # 同时清理绑定
            for agent_name, tool_names in self._agent_bindings.items():
                if name in tool_names:
                    tool_names.remove(name)
            return True
        return False

    # ----------------------------------------------------------
    # 绑定
    # ----------------------------------------------------------

    def bind_to_agent(self, agent_name: str, tool_names: list[str]) -> None:
        """
        将工具绑定到 Agent

        Args:
            agent_name: Agent 名称（如 "technical", "billing"）
            tool_names: 工具名列表
        """
        # 验证工具存在
        for name in tool_names:
            if name not in self._tools and name != "all":
                logger.warning(
                    f"工具 '{name}' 未注册，绑定到 Agent '{agent_name}' 可能无效"
                )

        self._agent_bindings[agent_name] = tool_names
        logger.info(f"Agent '{agent_name}' 绑定工具: {tool_names}")

    # ----------------------------------------------------------
    # 查询
    # ----------------------------------------------------------

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """获取单个工具"""
        return self._tools.get(name)

    def get_tools_for_agent(self, agent_name: str) -> list[BaseTool]:
        """
        获取 Agent 可用的工具列表

        Args:
            agent_name: Agent 名称

        Returns:
            工具实例列表
        """
        tool_names = self._agent_bindings.get(agent_name, [])

        # 特殊: "all" 表示所有工具
        if "all" in tool_names:
            return list(self._tools.values())

        tools = []
        for name in tool_names:
            tool = self._tools.get(name)
            if tool:
                tools.append(tool)
            else:
                logger.warning(f"Agent '{agent_name}' 绑定了未知工具 '{name}'")

        return tools

    def get_tool_schemas(self, agent_name: str) -> list[dict]:
        """
        获取 Agent 可用工具的 OpenAI Function Calling Schema

        Args:
            agent_name: Agent 名称

        Returns:
            [{"type": "function", "function": {...}}, ...]
        """
        tools = self.get_tools_for_agent(agent_name)
        return [t.to_openai_function() for t in tools]

    def get_tool_prompt(self, agent_name: str) -> str:
        """
        生成工具描述文本（注入 system prompt 用）

        Args:
            agent_name: Agent 名称

        Returns:
            格式化的工具列表文本
        """
        tools = self.get_tools_for_agent(agent_name)
        if not tools:
            return "（无可用工具）"
        return "\n".join(t.to_prompt_description() for t in tools)

    # ----------------------------------------------------------
    # 全局查询
    # ----------------------------------------------------------

    def list_all_tools(self) -> list[str]:
        """列出所有已注册的工具名"""
        return list(self._tools.keys())

    def list_all_bindings(self) -> dict[str, list[str]]:
        """列出所有 Agent → Tool 绑定"""
        return dict(self._agent_bindings)

    def get_all_tools(self) -> list[BaseTool]:
        """获取所有已注册的工具"""
        return list(self._tools.values())

    @property
    def tool_count(self) -> int:
        return len(self._tools)
