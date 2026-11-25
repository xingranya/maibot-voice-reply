"""插件配置模型。

配置分三节：
- ``plugin``：总开关与配置版本
- ``voice``：TTS 引擎、音色、语气、自动转语音策略、限流与内容过滤
- ``audio``：后处理滤波、响度归一化、口音守卫

所有字段都带 ``json_schema_extra`` 里的 ``label`` 与 ``hint``，WebUI 配置页据此渲染中文标签。
"""

from __future__ import annotations

from typing import Literal

from maibot_sdk import Field, PluginConfigBase

#: 各引擎的默认值，供 UI 提示复用
ENGINE_DESC = (
    "cvcpp = 本地 cosyvoice.cpp（免费、离线、慢）；"
    "sf = SiliconFlow 云端克隆（快、按字符计费）；"
    "clone = MiMo 音色复刻；preset = MiMo 预置音色；design = MiMo 文字设计音色"
)


class PluginSectionConfig(PluginConfigBase):
    """插件总开关。"""

    __ui_label__ = "插件"

    enabled: bool = Field(
        default=True,
        description="是否启用本插件",
        json_schema_extra={"label": "启用插件"},
    )
    config_version: str = Field(
        default="1.0.0",
        description="配置版本，用于后续迁移",
        json_schema_extra={"label": "配置版本"},
    )


class EngineSectionConfig(PluginConfigBase):
    """TTS 引擎与音色。"""

    __ui_label__ = "引擎与音色"

    voice_mode: Literal["cvcpp", "sf", "clone", "preset", "design"] = Field(
        default="cvcpp",
        description=ENGINE_DESC,
        json_schema_extra={"label": "合成引擎", "hint": ENGINE_DESC},
    )

    # ---- 本地 cosyvoice.cpp ----
    cv_endpoint: str = Field(
        default="http://172.17.0.1:18091",
        description="本地 cosyvoice.cpp 服务的 HTTP 地址",
        json_schema_extra={
            "label": "cvcpp 服务地址",
            "hint": "容器内访问宿主机用 172.17.0.1；同机部署可填 127.0.0.1。",
        },
    )
    cv_speed: float = Field(
        default=1.1,
        description="cvcpp 语速倍率，1.0 为原速",
        json_schema_extra={"label": "cvcpp 语速"},
    )
    cv_timeout_seconds: float = Field(
        default=110.0,
        description="cvcpp 单次合成超时（秒）",
        json_schema_extra={"label": "cvcpp 超时", "hint": "本地 CPU 推理，长句需要更久。"},
    )

    # ---- SiliconFlow ----
    sf_api_key: str = Field(
        default="",
        description="SiliconFlow API Key（sk- 开头）",
        json_schema_extra={"label": "SiliconFlow Key", "hint": "留空则 sf 模式不可用。"},
    )
    sf_api_base_url: str = Field(
        default="https://api.siliconflow.cn/v1",
        description="SiliconFlow API 地址",
        json_schema_extra={"label": "SiliconFlow 地址"},
    )
    sf_model: str = Field(
        default="FunAudioLLM/CosyVoice2-0.5B",
        description="SiliconFlow 语音模型",
        json_schema_extra={
            "label": "SiliconFlow 模型",
            "hint": "可选 FunAudioLLM/CosyVoice2-0.5B 或 fnlp/MOSS-TTSD-v0.5。",
        },
    )
    sf_voice_uri: str = Field(
        default="",
        description="SiliconFlow 已建立的音色 URI（speech:xxx）",
        json_schema_extra={"label": "SiliconFlow 音色 URI"},
    )
    sf_ref_audio: str = Field(
        default="",
        description="参考音频文件名（voices 目录下，≤30s），留空则只用 sf_voice_uri",
        json_schema_extra={
            "label": "SiliconFlow 参考音频",
            "hint": "填写后首次合成会自动上传建音色并把 URI 缓存回 sf_voice_uri。",
        },
    )
    sf_prompt_text: str = Field(
        default="",
        description="参考音频的逐字转写文本，必须与音频内容完全一致",
        json_schema_extra={
            "label": "参考音频文本",
            "hint": "填错会把参考音频原话念出来。",
        },
    )
    sf_sample_rate: int = Field(
        default=32000,
        description="SiliconFlow 输出采样率",
        json_schema_extra={"label": "SiliconFlow 采样率"},
    )

    # ---- MiMo ----
    mimo_api_key: str = Field(
        default="",
        description="小米 MiMo API Key",
        json_schema_extra={"label": "MiMo Key", "hint": "clone / preset / design 模式需要。"},
    )
    mimo_api_base_url: str = Field(
        default="https://api.xiaomimimo.com/v1",
        description="MiMo API 地址",
        json_schema_extra={"label": "MiMo 地址"},
    )
    preset_voice: str = Field(
        default="冰糖",
        description="MiMo 预置音色名",
        json_schema_extra={
            "label": "预置音色",
            "hint": "可选 冰糖 / 茉莉 / 苏打 / 白桦 / Mia / Chloe / Milo / Dean。",
        },
    )
    clone_voice: str = Field(
        default="",
        description="MiMo 音色复刻使用的参考音频名（voices 目录下的文件名，不含扩展名）",
        json_schema_extra={"label": "复刻参考音频"},
    )
    design_voice_description: str = Field(
        default="",
        description="MiMo 文字设计音色的描述",
        json_schema_extra={
            "label": "文字设计音色描述",
            "hint": "如「二十岁出头的女生，声音清亮偏甜，语速偏快」。",
        },
    )
    mimo_speed: float = Field(
        default=0.0,
        description="MiMo 语速倍率，0 表示不传该参数走服务端默认",
        json_schema_extra={
            "label": "MiMo 语速",
            "hint": "写进 audio.speed，约 0.85 偏慢、1.15 偏快；0 = 不指定。",
        },
    )
    mimo_tag: str = Field(
        default="",
        description="音频标签，写在 assistant 文本开头，如「（开心）」",
        json_schema_extra={
            "label": "音频标签",
            "hint": "官方支持半角 ()、全角（）、[] 三种括号；留空则不加。",
        },
    )
    mimo_optimize_text_preview: bool = Field(
        default=False,
        description="是否开启播报文本智能润色（仅 voicedesign 支持）",
        json_schema_extra={
            "label": "文本润色",
            "hint": "仅 design 模式有效，开启后模型会先润色再合成。",
        },
    )
    voices_dir: str = Field(
        default="voices",
        description="参考音频目录（相对插件目录）",
        json_schema_extra={"label": "参考音频目录"},
    )
    default_voice: str = Field(
        default="",
        description="默认音色标识，留空则由当前引擎自动推导",
        json_schema_extra={"label": "默认音色"},
    )


