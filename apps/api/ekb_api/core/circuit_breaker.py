"""M3-4 LLM 熔断器：连续失败开闸 → 短路 → 半开探测 → 恢复。

设计：
  - track_success / track_failure 由 llm.chat 在每次调用后反馈。
  - 连续失败达到 failure_threshold 时 OPEN（短路，不再发请求）。
  - 经过 recovery_timeout 秒后进入 HALF_OPEN（放一个探测请求）。
  - 探测成功 → CLOSED（恢复）；探测失败 → 回到 OPEN。
  - 降级事件通过 store.write_audit_log 记录，不泄露敏感上下文。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class CircuitBreaker:
    """进程级熔断器（单例），保护 LLM 外部调用。"""

    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    _state: CircuitState = field(default_factory=lambda: CircuitState.CLOSED)
    _consecutive_failures: int = 0
    _last_failure_time: float = 0.0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            # 检查是否已过恢复期 → 半开
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("LLM 熔断器进入 HALF_OPEN，放行探测请求")
        return self._state

    def allow_request(self) -> bool:
        """是否允许发请求。CLOSED 和 HALF_OPEN 放行；OPEN 短路。"""
        return self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def track_success(self) -> None:
        if self._state != CircuitState.CLOSED:
            logger.info("LLM 熔断器恢复 → CLOSED")
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0

    def track_failure(self) -> None:
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()
        if self._state == CircuitState.HALF_OPEN:
            # 探测失败，回到开闸
            self._state = CircuitState.OPEN
            logger.warning("LLM 熔断器探测失败，回到 OPEN")
        elif self._consecutive_failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning("LLM 熔断器开闸 OPEN（连续失败 %d 次）", self._consecutive_failures)

    def reset(self) -> None:
        """手动重置（测试用）。"""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time = 0.0


# 全局单例
_breaker: CircuitBreaker | None = None


def get_circuit_breaker() -> CircuitBreaker:
    """获取熔断器单例。

    M4-7 配置化：首次调用时从 Settings 读取 failure_threshold / recovery_timeout。
    已初始化的单例不会被重新配置（避免运行中变更阈值导致状态混乱）。
    """
    global _breaker
    if _breaker is None:
        from ekb_api.core.config import get_settings

        settings = get_settings()
        _breaker = CircuitBreaker(
            failure_threshold=settings.circuit_breaker_failure_threshold,
            recovery_timeout=settings.circuit_breaker_recovery_timeout,
        )
    return _breaker
