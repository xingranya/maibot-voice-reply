"""把语音合成的消费量写进 MaiBot 的模型消耗统计。

MaiBot 的「模型消耗」页面和 ``cssqy.token-usage-report`` 插件读的都是同一套表：

- ``llm_usage``：每次成功调用的明细，WebUI 的模型排行、板块占比、费用、平均耗时全从
  这张表算；聚合任务每 15 分钟再把它卷成 ``statistics_*_hourly``。
- ``model_task_usage``：分钟级可靠性汇总，成功和失败都计，只用来算失败率。

本地语音合成不经过主程序的模型调用链（``LLMOrchestrator``），所以不会自动出现在上面
两张表里。这里按主程序同样的字段口径自己落记录：

- ``request_type`` 用 ``plugin.<插件 ID>``。WebUI 的板块归属规则是
  ``request_type.startswith("plugin.")`` → 「插件 <ID>」；``cssqy`` 的模块分组按
  ``plugin`` 前缀归到「插件」。
- ``task_name`` 用 ``tts``，与主程序原生语音合成分工同名，便于横向对比。
- ``prompt_tokens`` 记合成文本的字符数（TTS 没有 token 概念，字符数是最接近工作量的
  口径），``completion_tokens`` 恒为 0。
- ``cost`` 按「每千字符单价」折算；本地引擎填 0 即为免费。
- 失败尝试只进 ``model_task_usage``，不进 ``llm_usage`` —— 与主程序
  ``track_model_task_usage``（含失败）和 ``record_usage_to_database``（只记成功）的分工一致。

所有写入都走插件 SDK 的 ``ctx.db`` 能力（``database.save`` / ``database.get`` /
``database.query``），不直接 import 主程序源码，也不碰数据库文件。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

#: ``llm_usage`` 表对应的 SQLModel 类名
LLM_USAGE_MODEL = "ModelUsage"

#: ``model_task_usage`` 表对应的 SQLModel 类名
TASK_USAGE_MODEL = "ModelTaskUsage"

#: 与主程序原生语音合成分工同名，方便在统计页里对照
TASK_NAME = "tts"


def count_chars(text: str) -> int:
    """按字符数计工作量（TTS 没有 token 概念）。"""
    return len(text or "")


class UsageReporter:
    """把一次语音合成的结果写进 MaiBot 的统计表。

    写入失败只记一条 warning，绝不影响语音本身是否发出 —— 统计是附加信息，
    不能因为它出错就让用户听不到声音。
    """

    def __init__(self, ctx: Any, plugin_id: str) -> None:
        self._ctx = ctx
        self._plugin_id = (plugin_id or "").strip()
        self._warned = False

    @property
    def request_type(self) -> str:
        """板块归属用的请求类型，WebUI 会显示成「插件 <ID>」。"""
        return f"plugin.{self._plugin_id}" if self._plugin_id else "plugin.voice-reply"

    def _warn(self, message: str, *args: Any) -> None:
        """同一条错误只提醒一次，避免每分钟刷屏。"""
        if self._warned:
            return
        self._warned = True
        self._ctx.logger.warning(message, *args)

    async def record_success(
        self,
        *,
        text: str,
        model: str,
        provider: str,
        duration: float,
        cost: float = 0.0,
        session_id: str = "",
        cached: bool = False,
    ) -> None:
        """记录一次成功的合成（含缓存命中，命中时 ``duration`` 传 0）。"""
        chars = count_chars(text)
        record = {
            "model_name": model,
            "model_assign_name": model,
            "model_api_provider_name": provider,
            "session_id": session_id or "",
            "task_name": TASK_NAME,
            "request_type": self.request_type,
            "time_cost": float(max(0.0, duration)),
            # timestamp 不传：让主程序用 default_factory=datetime.now 填，
            # 传字符串会被 SQLite DateTime 类型拒绝。
            "prompt_tokens": chars,
            "completion_tokens": 0,
            "total_tokens": chars,
            "cost": float(max(0.0, cost)),
        }
        try:
            await self._ctx.db.save(LLM_USAGE_MODEL, record)
        except Exception as exc:  # noqa: BLE001
            self._warn("写入模型消耗统计失败（不影响语音发送）：%s", exc)
        await self._bump_task_usage(
            model=model, provider=provider, duration=duration, succeeded=not cached
        )

    async def record_failure(
        self,
        *,
        model: str,
        provider: str,
        duration: float,
    ) -> None:
        """记录一次失败的尝试：只进可靠性汇总，不进消耗明细。"""
        await self._bump_task_usage(
            model=model, provider=provider, duration=duration, succeeded=False
        )

    async def _bump_task_usage(
        self, *, model: str, provider: str, duration: float, succeeded: bool
    ) -> None:
        """累加分钟级汇总：主键是五元组，只能先读再写回。"""
        bucket = datetime.now().replace(second=0, microsecond=0)
        filters = {
            "bucket_time": bucket,
            "task_name": TASK_NAME,
            "model_name": model,
            "model_identifier": model,
            "provider_name": provider,
        }
        try:
            rows = await self._ctx.db.get(TASK_USAGE_MODEL, filters=filters, limit=1)
            existing: Optional[Dict[str, Any]] = None
            if isinstance(rows, list) and rows:
                existing = rows[0]
            elif isinstance(rows, dict) and rows:
                existing = rows

            if existing is None:
                await self._ctx.db.save(
                    TASK_USAGE_MODEL,
                    {
                        **filters,
                        "request_count": 1,
                        "failure_count": 0 if succeeded else 1,
                        "success_duration": float(max(0.0, duration)) if succeeded else 0.0,
                    },
                )
                return

            await self._ctx.db.query(
                TASK_USAGE_MODEL,
                query_type="update",
                data={
                    "request_count": int(existing.get("request_count") or 0) + 1,
                    "failure_count": int(existing.get("failure_count") or 0)
                    + (0 if succeeded else 1),
                    "success_duration": float(existing.get("success_duration") or 0.0)
                    + (float(max(0.0, duration)) if succeeded else 0.0),
                },
                filters=filters,
            )
        except Exception as exc:  # noqa: BLE001
            self._warn("写入任务可靠性统计失败（不影响语音发送）：%s", exc)