class StyleSectionConfig(PluginConfigBase):
    """语气与文本处理。"""

    __ui_label__ = "语气与文本"

    style: str = Field(
        default=(
            "像跟熟人随口聊天，带点笑意和真实的情绪，语速自然，句子说完音调自然往下走"
            "|像跟熟人聊天带笑意，语速自然有快有慢，逗号处自然停顿，句子说完音调下沉"
            "|像跟朋友当面聊天，语气放松带点懒散，语速自然，句子说完音调往下落"
        ),
        description="风格指令，多套用 | 分隔，轮换使用",
        json_schema_extra={
            "label": "风格指令",
            "hint": "写给 TTS 的语气描述，像给演员说戏。多套用 | 分隔。",
        },
    )
    style_rotate: bool = Field(
        default=True,
        description="是否在多套风格之间随机轮换",
        json_schema_extra={"label": "轮换风格"},
    )
    clean_punctuation: bool = Field(
        default=True,
        description="把空格换行改成逗号、补齐句末标点、去掉 Markdown 标记",
        json_schema_extra={
            "label": "清洗标点",
            "hint": "模型常输出无标点的空格分词文本，直接朗读会没有停顿。",
        },
    )
    strip_latin: bool = Field(
        default=True,
        description="清理连续英文单词（部分模型遇到会返回空音频）",
        json_schema_extra={"label": "清理连续英文"},
    )
    oral_rewrite: bool = Field(
        default=False,
        description="合成前用 LLM 把书面语改写成口语",
        json_schema_extra={
            "label": "口语化改写",
            "hint": "多一次 LLM 调用，会增加延迟；默认关闭。",
        },
    )
    oral_model: str = Field(
        default="",
        description="口语化改写使用的模型任务名，留空用 fast_model",
        json_schema_extra={"label": "改写模型"},
    )


