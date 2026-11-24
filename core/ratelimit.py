"""每会话的冷却与每小时配额。

钩子在每条回复上都会被调用，这里只做内存计数，不落盘：
- ``cooldown_seconds``：同一会话两次自动语音的最小间隔
- ``max_per_hour``：同一会话一小时内最多自动语音条数（0 = 不限制）

计数用 ``deque`` 保存最近一小时的发送时间戳，超窗自动丢弃。
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict


class RateLimiter:
    """按会话维度做冷却与配额控制。"""

    WINDOW_SECONDS = 3600

    def __init__(self) -> None:
        self._last_at: Dict[str, float] = {}
        self._history: Dict[str, Deque[float]] = {}

    def blocked_reason(self, session_id: str, *, cooldown_seconds: int, max_per_hour: int) -> str:
        """返回限流原因；空字符串表示放行。

        Args:
            session_id: 会话标识。
            cooldown_seconds: 冷却秒数，0 表示不限制。
            max_per_hour: 每小时上限，0 表示不限制。
        """
        if not session_id:
            return ""
        now = time.time()

        if cooldown_seconds > 0:
            last = self._last_at.get(session_id, 0.0)
            if last > 0:
                elapsed = now - last
                if elapsed < cooldown_seconds:
                    return f"冷却中，还需 {int(cooldown_seconds - elapsed)} 秒"

        if max_per_hour > 0:
            history = self._history.get(session_id)
            if history:
                cutoff = now - self.WINDOW_SECONDS
                while history and history[0] < cutoff:
                    history.popleft()
                if len(history) >= max_per_hour:
                    return f"本小时已达上限 {max_per_hour} 条"

        return ""

    def mark_sent(self, session_id: str) -> None:
        """记录一次成功发送。"""
        if not session_id:
            return
        now = time.time()
        self._last_at[session_id] = now
        self._history.setdefault(session_id, deque()).append(now)

    def stats(self, session_id: str) -> dict[str, object]:
        """返回某会话的当前计数，供状态命令展示。"""
        now = time.time()
        history = self._history.get(session_id) or deque()
        cutoff = now - self.WINDOW_SECONDS
        recent = [t for t in history if t >= cutoff]
        last = self._last_at.get(session_id, 0.0)
        return {
            "last_at": last,
            "since_last": round(now - last, 1) if last else None,
            "hour_count": len(recent),
            "sessions": len(self._history),
        }

    def reset(self, session_id: str = "") -> None:
        """清空指定会话或全部会话的计数。"""
        if session_id:
            self._last_at.pop(session_id, None)
            self._history.pop(session_id, None)
            return
        self._last_at.clear()
        self._history.clear()
