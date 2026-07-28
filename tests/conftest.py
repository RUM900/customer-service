"""
pytest 配置和共享 fixtures
"""
import os
import sys
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================
# LLM Provider Fixtures
# ============================================================

@pytest.fixture
def mock_messages():
    """标准测试消息"""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, how are you?"},
    ]


@pytest.fixture
def sample_schema():
    """简单的 Pydantic 测试模型"""
    from pydantic import BaseModel, Field

    class TestResponse(BaseModel):
        answer: str = Field(description="The answer to the question")
        confidence: float = Field(description="Confidence score 0-1", ge=0.0, le=1.0)

    return TestResponse
