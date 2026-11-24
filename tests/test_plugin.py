"""plugin.py 组件级单元测试。

用桩 SDK + 假 ctx，覆盖钩子判定链、命令解析、Tool 边界。
不发真实网络请求。
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

import plugin as P
from core.config import VoicePluginConfig


class FakeSend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, str]] = []

    async def custom(self, kind: str, data: Any, stream_id: str) -> bool:
        self.calls.append((kind, data, stream_id))
        return True

    async def text(self, text: str, stream_id: str) -> bool:
        self.calls.append(("text", text, stream_id))
        return True


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _log(self, level: str, msg: str, *args: Any) -> None:
        try:
            self.records.append((level, msg % args if args else msg))
        except Exception:
            self.records.append((level, msg))

    def debug(self, msg: str, *a: Any) -> None:
        self._log("debug", msg, *a)

    def info(self, msg: str, *a: Any) -> None:
        self._log("info", msg, *a)

    def warning(self, msg: str, *a: Any) -> None:
        self._log("warning", msg, *a)

    def error(self, msg: str, *a: Any) -> None:
        self._log("error", msg, *a)

    def messages(self, level: str = "") -> list[str]:
        return [m for lv, m in self.records if not level or lv == level]


class FakeCtx:
    def __init__(self) -> None:
        self.send = FakeSend()
        self.logger = FakeLogger()

    async def send_text(self, text: str, stream_id: str) -> bool:
        return await self.send.text(text, stream_id)


class FakeTTS:
    """假 TTS：记录调用，返回固定音频。"""

    RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
    DEFAULT_MIMO_BASE_URL = "x"
    DEFAULT_SF_BASE_URL = "x"
    DEFAULT_CV_ENDPOINT = "x"

    def __init__(self, ok: bool = True, error: str = "", status: int = 0) -> None:
        self.ok = ok
        self.error = error
        self.status = status
        self.calls: list[dict[str, Any]] = []

    async def synthesize_with_cvcpp(self, **kw: Any) -> dict[str, Any]:
        self.calls.append({"engine": "cvcpp", **kw})
        return self._result()

    async def synthesize_with_preset(self, **kw: Any) -> dict[str, Any]:
        self.calls.append({"engine": "preset", **kw})
        return self._result()

    async def synthesize_with_voice_clone(self, **kw: Any) -> dict[str, Any]:
        self.calls.append({"engine": "clone", **kw})
        return self._result()

    async def synthesize_with_design(self, **kw: Any) -> dict[str, Any]:
        self.calls.append({"engine": "design", **kw})
        return self._result()

    async def synthesize_with_sf_clone(self, **kw: Any) -> dict[str, Any]:
        self.calls.append({"engine": "sf", **kw})
        return self._result()

    async def sf_upload_voice(self, **kw: Any) -> dict[str, Any]:
        return {"success": True, "uri": "speech:test:1:2"}

    async def check_cvcpp_health(self, **kw: Any) -> dict[str, Any]:
        return {"ok": True, "data": {"status": "ready"}}

    async def close(self) -> None:
        return None

    def _result(self) -> dict[str, Any]:
        if not self.ok:
            out: dict[str, Any] = {"success": False, "error": self.error}
            if self.status:
                out["status"] = self.status
            return out
        return {
            "success": True,
            "audio_base64": "data:audio/wav;base64," + base64.b64encode(b"RIFF" + b"0" * 200).decode(),
            "audio_len": 204,
            "elapsed_ms": 1200,
        }


def make_plugin(**overrides: Any) -> P.VoiceReplyPlugin:
    """构造一个配置齐全、依赖全部替换为假实现的插件实例。"""
    p = P.create_plugin()
    ctx = FakeCtx()
    p._ctx = ctx

    base = {
        "plugin": {"enabled": True, "config_version": "1.0.0"},
        "engine": {
            "voice_mode": "cvcpp",
            "cv_endpoint": "http://x:18091",
            "cv_speed": 1.1,
            "cv_timeout_seconds": 110.0,
            "sf_api_key": "",
            "sf_api_base_url": "https://api.siliconflow.cn/v1",
            "sf_model": "FunAudioLLM/CosyVoice2-0.5B",
            "sf_voice_uri": "",
            "sf_ref_audio": "",
            "sf_prompt_text": "",
            "sf_sample_rate": 32000,
            "mimo_api_key": "",
            "mimo_api_base_url": "https://api.xiaomimimo.com/v1",
            "preset_voice": "冰糖",
            "clone_voice": "",
            "design_voice_description": "",
            "mimo_speed": 0.0,
            "mimo_tag": "",
            "mimo_optimize_text_preview": False,
            "voices_dir": "voices",
            "default_voice": "",
        },
        "style": {
            "style": "带笑",
            "style_rotate": False,
            "clean_punctuation": True,
            "strip_latin": True,
            "oral_rewrite": False,
            "oral_model": "",
        },
        "auto": {
            "probability": 1.0,
            "min_chars": 12,
            "max_chars": 50,
            "in_group": True,
            "in_private": True,
            "exclude_groups": "",
            "keep_emoji": True,
            "cooldown_seconds": 0,
            "max_per_hour": 0,
        },
        "filter": {
            "skip_patterns": r"https?://",
            "min_chinese_ratio": 0.6,
            "allow_components": "text,at,reply,emoji",
        },
        "audio": {
            "peak_db": -1.5,
            "rms_db": -18.0,
            "denoise": False,
            "accent_retry": 0,
            "accent_risk_max": 10.0,
        },
    }
    for section, values in overrides.items():
        base[section].update(values)

    p._plugin_config_instance = VoicePluginConfig.model_validate(base)
    p.tts = FakeTTS()
    p._refresh_skip_regex()
    p._cache = p._build_cache()
    return p


def group_msg(text: str, gid: str = "1131727249", **extra: Any) -> dict:
    msg = {
        "processed_plain_text": text,
        "session_id": f"g:{gid}",
        "message_info": {"group_info": {"group_id": gid}},
        "raw_message": [{"type": "text", "data": "x"}],
    }
    msg.update(extra)
    return msg


# ────────────────────────── 钩子判定链 ──────────────────────────


class TestGateBasics:
    def test_disabled_plugin(self) -> None:
        p = make_plugin(plugin={"enabled": False})
        import asyncio

        assert asyncio.run(p.voice_probability_gate(message=group_msg("今天下班早我买了点水果回来"))) == {
            "action": "continue"
        }

    def test_zero_probability(self) -> None:
        p = make_plugin(auto={"probability": 0.0})
        import asyncio

        assert asyncio.run(p.voice_probability_gate(message=group_msg("今天下班早我买了点水果回来"))) == {
            "action": "continue"
        }

    def test_no_tts(self) -> None:
        p = make_plugin()
        p.tts = None
        import asyncio

        assert asyncio.run(p.voice_probability_gate(message=group_msg("今天下班早我买了点水果回来"))) == {
            "action": "continue"
        }

    def test_message_not_dict(self) -> None:
        p = make_plugin()
        import asyncio

        assert asyncio.run(p.voice_probability_gate(message="x")) == {"action": "continue"}

    def test_empty_text(self) -> None:
        p = make_plugin()
        import asyncio

        assert asyncio.run(p.voice_probability_gate(message=group_msg(""))) == {"action": "continue"}

    def test_no_session(self) -> None:
        p = make_plugin()
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来")
        msg.pop("session_id")
        assert asyncio.run(p.voice_probability_gate(message=msg)) == {"action": "continue"}


class TestGateLength:
    def test_below_min(self) -> None:
        p = make_plugin()
        import asyncio

        assert asyncio.run(p.voice_probability_gate(message=group_msg("好的"))) == {"action": "continue"}

    def test_above_max(self) -> None:
        p = make_plugin()
        import asyncio

        text = "字" * 60
        assert asyncio.run(p.voice_probability_gate(message=group_msg(text))) == {"action": "continue"}

    def test_max_zero_unlimited(self) -> None:
        p = make_plugin(auto={"max_chars": 0})
        import asyncio

        text = "今天下班早我买了点水果回来挺甜的你要不要尝尝看" * 3
        result = asyncio.run(p.voice_probability_gate(message=group_msg(text)))
        assert result["action"] == "abort"


class TestGateScope:
    def test_group_disabled(self) -> None:
        p = make_plugin(auto={"in_group": False})
        import asyncio

        assert asyncio.run(p.voice_probability_gate(message=group_msg("今天下班早我买了点水果回来"))) == {
            "action": "continue"
        }

    def test_private_disabled(self) -> None:
        p = make_plugin(auto={"in_private": False})
        import asyncio

        msg = {
            "processed_plain_text": "今天下班早我买了点水果回来",
            "session_id": "p:123",
            "message_info": {},
            "raw_message": [{"type": "text", "data": "x"}],
        }
        assert asyncio.run(p.voice_probability_gate(message=msg)) == {"action": "continue"}

    def test_private_allowed(self) -> None:
        p = make_plugin()
        import asyncio

        msg = {
            "processed_plain_text": "今天下班早我买了点水果回来",
            "session_id": "p:123",
            "message_info": {},
            "raw_message": [{"type": "text", "data": "x"}],
        }
        assert asyncio.run(p.voice_probability_gate(message=msg))["action"] == "abort"

    def test_excluded_group(self) -> None:
        p = make_plugin(auto={"exclude_groups": "724613337,123456789"})
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来", gid="724613337")
        assert asyncio.run(p.voice_probability_gate(message=msg)) == {"action": "continue"}

    def test_not_excluded_group(self) -> None:
        p = make_plugin(auto={"exclude_groups": "724613337"})
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来", gid="1131727249")
        assert asyncio.run(p.voice_probability_gate(message=msg))["action"] == "abort"

    def test_empty_exclude_list(self) -> None:
        p = make_plugin(auto={"exclude_groups": ""})
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来", gid="724613337")
        assert asyncio.run(p.voice_probability_gate(message=msg))["action"] == "abort"


class TestGateFilter:
    def test_url_blocked(self) -> None:
        p = make_plugin()
        import asyncio

        msg = group_msg("你看这个 https://b23.tv/abc 笑死我了真的")
        assert asyncio.run(p.voice_probability_gate(message=msg)) == {"action": "continue"}

    def test_chinese_ratio_blocked(self) -> None:
        p = make_plugin()
        import asyncio

        msg = group_msg("abcdefghijklmnop")
        assert asyncio.run(p.voice_probability_gate(message=msg)) == {"action": "continue"}

    def test_image_component_blocked(self) -> None:
        p = make_plugin()
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来", raw_message=[{"type": "text"}, {"type": "image"}])
        assert asyncio.run(p.voice_probability_gate(message=msg)) == {"action": "continue"}

    def test_emoji_component_allowed(self) -> None:
        p = make_plugin()
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来", raw_message=[{"type": "text"}, {"type": "emoji"}])
        assert asyncio.run(p.voice_probability_gate(message=msg))["action"] == "abort"


class TestGateRateLimit:
    def test_cooldown(self) -> None:
        p = make_plugin(auto={"cooldown_seconds": 600})
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来")
        assert asyncio.run(p.voice_probability_gate(message=msg))["action"] == "abort"
        assert asyncio.run(p.voice_probability_gate(message=msg)) == {"action": "continue"}

    def test_hourly_limit(self) -> None:
        p = make_plugin(auto={"max_per_hour": 2})
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来")
        for _ in range(2):
            assert asyncio.run(p.voice_probability_gate(message=msg))["action"] == "abort"
        assert asyncio.run(p.voice_probability_gate(message=msg)) == {"action": "continue"}


class TestGateFailure:
    def test_synthesis_failed_keeps_text(self) -> None:
        p = make_plugin()
        p.tts = FakeTTS(ok=False, error="boom")
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来")
        assert asyncio.run(p.voice_probability_gate(message=msg)) == {"action": "continue"}

    def test_send_failed_keeps_text(self) -> None:
        p = make_plugin()

        async def boom(kind: str, data: Any, stream_id: str) -> bool:
            return False

        p.ctx.send.custom = boom  # type: ignore[assignment]
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来")
        assert asyncio.run(p.voice_probability_gate(message=msg)) == {"action": "continue"}

    def test_exception_keeps_text(self) -> None:
        p = make_plugin()

        async def boom(**kw: Any) -> dict:
            raise RuntimeError("net down")

        p.tts.synthesize_with_cvcpp = boom  # type: ignore[assignment]
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来")
        assert asyncio.run(p.voice_probability_gate(message=msg)) == {"action": "continue"}


class TestGateSuccess:
    def test_abort_has_reason(self) -> None:
        p = make_plugin()
        import asyncio

        result = asyncio.run(p.voice_probability_gate(message=group_msg("今天下班早我买了点水果回来")))
        assert result["action"] == "abort"
        assert result["reason"]
        assert "语音" in result["reason"]

    def test_voice_sent(self) -> None:
        p = make_plugin()
        import asyncio

        asyncio.run(p.voice_probability_gate(message=group_msg("今天下班早我买了点水果回来")))
        kinds = [c[0] for c in p.ctx.send.calls]
        assert "voice" in kinds

    def test_emoji_resent(self) -> None:
        p = make_plugin()
        import asyncio

        png = base64.b64encode(b"png").decode()
        msg = group_msg(
            "今天下班早我买了点水果回来",
            raw_message=[{"type": "text"}, {"type": "emoji", "binary_data_base64": png}],
        )
        asyncio.run(p.voice_probability_gate(message=msg))
        kinds = [c[0] for c in p.ctx.send.calls]
        assert "emoji" in kinds

    def test_emoji_not_resent_when_disabled(self) -> None:
        p = make_plugin(auto={"keep_emoji": False})
        import asyncio

        png = base64.b64encode(b"png").decode()
        msg = group_msg(
            "今天下班早我买了点水果回来",
            raw_message=[{"type": "text"}, {"type": "emoji", "binary_data_base64": png}],
        )
        asyncio.run(p.voice_probability_gate(message=msg))
        kinds = [c[0] for c in p.ctx.send.calls]
        assert "emoji" not in kinds

    def test_emoji_without_data_skipped(self) -> None:
        p = make_plugin()
        import asyncio

        msg = group_msg(
            "今天下班早我买了点水果回来",
            raw_message=[{"type": "text"}, {"type": "emoji"}],
        )
        asyncio.run(p.voice_probability_gate(message=msg))
        kinds = [c[0] for c in p.ctx.send.calls]
        assert "emoji" not in kinds

    def test_marks_rate_limit(self) -> None:
        p = make_plugin(auto={"cooldown_seconds": 600})
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来")
        asyncio.run(p.voice_probability_gate(message=msg))
        assert p.limiter.stats("g:1131727249")["hour_count"] == 1

    def test_failed_send_does_not_mark(self) -> None:
        p = make_plugin(auto={"cooldown_seconds": 600})

        async def boom(kind: str, data: Any, stream_id: str) -> bool:
            return False

        p.ctx.send.custom = boom  # type: ignore[assignment]
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来")
        asyncio.run(p.voice_probability_gate(message=msg))
        assert p.limiter.stats("g:1131727249")["hour_count"] == 0


# ────────────────────────── 命令 ──────────────────────────


class TestCommands:
    def test_status(self) -> None:
        p = make_plugin()
        import asyncio

        ok, note, intercept = asyncio.run(
            p.cmd_status(stream_id="g:1", matched_groups={})
        )
        assert ok is True and intercept is True
        assert p.ctx.send.calls[-1][0] == "text"
        body = p.ctx.send.calls[-1][1]
        assert "cvcpp" in body
        assert "冷却" in body

    def test_probability_valid(self) -> None:
        p = make_plugin()
        import asyncio

        ok, _, _ = asyncio.run(
            p.cmd_probability(stream_id="g:1", matched_groups={"value": "0.45"})
        )
        assert ok is True
        assert p.config.auto.probability == 0.45

    def test_probability_invalid(self) -> None:
        p = make_plugin()
        import asyncio

        ok, _, _ = asyncio.run(
            p.cmd_probability(stream_id="g:1", matched_groups={"value": "1.5"})
        )
        assert ok is False
        assert p.config.auto.probability == 1.0

    def test_probability_missing(self) -> None:
        p = make_plugin()
        import asyncio

        ok, _, _ = asyncio.run(p.cmd_probability(stream_id="g:1", matched_groups={}))
        assert ok is False

    def test_cooldown(self) -> None:
        p = make_plugin()
        import asyncio

        ok, _, _ = asyncio.run(
            p.cmd_cooldown(stream_id="g:1", matched_groups={"value": "120"})
        )
        assert ok is True
        assert p.config.auto.cooldown_seconds == 120

    def test_cooldown_negative_rejected(self) -> None:
        p = make_plugin()
        import asyncio

        ok, _, _ = asyncio.run(
            p.cmd_cooldown(stream_id="g:1", matched_groups={"value": "-5"})
        )
        assert ok is False

    def test_limit(self) -> None:
        p = make_plugin()
        import asyncio

        ok, _, _ = asyncio.run(p.cmd_limit(stream_id="g:1", matched_groups={"value": "15"}))
        assert ok is True
        assert p.config.auto.max_per_hour == 15

    def test_group_add(self) -> None:
        p = make_plugin()
        import asyncio

        ok, _, _ = asyncio.run(p.cmd_group(stream_id="g:1", matched_groups={"value": "123456789"}))
        assert ok is True
        assert "123456789" in p.config.auto.exclude_groups

    def test_group_remove(self) -> None:
        p = make_plugin(auto={"exclude_groups": "123456789"})
        import asyncio

        ok, _, _ = asyncio.run(p.cmd_group(stream_id="g:1", matched_groups={"value": "123456789"}))
        assert ok is True
        assert "123456789" not in p.config.auto.exclude_groups

    def test_group_missing(self) -> None:
        p = make_plugin()
        import asyncio

        ok, _, _ = asyncio.run(p.cmd_group(stream_id="g:1", matched_groups={}))
        assert ok is False

    def test_toggle_off(self) -> None:
        p = make_plugin(auto={"probability": 0.5})
        import asyncio

        ok, _, _ = asyncio.run(p.cmd_toggle(stream_id="g:1", matched_groups={}))
        assert ok is True
        assert p.config.auto.probability == 0.0

    def test_toggle_on_restores(self) -> None:
        p = make_plugin(auto={"probability": 0.5})
        import asyncio

        asyncio.run(p.cmd_toggle(stream_id="g:1", matched_groups={}))
        asyncio.run(p.cmd_toggle(stream_id="g:1", matched_groups={}))
        assert p.config.auto.probability == 0.5

    def test_test_command(self) -> None:
        p = make_plugin()
        import asyncio

        ok, _, _ = asyncio.run(
            p.cmd_test(stream_id="g:1", matched_groups={"text": "你好呀今天天气不错"})
        )
        assert ok is True
        kinds = [c[0] for c in p.ctx.send.calls]
        assert "voice" in kinds

    def test_test_command_failure(self) -> None:
        p = make_plugin()
        p.tts = FakeTTS(ok=False, error="boom")
        import asyncio

        ok, _, _ = asyncio.run(
            p.cmd_test(stream_id="g:1", matched_groups={"text": "你好呀今天天气不错"})
        )
        assert ok is False

    def test_test_command_missing_text(self) -> None:
        p = make_plugin()
        import asyncio

        ok, _, _ = asyncio.run(p.cmd_test(stream_id="g:1", matched_groups={}))
        assert ok is False


# ────────────────────────── Tool ──────────────────────────


class TestTool:
    def test_no_stream(self) -> None:
        p = make_plugin()
        import asyncio

        result = asyncio.run(p.send_voice_reply(reply_text="你好", msg_id="1"))
        assert result["success"] is False

    def test_empty_text(self) -> None:
        p = make_plugin()
        import asyncio

        result = asyncio.run(p.send_voice_reply(reply_text="  ", msg_id="1", stream_id="g:1"))
        assert result["success"] is False

    def test_over_max(self) -> None:
        p = make_plugin()
        import asyncio

        result = asyncio.run(
            p.send_voice_reply(reply_text="字" * 60, msg_id="1", stream_id="g:1")
        )
        assert result["success"] is False
        assert "50" in result["error"]

    def test_max_zero_unlimited(self) -> None:
        p = make_plugin(auto={"max_chars": 0})
        import asyncio

        result = asyncio.run(
            p.send_voice_reply(reply_text="字" * 200, msg_id="1", stream_id="g:1")
        )
        assert result["success"] is True

    def test_success(self) -> None:
        p = make_plugin()
        import asyncio

        result = asyncio.run(
            p.send_voice_reply(reply_text="行吧那就这样吧我先挂了", msg_id="1", stream_id="g:1")
        )
        assert result["success"] is True
        assert result["engine"] == "cvcpp"
        assert result["chars"] > 0

    def test_synthesis_failure(self) -> None:
        p = make_plugin()
        p.tts = FakeTTS(ok=False, error="boom")
        import asyncio

        result = asyncio.run(
            p.send_voice_reply(reply_text="行吧那就这样吧我先挂了", msg_id="1", stream_id="g:1")
        )
        assert result["success"] is False

    def test_send_failure(self) -> None:
        p = make_plugin()

        async def boom(kind: str, data: Any, stream_id: str) -> bool:
            return False

        p.ctx.send.custom = boom  # type: ignore[assignment]
        import asyncio

        result = asyncio.run(
            p.send_voice_reply(reply_text="行吧那就这样吧我先挂了", msg_id="1", stream_id="g:1")
        )
        assert result["success"] is False

    def test_clean_applied(self) -> None:
        p = make_plugin()
        import asyncio

        asyncio.run(
            p.send_voice_reply(reply_text="今天 下班 早", msg_id="1", stream_id="g:1")
        )
        text = p.tts.calls[-1]["text"]
        assert "，" in text

    def test_style_instruction_used(self) -> None:
        """preset 引擎会把 style 传给 TTS；cvcpp 服务端不接受该参数。"""
        p = make_plugin(engine={"voice_mode": "preset"})
        import asyncio

        asyncio.run(
            p.send_voice_reply(
                reply_text="行吧那就这样吧我先挂了",
                msg_id="1",
                stream_id="g:1",
                style_instruction="特别开心",
            )
        )
        assert p.tts.calls[-1]["style_instruction"] == "特别开心"

    def test_default_style_used(self) -> None:
        p = make_plugin(engine={"voice_mode": "preset"})
        import asyncio

        asyncio.run(
            p.send_voice_reply(reply_text="行吧那就这样吧我先挂了", msg_id="1", stream_id="g:1")
        )
        assert p.tts.calls[-1]["style_instruction"] == "带笑"

    def test_cvcpp_has_no_style_param(self) -> None:
        p = make_plugin()
        import asyncio

        asyncio.run(
            p.send_voice_reply(reply_text="行吧那就这样吧我先挂了", msg_id="1", stream_id="g:1")
        )
        assert "style_instruction" not in p.tts.calls[-1]


# ────────────────────────── 音色解析 ──────────────────────────


class TestResolveVoice:
    def test_cvcpp(self) -> None:
        p = make_plugin()
        assert p._resolve_voice() == ("http://x:18091", "", "cvcpp")

    def test_preset(self) -> None:
        p = make_plugin(engine={"voice_mode": "preset"})
        assert p._resolve_voice() == ("冰糖", "", "preset")

    def test_preset_custom(self) -> None:
        p = make_plugin(engine={"voice_mode": "preset", "preset_voice": "茉莉"})
        assert p._resolve_voice()[0] == "茉莉"

    def test_design(self) -> None:
        p = make_plugin(engine={"voice_mode": "design", "design_voice_description": "甜妹"})
        assert p._resolve_voice() == ("甜妹", "", "design")

    def test_design_missing_desc(self) -> None:
        p = make_plugin(engine={"voice_mode": "design", "design_voice_description": ""})
        assert p._resolve_voice() == ("", "", "")

    def test_clone_missing_audio(self) -> None:
        p = make_plugin(engine={"voice_mode": "clone", "clone_voice": "nope"})
        assert p._resolve_voice() == ("", "", "")

    def test_clone_with_audio(self) -> None:
        p = make_plugin(engine={"voice_mode": "clone", "clone_voice": "ref"})
        p.voices = {"ref": "data:audio/wav;base64,QUJD"}
        assert p._resolve_voice() == ("ref", "data:audio/wav;base64,QUJD", "clone")

    def test_sf_with_uri(self) -> None:
        p = make_plugin(engine={"voice_mode": "sf", "sf_voice_uri": "speech:a:1:2"})
        key, ref, mode = p._resolve_voice()
        assert mode == "sf" and key == "speech:a:1:2"

    def test_default_voice_override(self) -> None:
        p = make_plugin(engine={"voice_mode": "preset", "default_voice": "苏打"})
        assert p._resolve_default_voice() == "苏打"


class TestPickStyle:
    def test_single(self) -> None:
        p = make_plugin()
        assert p._pick_style() == "带笑"

    def test_rotate_picks_member(self) -> None:
        p = make_plugin(style={"style": "A|B|C", "style_rotate": True})
        picked = {p._pick_style() for _ in range(50)}
        assert picked <= {"A", "B", "C"}
        assert len(picked) > 1

    def test_no_rotate_always_first(self) -> None:
        p = make_plugin(style={"style": "A|B|C", "style_rotate": False})
        assert {p._pick_style() for _ in range(20)} == {"A"}

    def test_empty_style(self) -> None:
        p = make_plugin(style={"style": ""})
        assert p._pick_style() == ""


class TestSkipRegex:
    def test_compiled(self) -> None:
        p = make_plugin()
        assert p._skip_regex is not None

    def test_invalid_pattern_ignored(self) -> None:
        p = make_plugin(filter={"skip_patterns": "(["}, )
        assert p._skip_regex is None

    def test_empty_pattern(self) -> None:
        p = make_plugin(filter={"skip_patterns": ""})
        assert p._skip_regex is None


class TestApi:
    def test_synthesize(self) -> None:
        p = make_plugin()
        import asyncio

        result = asyncio.run(p.api_synthesize(text="测试一下"))
        assert result["success"] is True
        assert result["engine"] == "cvcpp"
        assert result["audio_base64"]

    def test_synthesize_empty(self) -> None:
        p = make_plugin()
        import asyncio

        result = asyncio.run(p.api_synthesize(text="https://x.com"))
        assert result["success"] is False

    def test_synthesize_failure(self) -> None:
        p = make_plugin()
        p.tts = FakeTTS(ok=False, error="boom")
        import asyncio

        result = asyncio.run(p.api_synthesize(text="测试一下"))
        assert result["success"] is False


# ────────────────────────── 缓存 ──────────────────────────


class TestCacheIntegration:
    def test_second_call_hits_cache(self) -> None:
        p = make_plugin()
        import asyncio

        asyncio.run(p.api_synthesize(text="测试缓存"))
        first = len(p.tts.calls)
        asyncio.run(p.api_synthesize(text="测试缓存"))
        assert len(p.tts.calls) == first  # 没再调 TTS

    def test_cache_reports_model(self) -> None:
        p = make_plugin()
        import asyncio

        asyncio.run(p.api_synthesize(text="测试缓存"))
        second = asyncio.run(p.api_synthesize(text="测试缓存"))
        assert second["success"] is True

    def test_different_text_misses(self) -> None:
        p = make_plugin()
        import asyncio

        asyncio.run(p.api_synthesize(text="第一句"))
        asyncio.run(p.api_synthesize(text="第二句"))
        assert len(p.tts.calls) == 2

    def test_disabled_cache_always_calls(self) -> None:
        p = make_plugin(audio={"cache_enabled": False})
        import asyncio

        asyncio.run(p.api_synthesize(text="测试缓存"))
        asyncio.run(p.api_synthesize(text="测试缓存"))
        assert len(p.tts.calls) == 2

    def test_failure_not_cached(self) -> None:
        p = make_plugin()
        p.tts = FakeTTS(ok=False, error="boom")
        import asyncio

        asyncio.run(p.api_synthesize(text="测试缓存"))
        p.tts = FakeTTS()
        result = asyncio.run(p.api_synthesize(text="测试缓存"))
        assert result["success"] is True

    def test_gate_uses_cache(self) -> None:
        p = make_plugin(auto={"cooldown_seconds": 0, "max_per_hour": 0})
        import asyncio

        msg = group_msg("今天下班早我买了点水果回来")
        asyncio.run(p.voice_probability_gate(message=msg))
        calls = len(p.tts.calls)
        asyncio.run(p.voice_probability_gate(message=msg))
        assert len(p.tts.calls) == calls

    def test_stats_reported_in_status(self) -> None:
        p = make_plugin()
        import asyncio

        asyncio.run(p.api_synthesize(text="测试缓存"))
        asyncio.run(p.cmd_status(stream_id="g:1", matched_groups={}))
        body = p.ctx.send.calls[-1][1]
        assert "缓存" in body


# ────────────────────────── 排队上限 ──────────────────────────


class TestQueueBudget:
    def test_lock_timeout_returns_busy(self) -> None:
        """锁被占住超过预算时，应快速返回失败而不是一直等。"""
        p = make_plugin(audio={"queue_wait_seconds": 0.05})
        import asyncio

        async def run() -> dict:
            await p._synth_lock.acquire()
            try:
                return await p.api_synthesize(text="排队测试")
            finally:
                p._synth_lock.release()

        result = asyncio.run(run())
        assert result["success"] is False
        assert "排队" in result["error"]

    def test_lock_released_after_success(self) -> None:
        p = make_plugin()
        import asyncio

        asyncio.run(p.api_synthesize(text="正常路径"))
        assert p._synth_lock.locked() is False

    def test_lock_released_after_failure(self) -> None:
        p = make_plugin()
        p.tts = FakeTTS(ok=False, error="boom")
        import asyncio

        asyncio.run(p.api_synthesize(text="失败路径"))
        assert p._synth_lock.locked() is False


class TestCacheKeyStyle:
    """cvcpp 不消费 style，缓存键里不能带它，否则随机风格让缓存永远不命中。"""

    def test_cvcpp_style_ignored_in_key(self) -> None:
        p = make_plugin()
        import asyncio

        p.config.style.style_rotate = True
        asyncio.run(p.api_synthesize(text="同一句话"))
        first = len(p.tts.calls)
        asyncio.run(p.api_synthesize(text="同一句话"))
        assert len(p.tts.calls) == first

    def test_preset_style_in_key(self) -> None:
        p = make_plugin(engine={"voice_mode": "preset"})
        import asyncio

        asyncio.run(p.api_synthesize(text="同一句话", style_instruction="带笑"))
        asyncio.run(p.api_synthesize(text="同一句话", style_instruction="冷淡"))
        assert len(p.tts.calls) == 2
