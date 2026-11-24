"""TTS 引擎客户端。

支持五种后端：
- ``cvcpp``  : 本地 cosyvoice.cpp HTTP 服务（免费、离线）
- ``sf``     : SiliconFlow OpenAI 兼容 /audio/speech（云端克隆）
- ``clone``  : 小米 MiMo 音色复刻
- ``preset`` : 小米 MiMo 预置音色
- ``design`` : 小米 MiMo 文字设计音色

所有 ``synthesize_*`` 方法返回统一字典：

    {
        "success": bool,
        "audio_base64": "data:audio/wav;base64,...",   # 成功时
        "audio_len": int,                              # 字节数
        "elapsed_ms": int,
        "model": str,
        "status": int | None,                          # HTTP 状态码（失败时）
        "error": str,                                  # 失败原因
        "timeout": bool,                               # 是否超时
    }
"""

from __future__ import annotations

import asyncio
import base64
import gc
import json
import logging
from typing import Any, Optional

import aiohttp


def normalize_audio_base64(value: str) -> str:
    """归一化音频 base64：剥 data: 前缀、删空白、补齐 padding。

    返回纯 base64 字符串，幂等；空输入返回空字符串。
    """
    if not value:
        return ""
    body = value.rsplit("base64,", 1)[-1] if "base64," in value else value
    body = "".join(body.split())
    if not body:
        return ""
    core = body.rstrip("=")
    return core + "=" * ((-len(core)) % 4)


