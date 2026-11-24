"""MaiBot AI 语音回复插件。

把机器人的文字回复自动转成语音发送。支持本地离线 TTS、云端克隆、频率控制、
群排除、内容过滤与音频后处理。

入口约定：``create_plugin()`` 返回插件实例；组件通过 ``@Tool`` / ``@HookHandler`` /
``@Command`` / ``@API`` / ``@HomeCard`` 声明，由 Runner 自动收集。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from maibot_sdk import (
    API,
    Command,
    ConfigWidget,
    ConfigWidgetState,
    HomeCard,
    HookHandler,
    MaiBotPlugin,
    Tool,
)
from maibot_sdk.types import (
    ActivationType,
    ErrorPolicy,
    HookMode,
    HookOrder,
    ToolParameterInfo,
    ToolParamType,
)

from .core import accent_guard, audio_post, text as text_utils
from .core.cache import AudioCache
from .core.config import VoicePluginConfig
from .core.ratelimit import RateLimiter
from .core.tts import TTSService, normalize_audio_base64

PLUGIN_DIR = Path(__file__).resolve().parent


class VoiceReplyPlugin(MaiBotPlugin):
    """语音回复插件主类。"""

    config_model = VoicePluginConfig

    # 配置页运行时控件：一个健康探测按钮
    config_widgets = [
        ConfigWidget(
            id="health_check",
            kind="action",
            label="探测 TTS 服务",
            confirmation="将向当前引擎发送一次极短合成请求，可能产生少量费用。",
        )
    ]

    def __init__(self) -> None:
        super().__init__()
        self.tts: Optional[TTSService] = None
        self.voices: Dict[str, str] = {}
        self.default_voice: str = ""
        self.limiter = RateLimiter()
        self._skip_regex = None
        self._skip_source = ""
        self._sf_uploaded: Dict[str, str] = {}
        self._last_health: Dict[str, Any] = {}
        self._cache: Optional[AudioCache] = None
        # 本地引擎是单进程串行推理，并发请求只会互相拖慢，这里排队
        self._synth_lock = asyncio.Lock()

    # ────────────────────────── 生命周期 ──────────────────────────

    async def on_load(self) -> None:
        """初始化 TTS 客户端、加载参考音频、编译跳过正则。"""
        self._ensure_config_file()
        engine = self.config.engine
        self.tts = TTSService(
            mimo_api_key=engine.mimo_api_key,
            mimo_api_base_url=engine.mimo_api_base_url,
            sf_api_key=engine.sf_api_key,
            sf_api_base_url=engine.sf_api_base_url,
            cv_endpoint=engine.cv_endpoint,
            logger=self.ctx.logger,
        )
        await self._load_voices()
        self.default_voice = self._resolve_default_voice()
        self._refresh_skip_regex()
        self._cache = self._build_cache()

        self.ctx.logger.info(
            "语音插件已加载：引擎=%s，默认音色=%s，参考音频=%d 个",
            engine.voice_mode,
            self.default_voice or "(空)",
            len(self.voices),
        )
        if engine.voice_mode == "cvcpp":
            health = await self.tts.check_cvcpp_health()
            if health.get("ok"):
                self.ctx.logger.info("本地 TTS 服务就绪：%s", health.get("data"))
            else:
                self.ctx.logger.warning(
                    "本地 TTS 服务不可用（%s）：%s",
                    engine.cv_endpoint,
                    health.get("error") or health.get("status"),
                )

    async def on_unload(self) -> None:
        """关闭连接池，释放音频缓存。"""
        if self.tts is not None:
            await self.tts.close()
            self.tts = None
        self.voices.clear()
        if self._cache is not None:
            self._cache.clear()
            self._cache = None
        self.ctx.logger.info("语音插件已卸载")

    async def on_config_update(
        self, scope: str, config_data: dict[str, object], version: str
    ) -> None:
        """热更新配置后同步内部状态。"""
        del config_data, version
        if scope != "self":
            return
        engine = self.config.engine
        if self.tts is not None:
            self.tts.mimo_api_key = engine.mimo_api_key.strip()
            self.tts.mimo_api_base_url = (
                engine.mimo_api_base_url.strip() or TTSService.DEFAULT_MIMO_BASE_URL
            )
            self.tts.sf_api_key = engine.sf_api_key.strip()
            self.tts.sf_api_base_url = (
                engine.sf_api_base_url.strip() or TTSService.DEFAULT_SF_BASE_URL
            )
            self.tts.cv_endpoint = engine.cv_endpoint.strip() or TTSService.DEFAULT_CV_ENDPOINT
        self._sf_uploaded.clear()
        await self._load_voices()
        self.default_voice = self._resolve_default_voice()
        self._refresh_skip_regex()
        self._cache = self._build_cache()
        self.ctx.logger.info(
            "配置已热更新：引擎=%s，默认音色=%s", engine.voice_mode, self.default_voice or "(空)"
        )

    def _build_cache(self) -> Optional[AudioCache]:
        """按当前配置构造音频缓存；关闭时返回 None。"""
        cfg = self.config.audio
        if not cfg.cache_enabled or cfg.cache_max_mb <= 0:
            return None
        return AudioCache(
            max_bytes=int(cfg.cache_max_mb) * 1024 * 1024,
            ttl_seconds=int(cfg.cache_ttl_seconds),
        )

    def _ensure_config_file(self) -> None:
        """首次运行时从 config.example.toml 生成 config.toml，不覆盖已有文件。"""
        target = PLUGIN_DIR / "config.toml"
        example = PLUGIN_DIR / "config.example.toml"
        if target.exists() or not example.exists():
            return
        try:
            target.write_bytes(example.read_bytes())
            self.ctx.logger.info("已从 config.example.toml 生成 config.toml")
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("生成 config.toml 失败：%s", exc)

    # ────────────────────────── 参考音频 ──────────────────────────

    async def _load_voices(self) -> None:
        """扫描 voices 目录，把音频读成 data URI 缓存起来。"""
        voices_dir = PLUGIN_DIR / (self.config.engine.voices_dir or "voices")
        self.voices.clear()
        if not voices_dir.exists():
            try:
                voices_dir.mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("创建参考音频目录失败：%s", exc)
                return
            self.ctx.logger.info("已创建参考音频目录：%s", voices_dir)
            return

        files = sorted(voices_dir.glob("*.wav")) + sorted(voices_dir.glob("*.mp3"))
        for path in files:
            mime = "audio/wav" if path.suffix.lower() == ".wav" else "audio/mpeg"
            try:
                raw = path.read_bytes()
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("读取参考音频失败 %s：%s", path.name, exc)
                continue
            self.voices[path.stem] = (
                f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
            )
            self.ctx.logger.debug(
                "已加载参考音频 %s（%d KB）", path.stem, len(raw) // 1024
            )

    def _resolve_default_voice(self) -> str:
        """按当前引擎推导默认音色标识。"""
        engine = self.config.engine
        mode = engine.voice_mode
        if engine.default_voice.strip():
            return engine.default_voice.strip()
        if mode == "cvcpp":
            return engine.cv_endpoint
        if mode == "sf":
            return engine.sf_voice_uri or engine.sf_ref_audio
        if mode == "design":
            return engine.design_voice_description
        if mode == "clone":
            return engine.clone_voice or (next(iter(self.voices), ""))
        return engine.preset_voice or "mimo_default"

    def _resolve_voice(self, voice_name: str = "") -> tuple[str, str, str]:
        """解析出 ``(音色标识, 参考音频 data URI, 模式)``。

        Args:
            voice_name: 显式指定的音色名，留空用默认音色。
        """
        engine = self.config.engine
        mode = engine.voice_mode

        if mode == "cvcpp":
            return (engine.cv_endpoint, "", "cvcpp")

        if mode == "sf":
            uri = engine.sf_voice_uri.strip()
            ref = (voice_name or engine.sf_ref_audio).strip()
            if not uri and ref:
                cached = self._sf_uploaded.get(ref)
                if cached:
                    uri = cached
            return (uri, self.voices.get(ref, ""), "sf")

        if mode == "clone":
            name = (voice_name or engine.clone_voice).strip()
            ref = self.voices.get(name, "")
            if not ref:
                self.ctx.logger.error("复刻模式找不到参考音频：%s", name or "(未配置)")
                return ("", "", "")
            return (name, ref, "clone")

        if mode == "design":
            desc = (voice_name or engine.design_voice_description).strip()
            if not desc:
                self.ctx.logger.error("文字设计模式需要填写音色描述")
                return ("", "", "")
            return (desc, "", "design")

        voice = (voice_name or engine.preset_voice).strip()
        return (voice or "mimo_default", "", "preset")

    def _pick_style(self) -> str:
        """从配置的风格列表里挑一条。"""
        style_cfg = self.config.style
        options = [s.strip() for s in (style_cfg.style or "").split("|") if s.strip()]
        if not options:
            return ""
        if len(options) == 1 or not style_cfg.style_rotate:
            return options[0]
        return random.choice(options)

    def _refresh_skip_regex(self) -> None:
        """按当前配置重新编译跳过正则。"""
        pattern = self.config.filter.skip_patterns or ""
        if pattern == self._skip_source:
            return
        self._skip_source = pattern
        self._skip_regex = text_utils.compile_skip_regex(pattern)
        if pattern.strip() and self._skip_regex is None:
            self.ctx.logger.warning("跳过正则无法编译，已忽略：%r", pattern)

    # ────────────────────────── 合成与发送 ──────────────────────────

    async def _synthesize(
        self, text: str, voice_key: str, ref_audio: str, mode: str, style: str
    ) -> Dict[str, Any]:
        """按引擎分发到具体的合成方法。"""
        if self.tts is None:
            return {"success": False, "error": "TTS 客户端未初始化"}
        engine = self.config.engine

        if mode == "cvcpp":
            return await self.tts.synthesize_with_cvcpp(
                text=text,
                endpoint=voice_key,
                speed=engine.cv_speed,
                timeout_seconds=engine.cv_timeout_seconds,
            )
        if mode == "sf":
            return await self.tts.synthesize_with_sf_clone(
                text=text,
                voice_uri=voice_key,
                model=engine.sf_model,
                sample_rate=engine.sf_sample_rate,
            )
        if mode == "clone":
            return await self.tts.synthesize_with_voice_clone(
                text=text,
                reference_audio_base64=ref_audio,
                style_instruction=style,
                speed=engine.mimo_speed,
                tag=engine.mimo_tag,
            )
        if mode == "design":
            return await self.tts.synthesize_with_design(
                text=text,
                voice_description=voice_key,
                style_instruction=style,
                speed=engine.mimo_speed,
                tag=engine.mimo_tag,
                optimize_text_preview=engine.mimo_optimize_text_preview,
            )
        return await self.tts.synthesize_with_preset(
            text=text,
            voice_id=voice_key,
            style_instruction=style,
            speed=engine.mimo_speed,
            tag=engine.mimo_tag,
        )

    async def _ensure_sf_voice(self, ref_name: str) -> str:
        """确保 SiliconFlow 音色已建立，返回 voice URI。"""
        engine = self.config.engine
        if engine.sf_voice_uri.strip():
            return engine.sf_voice_uri.strip()
        if not ref_name:
            return ""
        cached = self._sf_uploaded.get(ref_name)
        if cached:
            return cached
        path = PLUGIN_DIR / (engine.voices_dir or "voices") / f"{ref_name}.wav"
        if not path.exists():
            path = PLUGIN_DIR / (engine.voices_dir or "voices") / f"{ref_name}.mp3"
        if not path.exists():
            self.ctx.logger.error("SiliconFlow 参考音频不存在：%s", ref_name)
            return ""
        if not engine.sf_prompt_text.strip():
            self.ctx.logger.error("SiliconFlow 建音色需要填写参考音频的逐字文本")
            return ""
        if self.tts is None:
            return ""
        result = await self.tts.sf_upload_voice(
            audio_bytes=path.read_bytes(),
            prompt_text=engine.sf_prompt_text.strip(),
            custom_name=ref_name,
            model=engine.sf_model,
        )
        if not result.get("success"):
            self.ctx.logger.error("SiliconFlow 建音色失败：%s", result.get("error"))
            return ""
        uri = str(result["uri"])
        self._sf_uploaded[ref_name] = uri
        self.ctx.logger.info(
            "SiliconFlow 音色已建立：%s（请把 URI 填进配置的 sf_voice_uri 以免重复上传）", uri
        )
        return uri

    async def synthesize_with_retry(
        self, text: str, style_instruction: str = "", voice_name: str = ""
    ) -> Dict[str, Any]:
        """合成语音：先查缓存，未命中再合成，可选口音重试。

        口音守卫只对 24kHz 以上的 WAV 生效；重试次数由 ``audio.accent_retry`` 控制，
        遇到限流/服务端错误会立即放弃而不是继续重试（避免放大 429）。
        本地引擎是单进程串行推理，并发请求只会互相拖慢，所以整体加锁排队。
        """
        audio_cfg = self.config.audio
        voice_key, ref_audio, mode = self._resolve_voice(voice_name)
        if not mode:
            return {"success": False, "error": "音色解析失败，请检查配置"}

        if mode == "sf" and not voice_key:
            voice_key = await self._ensure_sf_voice(
                voice_name or self.config.engine.sf_ref_audio
            )
            if not voice_key:
                return {"success": False, "error": "SiliconFlow 音色建立失败"}

        cache = self._cache
        # 只有真正消费 style 的引擎才把它算进缓存键。cvcpp 的 /tts 只收 text 与
        # speed，风格参数根本传不过去；把随机轮换的风格算进键里会让同文本永远
        # 命中不了缓存，白等十几秒。
        style_key = style_instruction if mode in ("clone", "preset", "design") else ""
        cache_key = (
            AudioCache.make_key(text, style_key, voice_key)
            if cache is not None
            else ""
        )
        if cache is not None:
            hit = cache.get(cache_key)
            if hit:
                self.ctx.logger.info(
                    "音频缓存命中：%d 字，%d KB，命中率 %.1f%%",
                    len(text), len(hit) // 1024, cache.stats()["hit_rate"] * 100,
                )
                return {
                    "success": True,
                    "audio_base64": "data:audio/wav;base64," + base64.b64encode(hit).decode(),
                    "audio_len": len(hit),
                    "elapsed_ms": 0,
                    "model": "cache",
                    "cached": True,
                }

        # 本地引擎单进程串行，锁上排队的请求会互相拖成几分钟。上游钩子只有
        # 130s，排太久注定赶不上，直接放弃让本条回退成文字。
        queue_budget = max(5.0, float(audio_cfg.queue_wait_seconds))
        try:
            await asyncio.wait_for(self._synth_lock.acquire(), timeout=queue_budget)
        except asyncio.TimeoutError:
            self.ctx.logger.info("合成排队超过 %.0fs，本条回退文字", queue_budget)
            return {"success": False, "error": "合成排队超时", "busy": True}
        try:
            # 排队期间可能已有同文本请求填了缓存，再查一次
            if cache is not None:
                hit = cache.get(cache_key)
                if hit:
                    return {
                        "success": True,
                        "audio_base64": "data:audio/wav;base64," + base64.b64encode(hit).decode(),
                        "audio_len": len(hit),
                        "elapsed_ms": 0,
                        "model": "cache",
                        "cached": True,
                    }
            result = await self._synthesize_locked(
                text, voice_key, ref_audio, mode, style_instruction
            )
            if cache is not None and result.get("success") and result.get("audio_base64"):
                raw = _decode_audio(result["audio_base64"])
                if raw:
                    cache.put(cache_key, raw)
            return result
        finally:
            self._synth_lock.release()

    async def _synthesize_locked(
        self, text: str, voice_key: str, ref_audio: str, mode: str, style_instruction: str
    ) -> Dict[str, Any]:
        """真正的合成循环，调用方必须已持有 ``_synth_lock``。"""
        audio_cfg = self.config.audio
        attempts = max(1, int(audio_cfg.accent_retry) + 1)
        best: Dict[str, Any] = {"success": False, "error": "未发起合成"}
        best_risk = float("inf")

        for attempt in range(1, attempts + 1):
            result = await self._synthesize(text, voice_key, ref_audio, mode, style_instruction)
            if not result.get("success") or not result.get("audio_base64"):
                status = result.get("status")
                if status in TTSService.RETRYABLE_STATUS or "429" in str(result.get("error", "")):
                    self.ctx.logger.warning(
                        "合成因限流/服务端错误中断，不再重试：%s", result.get("error")
                    )
                    return result
                if attempt >= attempts:
                    return result
                continue

            if audio_cfg.accent_retry <= 0:
                return result

            raw = _decode_audio(result["audio_base64"])
            metrics = accent_guard.analyze(raw) if raw else None
            if not metrics:
                return result
            risk = float(metrics.get("risk", 0.0))
            result["accent"] = metrics
            if risk < best_risk:
                best, best_risk = result, risk
            if risk <= audio_cfg.accent_risk_max:
                return result
            self.ctx.logger.info(
                "口音风险 %.2f 超过阈值 %.2f，第 %d/%d 次重试",
                risk, audio_cfg.accent_risk_max, attempt, attempts,
            )

        self.ctx.logger.info("口音重试结束，采用最优样本（风险 %.2f）", best_risk)
        return best

    def _post_process(self, audio_b64: str, result: Dict[str, Any]) -> str:
        """发送前做滤波、峰值限制与响度归一化；失败时原样返回。"""
        audio_cfg = self.config.audio
        if audio_cfg.peak_db >= 0:
            return audio_b64
        try:
            raw = _decode_audio(audio_b64)
            if not raw:
                return audio_b64
            fixed, info = audio_post.normalize_wav_bytes(
                raw,
                peak_db=audio_cfg.peak_db,
                rms_db=audio_cfg.rms_db,
                denoise=audio_cfg.denoise,
            )
            result["audio_info"] = info
            if not info.get("applied"):
                self.ctx.logger.info("音频后处理跳过：%s", info.get("error", ""))
                return audio_b64
            self.ctx.logger.info(
                "音频后处理：峰值 %.2f→%.2f dB，响度 %.2f→%.2f dB，增益 %.2f，降噪=%s",
                info["peak_before_db"], info["peak_after_db"],
                info["rms_before_db"], info["rms_after_db"],
                info["gain"], info.get("denoised", False),
            )
            return base64.b64encode(fixed).decode()
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("音频后处理失败，使用原始音频：%s", exc)
            return audio_b64

    async def _send_voice(self, audio_b64: str, stream_id: str) -> bool:
        """发送语音消息。传入裸 base64，MaiBot 会包装成 VoiceComponent。"""
        try:
            sent = await self.ctx.send.custom("voice", audio_b64, stream_id)
            self.ctx.logger.info("语音已发送：stream=%s，结果=%s", stream_id, sent)
            return bool(sent)
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("语音发送失败：%s", exc)
            return False

    async def _resend_emoji(self, message: Dict[str, Any], stream_id: str) -> int:
        """把原消息里的表情包补发出去，返回成功条数。"""
        raw = message.get("raw_message")
        if not isinstance(raw, list):
            return 0
        sent = 0
        for item in raw:
            if not isinstance(item, dict) or str(item.get("type") or "") != "emoji":
                continue
            b64 = str(item.get("binary_data_base64") or "").strip()
            if not b64:
                continue
            try:
                if await self.ctx.send.custom("emoji", b64, stream_id):
                    sent += 1
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("补发表情失败：%s", exc)
        if sent:
            self.ctx.logger.info("已补发表情 %d 个：stream=%s", sent, stream_id)
        return sent

    # ────────────────────────── Tool ──────────────────────────

    @Tool(
        "send_voice_reply",
        brief_description="用语音回复当前对话",
        detailed_description=(
            "把一段文本合成语音并发送到当前会话。\n\n"
            "什么时候用：对方明确要求「说给我听」「发语音」，或这条回复本身适合用声音表达情绪。\n"
            "什么时候不用：内容含链接、代码、长数字列表、大段英文，或字数超过配置上限——这些念出来很难听。\n\n"
            "reply_text 直接写你要说的话，标点自然即可，系统会自动补句号、清 Markdown。\n"
            "style_instruction 可选，用自然语言描述语气，例如「带点笑意，语速偏快」。"
        ),
        parameters=[
            ToolParameterInfo(
                name="reply_text",
                param_type=ToolParamType.STRING,
                description="要说的话。不要包含 Markdown、链接或大段英文。",
                required=True,
            ),
            ToolParameterInfo(
                name="msg_id",
                param_type=ToolParamType.STRING,
                description="当前消息 ID，用于定位会话",
                required=True,
            ),
            ToolParameterInfo(
                name="style_instruction",
                param_type=ToolParamType.STRING,
                description="可选的语气描述，留空则用配置里的轮换风格",
                required=False,
            ),
        ],
        activation_type=ActivationType.ALWAYS,
        timeout_ms=180000,
    )
    async def send_voice_reply(
        self,
        reply_text: str,
        msg_id: str = "",
        style_instruction: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """语音回复工具。本地引擎合成慢，超时设 180 秒。"""
        del msg_id
        stream_id = _find_stream_id(kwargs)
        if not stream_id:
            return {"success": False, "error": "无法定位会话，请直接用 reply 工具发文字"}

        body = (reply_text or "").strip()
        if not body:
            return {"success": False, "error": "文本为空"}

        max_chars = int(self.config.auto.max_chars)
        if max_chars > 0 and len(body) > max_chars:
            return {
                "success": False,
                "error": f"文本 {len(body)} 字超过语音上限 {max_chars} 字，本轮请直接发文字",
            }

        cleaned = text_utils.clean_for_tts(
            body,
            fix_punctuation=self.config.style.clean_punctuation,
            strip_latin=self.config.style.strip_latin,
        )
        if not cleaned:
            return {"success": False, "error": "清洗后没有可朗读内容"}

        style = style_instruction.strip() or self._pick_style()
        result = await self.synthesize_with_retry(cleaned, style)
        if not result.get("success") or not result.get("audio_base64"):
            return {"success": False, "error": result.get("error", "合成失败")}

        audio = self._post_process(result["audio_base64"], result)
        if not await self._send_voice(audio, stream_id):
            return {"success": False, "error": "语音发送失败"}

        return {
            "success": True,
            "message": "语音已发送",
            "chars": len(cleaned),
            "elapsed_ms": result.get("elapsed_ms"),
            "engine": self.config.engine.voice_mode,
        }

    # ────────────────────────── Hook ──────────────────────────

    @HookHandler(
        "send_service.before_send",
        name="voice_probability_gate",
        description="按配置概率把文字回复改发语音，不依赖模型主动调用工具",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        timeout_ms=130000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def voice_probability_gate(self, **kwargs: Any) -> Dict[str, Any]:
        """在发送前按概率把文字改成语音。

        顺序：先合成并发送，确认成功后才 abort 掉原文字；任何一步失败都返回
        continue，保证用户至少能收到文字。
        """
        cfg = self.config
        if not cfg.plugin.enabled or cfg.auto.probability <= 0 or self.tts is None:
            return {"action": "continue"}

        message = kwargs.get("message")
        if not isinstance(message, dict):
            return {"action": "continue"}

        body = str(message.get("processed_plain_text") or "").strip()
        session_id = str(message.get("session_id") or "").strip()
        if not body or not session_id:
            return {"action": "continue"}

        if len(body) < cfg.auto.min_chars:
            return {"action": "continue"}
        if cfg.auto.max_chars > 0 and len(body) > cfg.auto.max_chars:
            return {"action": "continue"}

        message_info = message.get("message_info")
        is_group = isinstance(message_info, dict) and bool(message_info.get("group_info"))
        if is_group and not cfg.auto.in_group:
            return {"action": "continue"}
        if not is_group and not cfg.auto.in_private:
            return {"action": "continue"}

        if is_group:
            group_id = str((message_info.get("group_info") or {}).get("group_id") or "").strip()
            excluded = text_utils.parse_csv(cfg.auto.exclude_groups)
            if group_id and group_id in excluded:
                self.ctx.logger.info("跳过语音：群 %s 在排除名单", group_id)
                return {"action": "continue"}

        reason = text_utils.blocked_reason(
            body,
            message,
            skip_regex=self._skip_regex,
            min_chinese_ratio=cfg.filter.min_chinese_ratio,
            allowed_components=text_utils.parse_csv(cfg.filter.allow_components),
        )
        if reason:
            self.ctx.logger.info("跳过语音：%s", reason)
            return {"action": "continue"}

        limited = self.limiter.blocked_reason(
            session_id,
            cooldown_seconds=cfg.auto.cooldown_seconds,
            max_per_hour=cfg.auto.max_per_hour,
        )
        if limited:
            self.ctx.logger.info("跳过语音：%s", limited)
            return {"action": "continue"}

        if random.random() >= cfg.auto.probability:
            return {"action": "continue"}

        speak_text = body
        if cfg.style.oral_rewrite:
            speak_text = await self._oralize(speak_text)
        cleaned = text_utils.clean_for_tts(
            speak_text,
            fix_punctuation=cfg.style.clean_punctuation,
            strip_latin=cfg.style.strip_latin,
        )
        if not cleaned:
            self.ctx.logger.info("跳过语音：清洗后无有效文本")
            return {"action": "continue"}

        try:
            result = await self.synthesize_with_retry(cleaned, self._pick_style())
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("语音合成异常，保留文字：%s", exc)
            return {"action": "continue"}

        if not result.get("success") or not result.get("audio_base64"):
            self.ctx.logger.warning("语音未合成，保留文字：%s", result.get("error"))
            return {"action": "continue"}

        audio = self._post_process(result["audio_base64"], result)
        if not await self._send_voice(audio, session_id):
            self.ctx.logger.warning("语音未发出，保留文字")
            return {"action": "continue"}

        self.limiter.mark_sent(session_id)
        if cfg.auto.keep_emoji:
            try:
                await self._resend_emoji(message, session_id)
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("补发表情异常：%s", exc)

        self.ctx.logger.info(
            "语音已替换文字：session=%s，%d 字，概率=%.2f", session_id, len(cleaned),
            cfg.auto.probability,
        )
        # abort 必须带 reason：否则 MaiBot 会生成「未提供原因」的工具结果，
        # Planner 会把它理解成插件故障并在下一轮说出来。
        return {
            "action": "abort",
            "reason": "本条回复已改用语音发送，文字部分不需要再发，也不需要重试或道歉。",
        }

    async def _oralize(self, body: str) -> str:
        """用 LLM 把书面语改写成口语；任何异常都回退原文。"""
        cfg = self.config.style
        try:
            result = await self.ctx.llm.generate(
                prompt=[
                    {
                        "role": "system",
                        "content": (
                            "把下面这段文字改写成适合口头说出来的话。要求："
                            "1) 保留原意和全部信息，不增删事实；"
                            "2) 拆成短句，句间用逗号或句号；"
                            "3) 去掉书面词与排比结构；"
                            "4) 不要加任何解释、前缀或引号；"
                            "5) 长度与原文相当。"
                        ),
                    },
                    {"role": "user", "content": body},
                ],
                task_name=cfg.oral_model or "fast_model",
                temperature=0.7,
                max_tokens=256,
            )
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("口语化改写失败，用原文：%s", exc)
            return body

        output = str(result.get("response") or "").strip().strip("「」\"'")
        if not output or len(output) < len(body) * 0.5 or len(output) > len(body) + 20:
            return body
        return output

    # ────────────────────────── Command ──────────────────────────

    @Command(
        "voice_status",
        description="查看语音插件当前状态",
        pattern=r"(?<!\S)/语音\s*$",
        aliases=["/voice"],
    )
    async def cmd_status(self, **kwargs: Any) -> tuple[bool, str, bool]:
        """输出引擎、音色、频率与限流状态。"""
        stream_id = _find_stream_id(kwargs)
        cfg = self.config
        lines = [
            f"引擎：{cfg.engine.voice_mode}",
            f"默认音色：{self.default_voice or '(未配置)'}",
            f"触发概率：{cfg.auto.probability:.0%}",
            f"字数窗口：{cfg.auto.min_chars}–{cfg.auto.max_chars or '不限'}",
            f"冷却：{cfg.auto.cooldown_seconds} 秒｜每小时上限：{cfg.auto.max_per_hour or '不限'}",
            f"排除群：{cfg.auto.exclude_groups or '无'}",
        ]
        if cfg.engine.voice_mode == "cvcpp" and self.tts is not None:
            health = await self.tts.check_cvcpp_health()
            detail = health.get("data") or {}
            state = "就绪" if health.get("ok") else "不可用"
            extra = ""
            if isinstance(detail, dict) and detail:
                busy = "忙" if detail.get("busy") else "空闲"
                extra = f"，{busy}，队列 {detail.get('pending', 0)}"
            lines.append(f"本地服务：{state}{extra}（{cfg.engine.cv_endpoint}）")
        stats = self.limiter.stats(stream_id)
        lines.append(f"本会话近一小时已发：{stats['hour_count']} 条")
        if self._cache is not None:
            cs = self._cache.stats()
            lines.append(
                f"音频缓存：{cs['entries']} 条 / {cs['bytes'] // 1024} KB，"
                f"命中率 {cs['hit_rate']:.0%}"
            )
        else:
            lines.append("音频缓存：已关闭")
        if self._synth_lock.locked():
            lines.append("当前有合成任务在跑，新请求会排队")
        reply = "\n".join(lines)
        await self.ctx.send.text(reply, stream_id)
        return True, "ok", True

    @Command(
        "voice_probability",
        description="设置自动转语音概率（0~1）",
        pattern=r"(?<!\S)/语音频率\s+(?P<value>[0-9]*\.?[0-9]+)\s*$",
        permission="operator",
    )
    async def cmd_probability(self, **kwargs: Any) -> tuple[bool, str, bool]:
        """调整触发概率。"""
        stream_id = _find_stream_id(kwargs)
        try:
            value = float(_group(kwargs, "value"))
        except (TypeError, ValueError):
            value = -1.0
        if not 0.0 <= value <= 1.0:
            await self.ctx.send.text("概率需在 0 到 1 之间，例如 /语音频率 0.3", stream_id)
            return False, "invalid", True
        await self._patch_config("auto", "probability", value)
        await self.ctx.send.text(f"自动转语音概率已设为 {value:.0%}", stream_id)
        return True, "ok", True

    @Command(
        "voice_cooldown",
        description="设置同会话冷却秒数",
        pattern=r"(?<!\S)/语音冷却\s+(?P<value>\d+)\s*$",
        permission="operator",
    )
    async def cmd_cooldown(self, **kwargs: Any) -> tuple[bool, str, bool]:
        """调整冷却时间。"""
        stream_id = _find_stream_id(kwargs)
        try:
            value = int(_group(kwargs, "value"))
        except (TypeError, ValueError):
            value = -1
        if value < 0:
            await self.ctx.send.text("冷却秒数需为非负整数", stream_id)
            return False, "invalid", True
        await self._patch_config("auto", "cooldown_seconds", value)
        await self.ctx.send.text(f"同会话冷却已设为 {value} 秒", stream_id)
        return True, "ok", True

    @Command(
        "voice_limit",
        description="设置每小时语音上限",
        pattern=r"(?<!\S)/语音上限\s+(?P<value>\d+)\s*$",
        permission="operator",
    )
    async def cmd_limit(self, **kwargs: Any) -> tuple[bool, str, bool]:
        """调整每小时上限。0 表示不限制。"""
        stream_id = _find_stream_id(kwargs)
        try:
            value = int(_group(kwargs, "value"))
        except (TypeError, ValueError):
            value = -1
        if value < 0:
            await self.ctx.send.text("上限需为非负整数，0 表示不限制", stream_id)
            return False, "invalid", True
        await self._patch_config("auto", "max_per_hour", value)
        await self.ctx.send.text(f"每小时上限已设为 {value or '不限'}", stream_id)
        return True, "ok", True

    @Command(
        "voice_group_toggle",
        description="把某个群加入或移出语音排除名单",
        pattern=r"(?<!\S)/语音群\s+(?P<value>\d{5,})\s*$",
        permission="operator",
    )
    async def cmd_group(self, **kwargs: Any) -> tuple[bool, str, bool]:
        """切换群排除状态。"""
        stream_id = _find_stream_id(kwargs)
        group_id = _group(kwargs, "value").strip()
        if not group_id:
            await self.ctx.send.text("请提供群号，例如 /语音群 123456789", stream_id)
            return False, "invalid", True

        current = text_utils.parse_csv(self.config.auto.exclude_groups)
        if group_id in current:
            current.remove(group_id)
            action = "已移出排除名单"
        else:
            current.append(group_id)
            action = "已加入排除名单"
        await self._patch_config("auto", "exclude_groups", ",".join(current))
        await self.ctx.send.text(f"群 {group_id} {action}。当前：{','.join(current) or '无'}", stream_id)
        return True, "ok", True

    @Command(
        "voice_toggle",
        description="开关语音插件的自动转语音",
        pattern=r"(?<!\S)/语音开关\s*$",
        permission="operator",
    )
    async def cmd_toggle(self, **kwargs: Any) -> tuple[bool, str, bool]:
        """在 0 与上次概率之间切换。"""
        stream_id = _find_stream_id(kwargs)
        current = float(self.config.auto.probability)
        if current > 0:
            self._saved_probability = current
            await self._patch_config("auto", "probability", 0.0)
            await self.ctx.send.text("自动转语音已关闭（工具仍可手动调用）", stream_id)
        else:
            restore = float(getattr(self, "_saved_probability", 0.3) or 0.3)
            await self._patch_config("auto", "probability", restore)
            await self.ctx.send.text(f"自动转语音已开启，概率 {restore:.0%}", stream_id)
        return True, "ok", True

    @Command(
        "voice_test",
        description="用指定文本试听当前音色",
        pattern=r"(?<!\S)/语音测试\s+(?P<text>.+)$",
        permission="operator",
    )
    async def cmd_test(self, **kwargs: Any) -> tuple[bool, str, bool]:
        """合成一小段音频并发送，用于快速验证音色与链路。"""
        stream_id = _find_stream_id(kwargs)
        body = _group(kwargs, "text").strip()
        if not body:
            await self.ctx.send.text("请提供要朗读的文本", stream_id)
            return False, "invalid", True
        body = body[:120]

        cleaned = text_utils.clean_for_tts(
            body,
            fix_punctuation=self.config.style.clean_punctuation,
            strip_latin=self.config.style.strip_latin,
        )
        result = await self.synthesize_with_retry(cleaned, self._pick_style())
        if not result.get("success") or not result.get("audio_base64"):
            await self.ctx.send.text(f"合成失败：{result.get('error', '未知错误')}", stream_id)
            return False, "failed", True
        audio = self._post_process(result["audio_base64"], result)
        ok = await self._send_voice(audio, stream_id)
        if not ok:
            await self.ctx.send.text("语音发送失败，请检查平台适配器", stream_id)
            return False, "failed", True
        await self.ctx.send.text(
            f"已发送测试语音（{len(cleaned)} 字，耗时 {result.get('elapsed_ms', 0)} ms）",
            stream_id,
        )
        return True, "ok", True

    async def _patch_config(self, section: str, key: str, value: Any) -> None:
        """把单个配置项写回内存模型与 config.toml。

        SDK 的配置能力是只读的，所以这里自己改模型、自己序列化回文件：
        先读现有原始配置，合并这一项，交给配置模型校验，再落盘。
        落盘后 Runner 下次重载会读到新值，不会在重启时丢失。
        """
        try:
            current = await self.ctx.config.get_plugin()
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("读取插件配置失败，改用内存副本：%s", exc)
            current = dict(self._plugin_config_data or {})

        merged = dict(current or {})
        bucket = dict(merged.get(section) or {})
        bucket[key] = value
        merged[section] = bucket

        model = type(self).get_config_model()
        if model is None:
            self.ctx.logger.warning("未声明配置模型，无法写入配置")
            return
        try:
            instance = model.model_validate(merged)
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("配置校验失败，改动未生效：%s", exc)
            return

        self._plugin_config_instance = instance
        self._plugin_config_data = instance.model_dump(mode="python")
        self._refresh_skip_regex()
        self._write_config_file(self._plugin_config_data)
        self.ctx.logger.info("配置已更新：%s.%s = %r", section, key, value)

    @staticmethod
    def _write_config_file(data: Dict[str, Any]) -> None:
        """把配置字典序列化成 TOML 写回 config.toml。

        只处理 str / int / float / bool 与一层嵌套表，够本插件用。
        """
        lines: List[str] = []
        scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
        tables = {k: v for k, v in data.items() if isinstance(v, dict)}

        for key, value in scalars.items():
            lines.append(f"{key} = {_toml_value(value)}")
        for name, table in tables.items():
            lines.append("")
            lines.append(f"[{name}]")
            for key, value in table.items():
                if isinstance(value, dict):
                    continue
                lines.append(f"{key} = {_toml_value(value)}")

        target = PLUGIN_DIR / "config.toml"
        try:
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("写回 config.toml 失败：%s", exc)

    # ────────────────────────── API ──────────────────────────

    @API(
        "voice_synthesize",
        description="供其它插件调用的语音合成接口，返回 wav 的 base64",
        version="1",
        public=True,
    )
    async def api_synthesize(
        self, text: str, style_instruction: str = "", voice_name: str = ""
    ) -> Dict[str, Any]:
        """合成语音但不发送，调用方自行处理音频。"""
        cleaned = text_utils.clean_for_tts(
            text,
            fix_punctuation=self.config.style.clean_punctuation,
            strip_latin=self.config.style.strip_latin,
        )
        if not cleaned:
            return {"success": False, "error": "清洗后无有效文本"}
        result = await self.synthesize_with_retry(
            cleaned, style_instruction or self._pick_style(), voice_name
        )
        if not result.get("success"):
            return {"success": False, "error": result.get("error", "合成失败")}
        audio = self._post_process(result["audio_base64"], result)
        return {
            "success": True,
            "audio_base64": normalize_audio_base64(audio),
            "audio_len": result.get("audio_len"),
            "elapsed_ms": result.get("elapsed_ms"),
            "engine": self.config.engine.voice_mode,
        }

    # ────────────────────────── 首页卡片与配置控件 ──────────────────────────

    @HomeCard(
        "voice_status_card",
        title="语音回复",
        description="把文字回复自动转成语音发送",
        content=[
            {
                "type": "markdown",
                "content": (
                    "**常用命令**\n\n"
                    "- `/语音` 查看当前引擎、音色、概率与限流状态\n"
                    "- `/语音频率 0.3` 调整自动转语音概率\n"
                    "- `/语音群 123456789` 把某个群加入或移出排除名单\n"
                    "- `/语音测试 你好呀` 试听当前音色\n"
                    "- `/语音开关` 一键暂停或恢复自动转语音"
                ),
            },
            {
                "type": "key_value",
                "entries": {
                    "引擎": "cvcpp / sf / clone / preset / design",
                    "配置": "插件配置页可直接改全部参数",
                },
            },
        ],
        link_url="https://github.com/xingranya/maibot-voice-reply",
        link_label="使用文档",
        width="medium",
        order=200,
    )
    async def home_card_anchor(self) -> None:
        """首页卡片锚点。

        WebUI 只渲染装饰器里声明的静态 ``content``，不回调插件取实时数据，
        所以这里不放逻辑，实时状态走 ``/语音`` 命令。
        """
        return None

    async def get_config_widget_state(self) -> dict[str, ConfigWidgetState]:
        """返回配置页控件的状态。"""
        state = self._last_health or {}
        if not state:
            return {"health_check": ConfigWidgetState(text="尚未探测", tone="neutral")}
        ok = bool(state.get("ok"))
        return {
            "health_check": ConfigWidgetState(
                text=str(state.get("text") or ""),
                tone="success" if ok else "error",
            )
        }

    async def on_config_widget_action(self, action_id: str) -> None:
        """处理配置页按钮：探测当前 TTS 服务。"""
        if action_id != "health_check":
            raise ValueError(f"未知控件操作: {action_id}")
        if self.tts is None:
            self._last_health = {"ok": False, "text": "TTS 客户端未初始化"}
            return
        engine = self.config.engine
        if engine.voice_mode == "cvcpp":
            probe = await self.tts.check_cvcpp_health()
            self._last_health = {
                "ok": bool(probe.get("ok")),
                "text": f"本地服务 {'就绪' if probe.get('ok') else '不可用'}：{engine.cv_endpoint}"
                + (f"（{probe.get('error')}）" if probe.get("error") else ""),
            }
            return
        started = time.time()
        result = await self.synthesize_with_retry("测试一下。", self._pick_style())
        elapsed = int((time.time() - started) * 1000)
        if result.get("success"):
            self._last_health = {
                "ok": True,
                "text": f"{engine.voice_mode} 合成正常，耗时 {elapsed} ms",
            }
        else:
            self._last_health = {
                "ok": False,
                "text": f"{engine.voice_mode} 合成失败：{result.get('error')}",
            }


def _toml_value(value: Any) -> str:
    """把 Python 标量渲染成 TOML 字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _group(kwargs: Dict[str, Any], name: str) -> str:
    """取命令正则的命名捕获组。MaiBot 只透传 groupdict()，匿名组拿不到。"""
    groups = kwargs.get("matched_groups")
    if isinstance(groups, dict):
        return str(groups.get(name) or "")
    return ""


def _find_stream_id(kwargs: Dict[str, Any]) -> str:
    """从 Tool/Command 的调用参数里找出会话 ID。"""
    for key in ("stream_id", "session_id"):
        value = str(kwargs.get(key) or "").strip()
        if value:
            return value
    message = kwargs.get("message")
    if isinstance(message, dict):
        value = str(message.get("session_id") or "").strip()
        if value:
            return value
    return ""


def _decode_audio(audio_b64: str) -> bytes:
    """把 data URI 或裸 base64 解码成字节。"""
    body = normalize_audio_base64(audio_b64)
    if not body:
        return b""
    try:
        return base64.b64decode(body)
    except Exception:  # noqa: BLE001
        return b""


def create_plugin() -> VoiceReplyPlugin:
    """插件工厂，Runner 通过它拿到实例。"""
    return VoiceReplyPlugin()