class AutoSectionConfig(PluginConfigBase):
    """自动转语音策略。"""

    __ui_label__ = "自动转语音"

    probability: float = Field(
        default=0.0,
        description="文字回复自动转语音的概率，0 表示关闭，1 表示每条都转",
        json_schema_extra={
            "label": "触发概率",
            "hint": "按概率把已生成的文字回复改发语音，不依赖模型主动调用工具。",
        },
    )
    min_chars: int = Field(
        default=12,
        description="自动转语音的最短字数",
        json_schema_extra={"label": "最短字数", "hint": "太短的回复（如「好」）仍发文字。"},
    )
    max_chars: int = Field(
        default=50,
        description="自动转语音的最长字数，0 表示不限制",
        json_schema_extra={
            "label": "最长字数",
            "hint": "本地引擎约 1 字/秒，过长会让等待变久。0 表示不限制。",
        },
    )
    in_group: bool = Field(
        default=True,
        description="群聊是否参与自动转语音",
        json_schema_extra={"label": "群聊启用"},
    )
    in_private: bool = Field(
        default=True,
        description="私聊是否参与自动转语音",
        json_schema_extra={"label": "私聊启用"},
    )
    exclude_groups: str = Field(
        default="",
        description="这些群不转语音，只发文字和表情包，群号用逗号分隔",
        json_schema_extra={"label": "排除群号", "hint": "多个群号用逗号分隔，如 123456,789012。"},
    )
    keep_emoji: bool = Field(
        default=True,
        description="转语音后把原消息里的表情包单独补发出去",
        json_schema_extra={"label": "保留表情包"},
    )
    cooldown_seconds: int = Field(
        default=30,
        description="同一会话两次自动语音的最小间隔（秒），0 表示不限制",
        json_schema_extra={"label": "同会话冷却(秒)"},
    )
    max_per_hour: int = Field(
        default=60,
        description="同一会话每小时最多自动语音条数，0 表示不限制",
        json_schema_extra={"label": "每小时上限(条)"},
    )


class FilterSectionConfig(PluginConfigBase):
    """内容过滤。"""

    __ui_label__ = "内容过滤"

    skip_patterns: str = Field(
        default=(
            r"https?://|www\.|b23\.tv|t\.cn|\[CQ:"
            r"|【合并转发|【图片|【文件|【语音|【链接|【卡片|【视频|【音乐"
            r"|\[图片|\[文件|\[转发|\[链接"
        ),
        description="命中任一正则就跳过自动语音，多条用 | 连接",
        json_schema_extra={
            "label": "跳过正则",
            "hint": "默认拦掉链接、合并转发与各种占位符——这些念出来毫无意义。",
        },
    )
    min_chinese_ratio: float = Field(
        default=0.6,
        description="中文字符占比低于该值就跳过，避免朗读纯英文或符号串",
        json_schema_extra={"label": "中文占比下限", "hint": "0~1，0 表示不限制。"},
    )
    allow_components: str = Field(
        default="text,at,reply,emoji",
        description="允许的原始消息组件类型，逗号分隔；出现其它类型就跳过",
        json_schema_extra={
            "label": "允许的组件",
            "hint": "可选 text / at / reply / emoji / image / file / voice / forward。",
        },
    )


