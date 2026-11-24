"""合成结果缓存。

同一个会话里机器人经常重复短句（「行吧」「好嘞」「那你说呢」），本地合成一句要
十几到几十秒，重复合成纯属浪费。这里按（文本 + 风格 + 音色）缓存 WAV 字节。

设计取舍：
- 只缓存成功结果，失败不缓存（避免把临时故障固化）
- LRU + 容量上限，按字节数淘汰，不按条数（音频大小差异大）
- 进程内内存，不落盘：重启丢失可接受，落盘要考虑密钥与隐私
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import Optional


class AudioCache:
    """按内容寻址的音频 LRU 缓存。"""

    def __init__(self, max_bytes: int = 48 * 1024 * 1024, ttl_seconds: int = 1800) -> None:
        self.max_bytes = max(0, int(max_bytes))
        self.ttl_seconds = max(0, int(ttl_seconds))
        self._items: "OrderedDict[str, tuple[bytes, float]]" = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    @staticmethod
    def make_key(text: str, style: str, voice: str) -> str:
        """拼出稳定的缓存键。"""
        raw = f"{voice}\x00{style}\x00{text}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def get(self, key: str) -> Optional[bytes]:
        """取缓存，命中时刷新 LRU 顺序。"""
        if self.max_bytes <= 0:
            return None
        item = self._items.get(key)
        if item is None:
            self.misses += 1
            return None
        payload, stored_at = item
        if self.ttl_seconds > 0 and time.time() - stored_at > self.ttl_seconds:
            self._drop(key)
            self.misses += 1
            return None
        self._items.move_to_end(key)
        self.hits += 1
        return payload

    def put(self, key: str, payload: bytes) -> None:
        """写缓存，超容量时按最久未用淘汰。"""
        if self.max_bytes <= 0 or not payload:
            return
        if len(payload) > self.max_bytes:
            return
        if key in self._items:
            self._drop(key)
        self._items[key] = (payload, time.time())
        self._bytes += len(payload)
        while self._bytes > self.max_bytes and self._items:
            oldest = next(iter(self._items))
            self._drop(oldest)

    def _drop(self, key: str) -> None:
        item = self._items.pop(key, None)
        if item is not None:
            self._bytes -= len(item[0])

    def clear(self) -> None:
        """清空缓存与统计。"""
        self._items.clear()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    def stats(self) -> dict[str, object]:
        """返回容量与命中率，供状态命令展示。"""
        total = self.hits + self.misses
        return {
            "entries": len(self._items),
            "bytes": self._bytes,
            "max_bytes": self.max_bytes,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }
