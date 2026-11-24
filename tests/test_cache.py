"""core/cache.py 单元测试。"""

from __future__ import annotations

import time

from core.cache import AudioCache


class TestKey:
    def test_stable(self) -> None:
        a = AudioCache.make_key("你好", "带笑", "v1")
        b = AudioCache.make_key("你好", "带笑", "v1")
        assert a == b

    def test_differs_by_text(self) -> None:
        assert AudioCache.make_key("A", "s", "v") != AudioCache.make_key("B", "s", "v")

    def test_differs_by_style(self) -> None:
        assert AudioCache.make_key("t", "A", "v") != AudioCache.make_key("t", "B", "v")

    def test_differs_by_voice(self) -> None:
        assert AudioCache.make_key("t", "s", "A") != AudioCache.make_key("t", "s", "B")

    def test_no_collision_on_separator(self) -> None:
        """风格与文本的边界不能让不同输入撞键。"""
        assert AudioCache.make_key("ab", "c", "v") != AudioCache.make_key("a", "bc", "v")


class TestBasic:
    def test_put_get(self) -> None:
        c = AudioCache(max_bytes=1024)
        c.put("k", b"abc")
        assert c.get("k") == b"abc"

    def test_miss(self) -> None:
        c = AudioCache(max_bytes=1024)
        assert c.get("nope") is None

    def test_hit_counters(self) -> None:
        c = AudioCache(max_bytes=1024)
        c.put("k", b"abc")
        c.get("k")
        c.get("nope")
        s = c.stats()
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["hit_rate"] == 0.5

    def test_empty_payload_ignored(self) -> None:
        c = AudioCache(max_bytes=1024)
        c.put("k", b"")
        assert c.get("k") is None

    def test_zero_capacity_disabled(self) -> None:
        c = AudioCache(max_bytes=0)
        c.put("k", b"abc")
        assert c.get("k") is None
        assert c.stats()["entries"] == 0


class TestEviction:
    def test_lru_order(self) -> None:
        c = AudioCache(max_bytes=10)
        c.put("a", b"1234")
        c.put("b", b"5678")
        c.get("a")          # a 变最近
        c.put("c", b"9999")  # 挤掉 b
        assert c.get("a") is not None
        assert c.get("b") is None

    def test_oversize_rejected(self) -> None:
        c = AudioCache(max_bytes=10)
        c.put("big", b"x" * 100)
        assert c.get("big") is None

    def test_replace_same_key(self) -> None:
        c = AudioCache(max_bytes=100)
        c.put("k", b"1111")
        c.put("k", b"22")
        assert c.get("k") == b"22"
        assert c.stats()["bytes"] == 2

    def test_bytes_tracked(self) -> None:
        c = AudioCache(max_bytes=100)
        c.put("a", b"1234")
        c.put("b", b"5678")
        assert c.stats()["bytes"] == 8


class TestTtl:
    def test_expired(self, monkeypatch) -> None:
        c = AudioCache(max_bytes=1024, ttl_seconds=60)
        base = time.time()
        monkeypatch.setattr(time, "time", lambda: base)
        c.put("k", b"abc")
        monkeypatch.setattr(time, "time", lambda: base + 120)
        assert c.get("k") is None

    def test_not_expired(self, monkeypatch) -> None:
        c = AudioCache(max_bytes=1024, ttl_seconds=60)
        base = time.time()
        monkeypatch.setattr(time, "time", lambda: base)
        c.put("k", b"abc")
        monkeypatch.setattr(time, "time", lambda: base + 30)
        assert c.get("k") == b"abc"

    def test_zero_ttl_never_expires(self, monkeypatch) -> None:
        c = AudioCache(max_bytes=1024, ttl_seconds=0)
        base = time.time()
        monkeypatch.setattr(time, "time", lambda: base)
        c.put("k", b"abc")
        monkeypatch.setattr(time, "time", lambda: base + 999999)
        assert c.get("k") == b"abc"


class TestClear:
    def test_clear(self) -> None:
        c = AudioCache(max_bytes=1024)
        c.put("k", b"abc")
        c.get("k")
        c.clear()
        assert c.stats()["entries"] == 0
        assert c.stats()["hits"] == 0
        assert c.stats()["misses"] == 0
