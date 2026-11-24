"""core/ratelimit.py 单元测试。"""

from __future__ import annotations

import time

from core.ratelimit import RateLimiter


class TestCooldown:
    def test_first_pass(self) -> None:
        rl = RateLimiter()
        assert rl.blocked_reason("s1", cooldown_seconds=60, max_per_hour=0) == ""

    def test_blocked_after_send(self) -> None:
        rl = RateLimiter()
        rl.mark_sent("s1")
        reason = rl.blocked_reason("s1", cooldown_seconds=60, max_per_hour=0)
        assert "冷却" in reason

    def test_independent_sessions(self) -> None:
        rl = RateLimiter()
        rl.mark_sent("s1")
        assert rl.blocked_reason("s2", cooldown_seconds=60, max_per_hour=0) == ""

    def test_zero_cooldown_skips(self) -> None:
        rl = RateLimiter()
        rl.mark_sent("s1")
        assert rl.blocked_reason("s1", cooldown_seconds=0, max_per_hour=0) == ""

    def test_cooldown_expires(self, monkeypatch) -> None:
        rl = RateLimiter()
        rl.mark_sent("s1")
        future = time.time() + 120
        monkeypatch.setattr(time, "time", lambda: future)
        assert rl.blocked_reason("s1", cooldown_seconds=60, max_per_hour=0) == ""


class TestHourlyLimit:
    def test_under_limit(self) -> None:
        rl = RateLimiter()
        rl.mark_sent("s1")
        assert rl.blocked_reason("s1", cooldown_seconds=0, max_per_hour=3) == ""

    def test_at_limit(self) -> None:
        rl = RateLimiter()
        for _ in range(3):
            rl.mark_sent("s1")
        reason = rl.blocked_reason("s1", cooldown_seconds=0, max_per_hour=3)
        assert "上限" in reason

    def test_zero_means_unlimited(self) -> None:
        rl = RateLimiter()
        for _ in range(100):
            rl.mark_sent("s1")
        assert rl.blocked_reason("s1", cooldown_seconds=0, max_per_hour=0) == ""

    def test_window_slides(self, monkeypatch) -> None:
        rl = RateLimiter()
        base = time.time()
        monkeypatch.setattr(time, "time", lambda: base)
        for _ in range(3):
            rl.mark_sent("s1")
        monkeypatch.setattr(time, "time", lambda: base + 3700)
        assert rl.blocked_reason("s1", cooldown_seconds=0, max_per_hour=3) == ""


class TestStatsAndReset:
    def test_stats_counts(self) -> None:
        rl = RateLimiter()
        rl.mark_sent("s1")
        rl.mark_sent("s1")
        assert rl.stats("s1")["hour_count"] == 2

    def test_stats_unknown_session(self) -> None:
        rl = RateLimiter()
        assert rl.stats("nope")["hour_count"] == 0

    def test_reset_one(self) -> None:
        rl = RateLimiter()
        rl.mark_sent("s1")
        rl.mark_sent("s2")
        rl.reset("s1")
        assert rl.stats("s1")["hour_count"] == 0
        assert rl.stats("s2")["hour_count"] == 1

    def test_reset_all(self) -> None:
        rl = RateLimiter()
        rl.mark_sent("s1")
        rl.mark_sent("s2")
        rl.reset()
        assert rl.stats("s1")["hour_count"] == 0
        assert rl.stats("s2")["hour_count"] == 0

    def test_empty_session_noop(self) -> None:
        rl = RateLimiter()
        rl.mark_sent("")
        assert rl.stats("")["hour_count"] == 0
