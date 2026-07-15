"""
FastAPI 中间件

- 请求日志
- CORS
- 简单限流（基于内存的滑动窗口）
"""
import time
import logging
from collections import defaultdict

from fastapi import Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

import config

logger = logging.getLogger("api")


# ============================================================
# 请求日志中间件
# ============================================================

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """记录每个请求的耗时和状态"""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        elapsed_ms = (time.time() - start) * 1000

        logger.info(
            f"{request.method} {request.url.path} "
            f"→ {response.status_code} "
            f"({elapsed_ms:.1f}ms)"
        )
        return response


# ============================================================
# 简单限流中间件
# ============================================================

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    基于滑动窗口的简单限流

    每个 IP 每分钟最多 config.RATE_LIMIT_PER_MINUTE 次请求。
    仅限 /chat/ 路径。
    """

    def __init__(self, app, max_requests: int = None, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests or config.RATE_LIMIT_PER_MINUTE
        self.window_seconds = window_seconds
        self._clients: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # 只限制 chat 端点
        if not request.url.path.startswith("/chat"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        # 清理过期记录
        self._clients[client_ip] = [
            t for t in self._clients[client_ip]
            if now - t < self.window_seconds
        ]

        if len(self._clients[client_ip]) >= self.max_requests:
            logger.warning(f"Rate limit exceeded for {client_ip}")
            return Response(
                content='{"error": "请求频率过高，请稍后再试"}',
                status_code=429,
                media_type="application/json",
            )

        self._clients[client_ip].append(now)
        return await call_next(request)


# ============================================================
# 注册所有中间件
# ============================================================

def setup_middleware(app):
    """注册所有中间件到 FastAPI app"""

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 请求日志
    app.add_middleware(RequestLoggingMiddleware)

    # 限流
    app.add_middleware(RateLimitMiddleware)
