"""core/tts.py 单元测试（不发真实请求）。"""

from __future__ import annotations

import asyncio

import pytest

from core.tts import TTSService, normalize_audio_base64


class TestNormalize:
    def test_data_uri(self) -> None:
        assert normalize_audio_base64("data:audio/wav;base64,QUJD") == "QUJD"

    def test_plain(self) -> None:
        assert normalize_audio_base64("QUJD") == "QUJD"

    def test_whitespace(self) -> None:
        assert normalize_audio_base64("QU JD\n") == "QUJD"

    def test_padding_restored(self) -> None:
        assert normalize_audio_base64("QUJ") == "QUJ="

    def test_empty(self) -> None:
        assert normalize_audio_base64("") == ""

    def test_idempotent(self) -> None:
        once = normalize_audio_base64("QUJ")
        assert normalize_audio_base64(once) == once


class TestDefaults:
    def test_endpoint_defaults(self) -> None:
        svc = TTSService()
        assert svc.mimo_api_base_url == TTSService.DEFAULT_MIMO_BASE_URL
        assert svc.sf_api_base_url == TTSService.DEFAULT_SF_BASE_URL
        assert svc.cv_endpoint == TTSService.DEFAULT_CV_ENDPOINT

    def test_custom_endpoint(self) -> None:
        svc = TTSService(cv_endpoint="http://10.0.0.5:9000")
        assert svc.cv_endpoint == "http://10.0.0.5:9000"

    def test_strip_keys(self) -> None:
        svc = TTSService(mimo_api_key="  abc  ", sf_api_key=" def ")
        assert svc.mimo_api_key == "abc"
        assert svc.sf_api_key == "def"

    def test_retryable_set(self) -> None:
        assert 429 in TTSService.RETRYABLE_STATUS
        assert 200 not in TTSService.RETRYABLE_STATUS


class TestNoKeyGuards:
    def test_mimo_without_key(self) -> None:
        svc = TTSService()
        result = asyncio.run(svc.synthesize_with_preset(text="你好", voice_id="冰糖"))
        assert result["success"] is False
        assert "Key" in result["error"]

    def test_clone_without_key(self) -> None:
        svc = TTSService()
        result = asyncio.run(
            svc.synthesize_with_voice_clone(text="你好", reference_audio_base64="QUJD")
        )
        assert result["success"] is False

    def test_design_without_key(self) -> None:
        svc = TTSService()
        result = asyncio.run(svc.synthesize_with_design(text="你好", voice_description="甜"))
        assert result["success"] is False


class TestMimoRequestShape:
    """验证按文档构造的请求体结构。"""

    def _capture(self, svc: TTSService) -> dict:
        captured: dict = {}

        async def fake(body: dict, headers: dict) -> dict:
            captured["body"] = body
            captured["headers"] = headers
            return {"success": True, "audio_base64": "data:audio/wav;base64,QUJD"}

        svc._do_request = fake  # type: ignore[assignment]
        return captured

    def test_preset_body(self) -> None:
        svc = TTSService(mimo_api_key="k")
        cap = self._capture(svc)
        asyncio.run(
            svc.synthesize_with_preset(text="你好", voice_id="冰糖", style_instruction="带笑")
        )
        body = cap["body"]
        assert body["model"] == TTSService.MODEL_PRESET
        assert body["stream"] is False
        assert body["modalities"] == ["text", "audio"]
        assert body["audio"]["format"] == "wav"
        assert body["audio"]["voice"] == "冰糖"
        # 文本必须在 assistant
        roles = {m["role"]: m["content"] for m in body["messages"]}
        assert roles["assistant"] == "你好"
        assert roles["user"] == "带笑"
        assert cap["headers"]["api-key"] == "k"

    def test_preset_speed_omitted_when_zero(self) -> None:
        svc = TTSService(mimo_api_key="k")
        cap = self._capture(svc)
        asyncio.run(svc.synthesize_with_preset(text="你好", voice_id="冰糖"))
        assert "speed" not in cap["body"]["audio"]

    def test_preset_speed_included(self) -> None:
        svc = TTSService(mimo_api_key="k")
        cap = self._capture(svc)
        asyncio.run(
            svc.synthesize_with_preset(text="你好", voice_id="冰糖", speed=1.15)
        )
        assert cap["body"]["audio"]["speed"] == 1.15

    def test_tag_prefixed_to_assistant(self) -> None:
        svc = TTSService(mimo_api_key="k")
        cap = self._capture(svc)
        asyncio.run(
            svc.synthesize_with_preset(text="你好", voice_id="冰糖", tag="（开心）")
        )
        roles = {m["role"]: m["content"] for m in cap["body"]["messages"]}
        assert roles["assistant"].startswith("（开心）")
        assert roles["assistant"].endswith("你好")

    def test_design_has_no_voice_field(self) -> None:
        svc = TTSService(mimo_api_key="k")
        cap = self._capture(svc)
        asyncio.run(
            svc.synthesize_with_design(text="你好", voice_description="甜妹", style_instruction="带笑")
        )
        body = cap["body"]
        assert body["model"] == TTSService.MODEL_DESIGN
        assert "voice" not in body["audio"]
        roles = {m["role"]: m["content"] for m in body["messages"]}
        # 音色描述与风格都拼进 user
        assert "甜妹" in roles["user"]
        assert "带笑" in roles["user"]

    def test_design_optimize_flag(self) -> None:
        svc = TTSService(mimo_api_key="k")
        cap = self._capture(svc)
        asyncio.run(
            svc.synthesize_with_design(
                text="你好", voice_description="甜妹", optimize_text_preview=True
            )
        )
        assert cap["body"]["audio"]["optimize_text_preview"] is True

    def test_clone_voice_is_data_uri(self) -> None:
        svc = TTSService(mimo_api_key="k")
        cap = self._capture(svc)
        asyncio.run(
            svc.synthesize_with_voice_clone(text="你好", reference_audio_base64="QUJD")
        )
        voice = cap["body"]["audio"]["voice"]
        assert voice.startswith("data:audio/wav;base64,")
        assert voice.endswith("QUJD")

    def test_clone_preserves_data_uri(self) -> None:
        svc = TTSService(mimo_api_key="k")
        cap = self._capture(svc)
        uri = "data:audio/mpeg;base64,QUJD"
        asyncio.run(svc.synthesize_with_voice_clone(text="你好", reference_audio_base64=uri))
        assert cap["body"]["audio"]["voice"] == uri