class AudioSectionConfig(PluginConfigBase):
    """音频后处理。"""

    __ui_label__ = "音频后处理"

    peak_db: float = Field(
        default=-1.5,
        description="峰值目标（dBFS），≥0 表示不做后处理",
        json_schema_extra={"label": "峰值目标(dB)"},
    )
    rms_db: float = Field(
        default=-18.0,
        description="响度目标（dBFS）",
        json_schema_extra={"label": "响度目标(dB)"},
    )
    denoise: bool = Field(
        default=True,
        description="启用滤波降噪（高通去喷麦 + 高频搁架压电流音 + 低通砍毛刺）",
        json_schema_extra={"label": "滤波降噪"},
    )
    accent_retry: int = Field(
        default=0,
        description="口音检测不通过时的重试次数，0 表示不检测",
        json_schema_extra={
            "label": "口音重试次数",
            "hint": "检测到句尾上扬（台湾腔）时重新合成，每次重试都会增加延迟。",
        },
    )
    accent_risk_max: float = Field(
        default=10.0,
        description="口音风险阈值，超过则重试",
        json_schema_extra={"label": "口音风险阈值", "hint": "越低越严格。"},
    )
    queue_wait_seconds: float = Field(
        default=45.0,
        description="本地引擎串行推理，排队超过这个时长就放弃本次语音",
        json_schema_extra={
            "label": "排队等待上限(秒)",
            "hint": "本地引擎一句要十几到几十秒，排太久赶不上发送窗口，直接回退文字。",
        },
    )
    cache_enabled: bool = Field(
        default=True,
        description="缓存合成结果，同文本同风格直接复用音频",
        json_schema_extra={
            "label": "启用音频缓存",
            "hint": "本地合成一句要十几秒，重复短句走缓存能省掉等待。",
        },
    )
    cache_max_mb: int = Field(
        default=48,
        description="音频缓存容量上限（MB）",
        json_schema_extra={"label": "缓存容量(MB)", "hint": "0 表示不缓存。"},
    )
    cache_ttl_seconds: int = Field(
        default=1800,
        description="缓存条目有效期（秒）",
        json_schema_extra={"label": "缓存有效期(秒)"},
    )


class UsageSectionConfig(PluginConfigBase):
    """把语音合成的消费量记进 MaiBot 的模型消耗统计。"""

    __ui_label__ = "用量统计"

    enabled: bool = Field(
        default=True,
        description="把每次合成写进 MaiBot 的模型消耗统计（llm_usage 与 model_task_usage）",
        json_schema_extra={
            "label": "记录用量",
            "hint": "开启后 WebUI 的模型消耗页与 cssqy.token-usage-report 都能看到语音合成的调用次数、耗时和费用。",
        },
    )
    model_name: str = Field(
        default="",
        description="统计里显示的模型名；留空按引擎自动取",
        json_schema_extra={
            "label": "模型名",
            "hint": "留空时 cvcpp 显示 cosyvoice3-cvcpp，SiliconFlow 显示实际模型名，MiMo 显示 mimo-v2.5-tts*。",
        },
    )
    provider_name: str = Field(
        default="",
        description="统计里显示的供应商名；留空按引擎自动取",
        json_schema_extra={
            "label": "供应商名",
            "hint": "留空时 cvcpp 显示「本地 cosyvoice.cpp」，SiliconFlow 显示 SiliconFlow，MiMo 显示「小米 MiMo」。",
        },
    )
    cost_per_1k_chars: float = Field(
        default=0.0,
        description="每千字符的费用（元），用于折算 cost 字段",
        json_schema_extra={
            "label": "每千字符费用(元)",
            "hint": "本地 cvcpp 填 0 即为免费；SiliconFlow CosyVoice2 约 0.05。",
        },
    )
    count_cache_hits: bool = Field(
        default=True,
        description="缓存命中是否也计一次调用",
        json_schema_extra={
            "label": "缓存命中也计数",
            "hint": "缓存命中不消耗算力，但确实省了一次合成；关闭后只统计真实调用。",
        },
    )


class VoicePluginConfig(PluginConfigBase):
    """插件根配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    engine: EngineSectionConfig = Field(default_factory=EngineSectionConfig)
    style: StyleSectionConfig = Field(default_factory=StyleSectionConfig)
    auto: AutoSectionConfig = Field(default_factory=AutoSectionConfig)
    filter: FilterSectionConfig = Field(default_factory=FilterSectionConfig)
    audio: AudioSectionConfig = Field(default_factory=AudioSectionConfig)
    usage: UsageSectionConfig = Field(default_factory=UsageSectionConfig)