class TTSService:
    """多引擎 TTS 客户端，所有请求共用一条 aiohttp 连接池。"""

    # ---- 小米 MiMo ----
    DEFAULT_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
    MODEL_PRESET = "mimo-v2.5-tts"
    MODEL_CLONE = "mimo-v2.5-tts-voiceclone"
    MODEL_DESIGN = "mimo-v2.5-tts-voicedesign"

    # ---- SiliconFlow ----
    DEFAULT_SF_BASE_URL = "https://api.siliconflow.cn/v1"
    SF_MODEL_COSY = "FunAudioLLM/CosyVoice2-0.5B"
    SF_MODEL_MOSS = "fnlp/MOSS-TTSD-v0.5"

    # ---- 本地 cosyvoice.cpp ----
    DEFAULT_CV_ENDPOINT = "http://127.0.0.1:18091"

    # 可重试的 HTTP 状态码：限流与服务端错误
    RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
    MAX_ATTEMPTS = 2
    BACKOFF_BASE = 2.0
    # 单次合成的总时间预算（含重试），必须小于调用方超时
    TOTAL_BUDGET = 22.0

    def __init__(
        self,
        *,
        mimo_api_key: str = "",
        mimo_api_base_url: str = "",
        sf_api_key: str = "",
        sf_api_base_url: str = "",
        cv_endpoint: str = "",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.mimo_api_key = (mimo_api_key or "").strip()
        self.mimo_api_base_url = (mimo_api_base_url or "").strip() or self.DEFAULT_MIMO_BASE_URL
        self.sf_api_key = (sf_api_key or "").strip()
        self.sf_api_base_url = (sf_api_base_url or "").strip() or self.DEFAULT_SF_BASE_URL
        self.cv_endpoint = (cv_endpoint or "").strip() or self.DEFAULT_CV_ENDPOINT
        self.logger = logger or logging.getLogger(__name__)
        self._session: Optional[aiohttp.ClientSession] = None

    # ────────────────────────── 生命周期 ──────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建复用的 ClientSession。"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=20, connect=8, sock_read=16)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        """关闭连接池，释放资源。"""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    # ────────────────────────── 本地 cvcpp ──────────────────────────

    async def synthesize_with_cvcpp(
        self,
        text: str,
        endpoint: str = "",
        speed: float = 1.1,
        timeout_seconds: float = 110.0,
    ) -> dict[str, Any]:
        """调用本地 cosyvoice.cpp 的 /tts 接口，返回 24kHz 单声道 wav。"""
        base = (endpoint or self.cv_endpoint or self.DEFAULT_CV_ENDPOINT).rstrip("/")
        started = asyncio.get_event_loop().time()
        try:
            session = await self._get_session()
            async with session.post(
                f"{base}/tts",
                data=json.dumps(
                    {"text": text, "speed": float(speed)}, ensure_ascii=False
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(
                    total=timeout_seconds, connect=10, sock_read=timeout_seconds
                ),
            ) as response:
                body = await response.read()
                elapsed_ms = int((asyncio.get_event_loop().time() - started) * 1000)
                if response.status != 200:
                    # 429（队列满）与 503（退化/排队超时）都是「这次发不出来」，
                    # 上游会据此回退成文字，不需要再重试。
                    return {
                        "success": False,
                        "status": response.status,
                        "elapsed_ms": elapsed_ms,
                        "busy": response.status in (429, 503),
                        "error": f"cvcpp 返回 {response.status}: {body[:200]!r}",
                    }
                if not body:
                    return {"success": False, "elapsed_ms": elapsed_ms, "error": "空音频响应"}
                return {
                    "success": True,
                    "audio_base64": f"data:audio/wav;base64,{base64.b64encode(body).decode()}",
                    "audio_len": len(body),
                    "elapsed_ms": elapsed_ms,
                    "model": "cosyvoice3-cvcpp",
                }
        except asyncio.TimeoutError:
            return {"success": False, "error": "cvcpp 合成超时", "timeout": True}
        except aiohttp.ClientError as exc:
            return {"success": False, "error": f"网络错误: {type(exc).__name__}: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    async def check_cvcpp_health(self, endpoint: str = "", timeout_seconds: float = 5.0) -> dict[str, Any]:
        """探测本地服务是否就绪，供 /语音 状态命令使用。"""
        base = (endpoint or self.cv_endpoint or self.DEFAULT_CV_ENDPOINT).rstrip("/")
        try:
            session = await self._get_session()
            async with session.get(
                f"{base}/health",
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as response:
                raw = await response.read()
                try:
                    data = json.loads(raw or b"{}")
                except Exception:  # noqa: BLE001
                    data = {"raw": raw[:120].decode("utf-8", "replace")}
                return {"ok": response.status == 200, "status": response.status, "data": data}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "status": None, "data": {}, "error": f"{type(exc).__name__}: {exc}"}

    # ────────────────────────── SiliconFlow ──────────────────────────

    async def sf_upload_voice(
        self,
        audio_bytes: bytes,
        prompt_text: str,
        custom_name: str,
        model: str = "",
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]:
        """上传参考音频建立克隆音色，返回 ``{"success", "uri"}``。

        ``prompt_text`` 必须与参考音频逐字一致，否则合成时会先念出参考音频内容。
        """
        if not self.sf_api_key:
            return {"success": False, "error": "未配置 SiliconFlow API Key"}
        url = f"{self.sf_api_base_url.rstrip('/')}/uploads/audio/voice"
        try:
            session = await self._get_session()
            form = aiohttp.FormData()
            form.add_field(
                "file", audio_bytes, filename=f"{custom_name}.wav", content_type="audio/wav"
            )
            form.add_field("model", model or self.SF_MODEL_COSY)
            form.add_field("customName", custom_name)
            form.add_field("text", prompt_text)
            async with session.post(
                url,
                data=form,
                headers={"Authorization": f"Bearer {self.sf_api_key}"},
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as response:
                raw = await response.read()
                if response.status != 200:
                    return {
                        "success": False,
                        "status": response.status,
                        "error": f"上传失败 {response.status}: {raw[:300]!r}",
                    }
                data = json.loads(raw or b"{}")
                uri = str(data.get("uri") or "").strip()
                if not uri:
                    return {"success": False, "error": f"响应缺少 uri: {data}"}
                return {"success": True, "uri": uri}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    async def synthesize_with_sf_clone(
        self,
        text: str,
        voice_uri: str,
        model: str = "",
        audio_format: str = "wav",
        sample_rate: int = 32000,
        speed: float = 1.0,
    ) -> dict[str, Any]:
        """SiliconFlow /audio/speech 合成。

        注意：``instructions`` 参数会被服务端静默忽略，语气只能靠文本本身。
        """
        if not self.sf_api_key:
            return {"success": False, "error": "未配置 SiliconFlow API Key"}
        if not voice_uri:
            return {"success": False, "error": "未配置 SiliconFlow 音色 URI"}
        url = f"{self.sf_api_base_url.rstrip('/')}/audio/speech"
        body = {
            "model": model or self.SF_MODEL_COSY,
            "voice": voice_uri,
            "input": text,
            "response_format": audio_format,
            "sample_rate": int(sample_rate),
            "speed": float(speed),
        }
        started = asyncio.get_event_loop().time()
        try:
            session = await self._get_session()
            async with session.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {self.sf_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=self.TOTAL_BUDGET, connect=8),
            ) as response:
                raw = await response.read()
                elapsed_ms = int((asyncio.get_event_loop().time() - started) * 1000)
                if response.status != 200:
                    return {
                        "success": False,
                        "status": response.status,
                        "elapsed_ms": elapsed_ms,
                        "error": f"SiliconFlow 返回 {response.status}: {raw[:200]!r}",
                    }
                if not raw:
                    return {"success": False, "elapsed_ms": elapsed_ms, "error": "空音频响应"}
                mime = "audio/wav" if audio_format == "wav" else f"audio/{audio_format}"
                return {
                    "success": True,
                    "audio_base64": f"data:{mime};base64,{base64.b64encode(raw).decode()}",
                    "audio_len": len(raw),
                    "elapsed_ms": elapsed_ms,
                    "model": body["model"],
                }
        except asyncio.TimeoutError:
            return {"success": False, "error": "SiliconFlow 合成超时", "timeout": True}
        except aiohttp.ClientError as exc:
            return {"success": False, "error": f"网络错误: {type(exc).__name__}: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    # ────────────────────────── 小米 MiMo ──────────────────────────

    async def synthesize_with_preset(
        self,
        text: str,
        voice_id: str,
        style_instruction: str = "",
        audio_format: str = "wav",
        *,
        speed: float = 0.0,
        tag: str = "",
    ) -> dict[str, Any]:
        """MiMo 预置音色合成。

        按官方文档：目标文本放 ``role: assistant``，风格指令放 ``role: user``；
        音频标签（如 ``（开心）``）写在 assistant 内容开头。
        """
        return await self._mimo_request(
            model=self.MODEL_PRESET,
            text=text,
            style_instruction=style_instruction,
            audio_format=audio_format,
            voice=voice_id,
            speed=speed,
            tag=tag,
        )

    async def synthesize_with_voice_clone(
        self,
        text: str,
        reference_audio_base64: str,
        style_instruction: str = "",
        audio_format: str = "wav",
        *,
        speed: float = 0.0,
        tag: str = "",
    ) -> dict[str, Any]:
        """MiMo 音色复刻。

        ``audio.voice`` 必填，只接受 mp3/wav 的 base64；编码后不能超过 10 MB。
        """
        voice = (
            reference_audio_base64
            if reference_audio_base64.startswith("data:")
            else f"data:audio/wav;base64,{reference_audio_base64}"
        )
        return await self._mimo_request(
            model=self.MODEL_CLONE,
            text=text,
            style_instruction=style_instruction,
            audio_format=audio_format,
            voice=voice,
            speed=speed,
            tag=tag,
        )

    async def synthesize_with_design(
        self,
        text: str,
        voice_description: str,
        style_instruction: str = "",
        audio_format: str = "wav",
        *,
        speed: float = 0.0,
        tag: str = "",
        optimize_text_preview: bool = False,
    ) -> dict[str, Any]:
        """MiMo 文字设计音色。

        voicedesign 不支持 ``audio.voice``，音色描述必须写进 user 消息；
        ``optimize_text_preview`` 只在 voicedesign 上有效。
        """
        head = "；".join(x for x in (voice_description, style_instruction) if x)
        return await self._mimo_request(
            model=self.MODEL_DESIGN,
            text=text,
            style_instruction=head,
            audio_format=audio_format,
            voice="",
            speed=speed,
            tag=tag,
            optimize_text_preview=optimize_text_preview,
        )

    async def _mimo_request(
        self,
        *,
        model: str,
        text: str,
        style_instruction: str,
        audio_format: str,
        voice: str,
        speed: float = 0.0,
        tag: str = "",
        optimize_text_preview: bool = False,
    ) -> dict[str, Any]:
        """构造并发送 MiMo 的 chat/completions 请求。

        文档要点（https://mimo.mi.com/docs/zh-CN/api/audio/tts-post）：
        - 目标文本必须在 ``role: assistant`` 的消息里，放 user 会被念成指令；
        - ``role: user`` 是可选的自然语言风格指令，voicedesign 时必填；
        - ``audio.format`` 支持 wav / mp3 / pcm / pcm16；
        - ``audio.voice`` 预置音色为 ID，复刻为音频 base64，voicedesign 不支持；
        - ``audio.optimize_text_preview`` 仅 voicedesign 支持；
        - 流式输出必须用 pcm16，本插件走非流式，保持 wav。
        """
        if not self.mimo_api_key:
            return {"success": False, "error": "未配置 MiMo API Key"}

        speak = f"{tag}{text}" if tag else text
        messages: list[dict[str, str]] = []
        if style_instruction or model == self.MODEL_DESIGN:
            messages.append({"role": "user", "content": style_instruction or ""})
        messages.append({"role": "assistant", "content": speak})

        audio: dict[str, Any] = {"format": audio_format}
        if voice:
            audio["voice"] = voice
        if speed > 0:
            audio["speed"] = float(speed)
        if optimize_text_preview and model == self.MODEL_DESIGN:
            audio["optimize_text_preview"] = True

        body: dict[str, Any] = {
            "model": model,
            "modalities": ["text", "audio"],
            "messages": messages,
            "audio": audio,
            "stream": False,
        }
        headers = {"api-key": self.mimo_api_key, "Content-Type": "application/json"}
        result = await self._do_request(body, headers)
        result.setdefault("model", model)
        return result

    async def _do_request(self, body: dict[str, Any], headers: dict[str, Any]) -> dict[str, Any]:
        """带重试与总预算控制的请求。

        限流（429）与服务端错误会退避重试一次；其它错误直接返回。
        """
        url = f"{self.mimo_api_base_url.rstrip('/')}/chat/completions"
        started = asyncio.get_event_loop().time()
        last: dict[str, Any] = {"success": False, "error": "未发起请求"}

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            elapsed = asyncio.get_event_loop().time() - started
            remaining = self.TOTAL_BUDGET - elapsed
            if remaining <= 1.0:
                return {
                    "success": False,
                    "error": f"时间预算耗尽（已用 {elapsed:.1f}s）",
                    "timeout": True,
                }
            try:
                session = await self._get_session()
                async with session.post(
                    url,
                    json=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(
                        total=min(remaining, 20.0), connect=min(remaining, 8.0)
                    ),
                ) as response:
                    raw = await response.read()
                    elapsed_ms = int((asyncio.get_event_loop().time() - started) * 1000)
                    if response.status == 200:
                        return await self._parse_response(raw, body, elapsed_ms)
                    last = {
                        "success": False,
                        "status": response.status,
                        "elapsed_ms": elapsed_ms,
                        "error": f"API 返回 {response.status}: {raw[:200]!r}",
                    }
                    if response.status not in self.RETRYABLE_STATUS or attempt >= self.MAX_ATTEMPTS:
                        return last
                    delay = self.BACKOFF_BASE * (2 ** (attempt - 1))
                    if asyncio.get_event_loop().time() - started + delay + 3.0 > self.TOTAL_BUDGET:
                        return last
                    self.logger.warning(
                        "TTS %s 返回 %s，%.1fs 后重试", model_name(body), response.status, delay
                    )
                    await asyncio.sleep(delay)
            except asyncio.TimeoutError:
                return {"success": False, "error": "TTS 请求超时", "timeout": True}
            except aiohttp.ClientError as exc:
                last = {"success": False, "error": f"网络错误: {type(exc).__name__}: {exc}"}
                if attempt >= self.MAX_ATTEMPTS:
                    return last
            except Exception as exc:  # noqa: BLE001
                return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

        return last

    async def _parse_response(
        self, raw: bytes, body: dict[str, Any], elapsed_ms: int
    ) -> dict[str, Any]:
        """解析 MiMo 的 chat/completions 响应，抽出 audio.data 里的 base64。"""
        try:
            data = json.loads(raw or b"{}")
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "elapsed_ms": elapsed_ms, "error": f"响应不是 JSON: {exc}"}

        choices = data.get("choices") or []
        if not choices:
            return {
                "success": False,
                "elapsed_ms": elapsed_ms,
                "error": f"响应缺少 choices: {str(data)[:200]}",
            }
        message = choices[0].get("message") or {}
        audio = message.get("audio") or {}
        b64 = normalize_audio_base64(str(audio.get("data") or ""))
        if not b64:
            return {"success": False, "elapsed_ms": elapsed_ms, "error": "响应中没有音频数据"}
        fmt = str(audio.get("format") or body.get("audio", {}).get("format") or "wav")
        return {
            "success": True,
            "audio_base64": f"data:audio/{fmt};base64,{b64}",
            "audio_len": len(b64) * 3 // 4,
            "elapsed_ms": elapsed_ms,
            "model": str(body.get("model") or ""),
        }


def model_name(body: dict[str, Any]) -> str:
    """从请求体取模型名，仅用于日志。"""
    return str(body.get("model") or "?")