class TestParseResponse:
    def _svc(self) -> TTSService:
        return TTSService(mimo_api_key="k")

    def test_valid(self) -> None:
        svc = self._svc()
        raw = b'{"choices":[{"message":{"audio":{"data":"QUJD","format":"wav"}}}]}'
        result = asyncio.run(svc._parse_response(raw, {"model": "m"}, 100))
        assert result["success"] is True
        assert result["audio_base64"].endswith("QUJD")

    def test_bad_json(self) -> None:
        svc = self._svc()
        result = asyncio.run(svc._parse_response(b"not json", {}, 100))
        assert result["success"] is False

    def test_no_choices(self) -> None:
        svc = self._svc()
        result = asyncio.run(svc._parse_response(b'{"choices":[]}', {}, 100))
        assert result["success"] is False

    def test_no_audio(self) -> None:
        svc = self._svc()
        raw = b'{"choices":[{"message":{"audio":{}}}]}'
        result = asyncio.run(svc._parse_response(raw, {}, 100))
        assert result["success"] is False


class TestCvcppRequestEncoding:
    """cvcpp 请求体必须是原始 UTF-8，不能是 \\uXXXX 转义。

    背景：aiohttp 的 ``json=`` 参数默认 ``ensure_ascii=True``，中文会变成
    ``\u4eca\u5929``。曾经配套的本地服务端在解码时漏跳四位十六进制数字，
    把「今天」念成「今4eca天4e0b」，音频时长翻三倍。这里锁死编码方式，
    避免以后有人改回 ``json=``。
    """

    @staticmethod
    def _svc() -> TTSService:
        return TTSService(cv_endpoint="http://127.0.0.1:18091")

    def test_posts_raw_utf8_not_escaped(self) -> None:
        svc = self._svc()
        captured: dict = {}

        class FakeResponse:
            status = 200

            async def read(self) -> bytes:
                return b"RIFFfake"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class FakeSession:
            def post(self, url, **kwargs):
                captured["url"] = url
                captured.update(kwargs)
                return FakeResponse()

        async def fake_get_session():
            return FakeSession()

        svc._get_session = fake_get_session  # type: ignore[assignment]
        asyncio.run(svc.synthesize_with_cvcpp("今天下班早"))

        assert "data" in captured, "必须用 data= 传原始字节，不能用 json="
        assert "json" not in captured
        body = captured["data"]
        assert isinstance(body, bytes)
        assert "今天下班早".encode("utf-8") in body, "中文必须原样出现在请求体里"
        assert b"\\u" not in body, "请求体不能含 \\uXXXX 转义"

    def test_speed_serialized_as_number(self) -> None:
        svc = self._svc()
        captured: dict = {}

        class FakeResponse:
            status = 200

            async def read(self) -> bytes:
                return b"RIFFfake"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class FakeSession:
            def post(self, url, **kwargs):
                captured.update(kwargs)
                return FakeResponse()

        async def fake_get_session():
            return FakeSession()

        svc._get_session = fake_get_session  # type: ignore[assignment]
        asyncio.run(svc.synthesize_with_cvcpp("嗯", speed=1.1))
        body = captured["data"].decode("utf-8")
        assert '"speed": 1.1' in body or '"speed":1.1' in body
