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
    定期清理过期 IP 记录，防止内存泄漏。
    """

    # 最大保留 IP 记录数（防止极端情况下内存耗尽）
    MAX_CLIENT_ENTRIES = 100_000

    def __init__(self, app, max_requests: int = None, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests or config.RATE_LIMIT_PER_MINUTE
        self.window_seconds = window_seconds
        self._clients: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.time()

    async def dispatch(self, request: Request, call_next):
        # 只限制 chat 端点
        if not request.url.path.startswith("/chat"):
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        now = time.time()

        # 清理当前 IP 的过期记录
        self._clients[client_ip] = [
            t for t in self._clients[client_ip]
            if now - t < self.window_seconds
        ]

        # 清理空记录（当前 IP 的过期条目已清空时删除 key）
        if not self._clients[client_ip]:
            del self._clients[client_ip]

        if len(self._clients.get(client_ip, [])) >= self.max_requests:
            logger.warning(f"Rate limit exceeded for {client_ip}")
            return Response(
                content='{"error": "请求频率过高，请稍后再试"}',
                status_code=429,
                media_type="application/json",
            )

        self._clients[client_ip].append(now)

        # 定期全局清理（每 5 分钟执行一次）
        if now - self._last_cleanup > 300:
            self._cleanup_stale_entries(now)

        # 防御性限制：总记录数过多时强制清理最旧的条目
        if len(self._clients) > self.MAX_CLIENT_ENTRIES:
            logger.warning(f"限流器记录超过 {self.MAX_CLIENT_ENTRIES}，执行强制清理")
            self._force_cleanup(now)

        return await call_next(request)

    def _get_client_ip(self, request: Request) -> str:
        """获取真实客户端 IP

        部署在反向代理后（Nginx 等），request.client.host 会是代理 IP，
        需要信任 X-Forwarded-For 才能拿到真实客户端 IP。
        默认不信任 XFF（防伪造绕过限流），仅在 TRUST_X_FORWARDED_FOR=true 时启用。
        """
        if config.TRUST_X_FORWARDED_FOR:
            xff = request.headers.get("X-Forwarded-For")
            if xff:
                # X-Forwarded-For 格式: "client, proxy1, proxy2"，取第一个
                return xff.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _cleanup_stale_entries(self, now: float) -> None:
        """定期清理所有过期的 IP 记录"""
        stale_ips = []
        for ip, timestamps in self._clients.items():
            # 过滤过期时间戳
            fresh = [t for t in timestamps if now - t < self.window_seconds]
            if fresh:
                self._clients[ip] = fresh
            else:
                stale_ips.append(ip)

        for ip in stale_ips:
            del self._clients[ip]

        self._last_cleanup = now
        if stale_ips:
            logger.debug(f"限流器清理了 {len(stale_ips)} 个过期 IP 记录")

    def _force_cleanup(self, now: float) -> None:
        """强制清理：保留最近活跃的 50% 记录"""
        # 按最近活跃时间排序，保留最近的一半
        sorted_ips = sorted(
            self._clients.items(),
            key=lambda x: max(x[1]) if x[1] else 0,
            reverse=True,
        )
        keep = max(len(sorted_ips) // 2, 10000)
        self._clients = defaultdict(list, dict(sorted_ips[:keep]))
        logger.info(f"强制清理完成，保留 {keep} 条记录")


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
