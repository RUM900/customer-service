"""
LLM 调用重试策略 — 基于 tenacity

只对瞬时性错误重试（网络超时/连接中断/429 限流/5xx），
认证错误(401)、参数错误(400)等非瞬时错误直接抛出。
"""
import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)


def _is_transient(exc: Exception) -> bool:
    """判断是否瞬时性错误（可重试）"""
    import openai

    if isinstance(
        exc,
        (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError,
         httpx.WriteError, httpx.RemoteProtocolError),
    ):
        return True
    if isinstance(
        exc,
        (openai.APIConnectionError, openai.APITimeoutError,
         openai.RateLimitError, openai.InternalServerError),
    ):
        return True

    # Claude SDK 的瞬时错误（内部同样基于 httpx）
    try:
        import anthropic
        if isinstance(
            exc,
            (anthropic.APIConnectionError, anthropic.APITimeoutError,
             anthropic.RateLimitError, anthropic.InternalServerError),
        ):
            return True
    except ImportError:
        pass

    return False


def llm_retry(func):
    """对 LLM 网络调用加指数退避重试（最多 3 次）"""
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception(_is_transient),
        reraise=True,
    )(func)
