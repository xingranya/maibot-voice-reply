"""core/usage.py 单元测试。

重点覆盖三件事：

1. 写进 ``llm_usage`` 的字段口径与主程序一致（尤其是不传 ``timestamp``）。
2. 分钟级汇总走「先读再写回」，同一分钟内多次调用要累加而不是覆盖。
3. 任何写库异常都不能冒泡到调用方 —— 统计坏了不能让语音发不出去。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncio

import pytest

from core.usage import LLM_USAGE_MODEL, TASK_NAME, TASK_USAGE_MODEL, UsageReporter, count_chars


class FakeLogger:
    def __init__(self) -> None:
        self.warnings: List[str] = []

    def warning(self, message: str, *args: Any) -> None:
        self.warnings.append(message % args if args else message)

    def info(self, message: str, *args: Any) -> None:
        del message, args


class FakeDB:
    """只实现 usage.py 用到的那三个方法。"""

    def __init__(self) -> None:
        self.saved: List[tuple[str, Dict[str, Any]]] = []
        self.updated: List[tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        self.rows: List[Dict[str, Any]] = []
        self.fail_on: Optional[str] = None

    async def save(self, model_name: str, data: Dict[str, Any]) -> None:
        if self.fail_on == "save":
            raise RuntimeError("db down")
        self.saved.append((model_name, data))

    async def get(
        self, model_name: str, filters: Dict[str, Any], limit: int = 1
    ) -> List[Dict[str, Any]]:
        del model_name, limit
        if self.fail_on == "get":
            raise RuntimeError("db down")
        for row in self.rows:
            if all(row.get(k) == v for k, v in filters.items()):
                return [row]
        return []

    async def query(
        self,
        model_name: str,
        query_type: str,
        data: Dict[str, Any],
        filters: Dict[str, Any],
    ) -> None:
        if self.fail_on == "query":
            raise RuntimeError("db down")
        self.updated.append((model_name, data, filters))


class FakeCtx:
    def __init__(self) -> None:
        self.db = FakeDB()
        self.logger = FakeLogger()


@pytest.fixture()
def ctx() -> FakeCtx:
    return FakeCtx()


def test_count_chars() -> None:
    assert count_chars("你好") == 2
    assert count_chars("") == 0
    assert count_chars("a b") == 3


def test_request_type_uses_plugin_id(ctx: FakeCtx) -> None:
    reporter = UsageReporter(ctx, "xingranya.maibot-voice-reply")
    assert reporter.request_type == "plugin.xingranya.maibot-voice-reply"


def test_request_type_fallback(ctx: FakeCtx) -> None:
    """插件 ID 取不到时也要给个能归到「插件」板块的值。"""
    assert UsageReporter(ctx, "").request_type == "plugin.voice-reply"


def test_record_success_writes_llm_usage(ctx: FakeCtx) -> None:
    reporter = UsageReporter(ctx, "xingranya.maibot-voice-reply")
    asyncio.run(
        reporter.record_success(
            text="今天下班早",
            model="cosyvoice3-cvcpp",
            provider="本地 cosyvoice.cpp",
            duration=3.2,
        )
    )
    assert len(ctx.db.saved) == 2  # llm_usage + 首次 model_task_usage
    model_name, row = ctx.db.saved[0]
    assert model_name == LLM_USAGE_MODEL
    assert row["task_name"] == TASK_NAME
    assert row["request_type"] == "plugin.xingranya.maibot-voice-reply"
    assert row["prompt_tokens"] == 5
    assert row["completion_tokens"] == 0
    assert row["total_tokens"] == 5
    assert row["time_cost"] == pytest.approx(3.2)
    assert row["model_name"] == "cosyvoice3-cvcpp"
    assert row["model_api_provider_name"] == "本地 cosyvoice.cpp"


def test_record_success_never_sends_timestamp(ctx: FakeCtx) -> None:
    """timestamp 必须交给主程序的 default_factory，传字符串会被 SQLite 拒绝。"""
    reporter = UsageReporter(ctx, "p")
    asyncio.run(reporter.record_success(text="a", model="m", provider="p", duration=1.0))
    _, row = ctx.db.saved[0]
    assert "timestamp" not in row


def test_record_success_cost(ctx: FakeCtx) -> None:
    reporter = UsageReporter(ctx, "p")
    asyncio.run(
        reporter.record_success(
            text="一二三四五六七八九十", model="m", provider="p", duration=1.0, cost=0.25
        )
    )
    _, row = ctx.db.saved[0]
    assert row["cost"] == pytest.approx(0.25)


def test_first_task_usage_row(ctx: FakeCtx) -> None:
    reporter = UsageReporter(ctx, "p")
    asyncio.run(reporter.record_success(text="a", model="m", provider="p", duration=2.5))
    model_name, row = ctx.db.saved[1]
    assert model_name == TASK_USAGE_MODEL
    assert row["request_count"] == 1
    assert row["failure_count"] == 0
    assert row["success_duration"] == pytest.approx(2.5)
    assert row["task_name"] == TASK_NAME
    assert isinstance(row["bucket_time"], datetime)
    assert row["bucket_time"].second == 0
    assert row["bucket_time"].microsecond == 0


def test_task_usage_accumulates(ctx: FakeCtx) -> None:
    """同一分钟内第二次调用必须走 update 累加，不能插新行。"""
    reporter = UsageReporter(ctx, "p")
    asyncio.run(reporter.record_success(text="a", model="m", provider="p", duration=2.0))
    bucket = ctx.db.saved[1][1]["bucket_time"]
    ctx.db.rows.append(
        {
            "bucket_time": bucket,
            "task_name": TASK_NAME,
            "model_name": "m",
            "model_identifier": "m",
            "provider_name": "p",
            "request_count": 1,
            "failure_count": 0,
            "success_duration": 2.0,
        }
    )
    asyncio.run(reporter.record_success(text="b", model="m", provider="p", duration=3.0))
    assert len(ctx.db.updated) == 1
    model_name, data, _ = ctx.db.updated[0]
    assert model_name == TASK_USAGE_MODEL
    assert data["request_count"] == 2
    assert data["failure_count"] == 0
    assert data["success_duration"] == pytest.approx(5.0)


def test_record_failure_only_touches_task_usage(ctx: FakeCtx) -> None:
    """失败不进 llm_usage —— 与主程序「只记成功」的口径一致。"""
    reporter = UsageReporter(ctx, "p")
    asyncio.run(reporter.record_failure(model="m", provider="p", duration=1.0))
    assert ctx.db.saved[0][0] == TASK_USAGE_MODEL
    row = ctx.db.saved[0][1]
    assert row["request_count"] == 1
    assert row["failure_count"] == 1
    assert row["success_duration"] == 0.0


def test_cached_hit_not_counted_as_success_duration(ctx: FakeCtx) -> None:
    """缓存命中计调用次数，但不把耗时算进成功时长（它没真跑推理）。"""
    reporter = UsageReporter(ctx, "p")
    asyncio.run(
        reporter.record_success(text="a", model="m", provider="p", duration=0.0, cached=True)
    )
    row = ctx.db.saved[1][1]
    assert row["request_count"] == 1
    assert row["success_duration"] == 0.0


def test_save_failure_is_swallowed(ctx: FakeCtx) -> None:
    """写库炸了只记 warning，不能把异常抛给合成链路。"""
    reporter = UsageReporter(ctx, "p")
    ctx.db.fail_on = "save"
    asyncio.run(reporter.record_success(text="a", model="m", provider="p", duration=1.0))
    assert ctx.db.saved == []
    assert ctx.logger.warnings


def test_get_failure_is_swallowed(ctx: FakeCtx) -> None:
    reporter = UsageReporter(ctx, "p")
    ctx.db.fail_on = "get"
    asyncio.run(reporter.record_success(text="a", model="m", provider="p", duration=1.0))
    assert ctx.logger.warnings


def test_warning_only_once(ctx: FakeCtx) -> None:
    """同一条错误不能每分钟刷一遍日志。"""
    reporter = UsageReporter(ctx, "p")
    ctx.db.fail_on = "save"
    for _ in range(5):
        asyncio.run(reporter.record_success(text="a", model="m", provider="p", duration=1.0))
    assert len(ctx.logger.warnings) == 1


def test_negative_duration_clamped(ctx: FakeCtx) -> None:
    """耗时传负数（异常路径）时不能把统计写成负值。"""
    reporter = UsageReporter(ctx, "p")
    asyncio.run(reporter.record_success(text="a", model="m", provider="p", duration=-1.0))
    _, row = ctx.db.saved[0]
    assert row["time_cost"] == 0.0
