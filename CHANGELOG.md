# 更新日志

本文件记录 `maibot-voice-reply` 的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.0] - 2026-11-25

### 新增

- **用量统计**（`core/usage.py` + `[usage]` 配置节）：每次合成写进 MaiBot 的
  `llm_usage`（明细）与 `model_task_usage`（分钟级可靠性），字段口径与主程序一致。
  - `request_type` 用 `plugin.xingranya.maibot-voice-reply`，WebUI 的模型消耗页
    归到「插件 xingranya.maibot-voice-reply」板块；`cssqy.token-usage-report`
    按 `plugin` 前缀自动归到「插件」分组，无需改它的配置。
  - `task_name` 用 `tts`，与主程序原生语音合成分工同名。
  - `prompt_tokens` / `total_tokens` 记合成文本的字符数（TTS 没有 token 概念），
    `completion_tokens` 恒为 0，`cost` 按 `cost_per_1k_chars` 折算。
  - 缓存命中计一次调用（`time_cost = 0`），可用 `count_cache_hits` 关掉。
  - 失败尝试只进 `model_task_usage` 的 `failure_count`，不进 `llm_usage`。
  - 写库异常只记一条 warning，绝不影响语音是否发出。
- `_manifest.json` 声明 `database.save` / `database.get` / `database.query` 三项能力。

### 变更

- 版本号 `1.0.1` → `1.1.0`（新增功能，向后兼容）。

### 测试

- 新增 `tests/test_usage.py`（14 项）与 `TestUsageReporting`（11 项），
  覆盖字段口径、同分钟累加、缓存命中、写库失败兜底、warning 只报一次。
- 全量 232 项通过。

---

## [1.0.1] - 2026-11-25

### 修复

- `clean_for_tts` 的标点收尾用 `strip()` 会把句末的 `！` `？` 一并削掉，导致「太好了！」
  变成「太好了。」。改为只剥开头标点、末尾只剥逗号与分号。
- `normalize_wav_bytes` 在 `peak_db >= 0` 时缺少显式返回，靠后续异常兜底；
  现在提前返回并写入 `reason`。
- `synthesize_with_cvcpp` 原先用 `json=` 传参，aiohttp 默认 `ensure_ascii=True`，
  中文被转成 `\uXXXX`。配套的本地服务端在解码时漏跳四位十六进制数字，
  于是 `今天` 被念成 `今4eca天4e0b`，音频时长翻三倍、听起来像在念乱码。
  客户端改为 `data=json.dumps(..., ensure_ascii=False).encode("utf-8")` 直接发 UTF-8，
  不再依赖对端解码转义序列的能力。

### 测试

- 补齐 207 项单元测试：文本清洗、限流、音频后处理、口音守卫、TTS 请求体、插件组件、
  缓存键与并发排队。
- 新增 `TestCvcppRequestEncoding`：锁死 cvcpp 请求体必须是原始 UTF-8 字节，
  防止有人改回 `json=` 再次触发 `\uXXXX` 解码问题。
- 增加 `tests/stubs/maibot_sdk` 桩，基于 pydantic，保证嵌套配置段与真实 SDK 行为一致。

---

## [1.0.0] - 2026-11-25

首个公开版本。

### 新增

#### 合成引擎

- 五种 TTS 后端，通过 `engine.voice_mode` 切换：
  - `cvcpp` — 本地 cosyvoice.cpp HTTP 服务，离线免费，4 核 ARM CPU 上 28 字约 20-24 秒。
  - `sf` — SiliconFlow `/v1/audio/speech`，CosyVoice2-0.5B，0.7-2.5 秒返回，¥0.05/千字符。
  - `clone` — 小米 MiMo `mimo-v2.5-tts-voiceclone`，参考音频 base64 直传，≤10MB。
  - `preset` — 小米 MiMo `mimo-v2.5-tts`，预置音色 冰糖/茉莉/苏打/白桦/Mia/Chloe/Milo/Dean。
  - `design` — 小米 MiMo `mimo-v2.5-tts-voicedesign`，用文字描述设计音色。
- 三个 MiMo 引擎严格按官方 API 文档构造请求：目标文本放 `role: assistant`、风格指令放 `role: user`、`audio.format` 固定 `wav`、`stream: false`；`voicedesign` 不传 `audio.voice`。
- MiMo 扩展参数：`engine.mimo_speed`（写进 `audio.speed`，0 表示不传）、`engine.mimo_tag`（音频标签，拼在 assistant 文本开头，支持 `()` / `（）` / `[]`）、`engine.mimo_optimize_text_preview`（仅 voicedesign 生效）。
- `TTSService` 统一客户端，所有引擎共用一条 aiohttp 连接池，返回统一字典（`success` / `audio_base64` / `audio_len` / `elapsed_ms` / `model` / `status` / `error` / `timeout`）。
- 可重试 HTTP 状态码集合 `{429, 500, 502, 503, 504}`，最多 2 次尝试，指数退避基数 2.0，单次合成总预算 22 秒。
- `normalize_audio_base64()`：剥 `data:` 前缀、删空白、补齐 padding，幂等。
- SiliconFlow 参考音频首次合成时自动上传建音色，URI 缓存在进程内存（`_sf_uploaded`），并在日志中打印，便于手动写入 `sf_voice_uri` 以免重启后重复上传。

#### 触发方式

- `send_voice_reply` Tool：模型自主调用发语音，`timeout_ms=180000`，为本地合成留足时间。
- `voice_probability_gate` Hook：挂在 `send_service.before_send`，`HookMode.BLOCKING` + `HookOrder.LATE`，`timeout_ms=130000`，`ErrorPolicy.SKIP`，按 `auto.probability` 把已生成的文字回复改发语音。

#### 频率与范围控制

- `auto.probability`：文字回复自动转语音的概率，0 关闭、1 全转。
- `auto.cooldown_seconds`：同会话两次自动语音的最小间隔，默认 30 秒，0 不限制。
- `auto.max_per_hour`：同会话每小时最多自动语音条数，默认 60，0 不限制。
- `RateLimiter`：基于 `deque` 的内存计数，只保留最近一小时时间戳，超窗自动丢弃，不落盘。
- `auto.min_chars` / `auto.max_chars`：字数窗口，默认 12-50，`max_chars = 0` 表示不限制。
- `auto.in_group` / `auto.in_private`：分别控制群聊与私聊是否参与。
- `auto.exclude_groups`：群排除列表，逗号分隔群号，这些群只发文字和表情包。
- `auto.keep_emoji`：转语音后把原消息里的表情包单独补发。

#### 内容过滤

- `filter.skip_patterns`：默认拦掉链接、`b23.tv`、`t.cn`、`[CQ:`、`【合并转发】`、`【图片】`、`【文件】`、`【语音】`、`【链接】`、`【卡片】`、`【视频】`、`【音乐】` 及对应方括号占位符。
- `filter.min_chinese_ratio`：中文占比下限，默认 0.6，避免朗读纯英文或符号串。
- `filter.allow_components`：组件白名单，默认 `text,at,reply,emoji`。`emoji` 必须在列表里，否则几乎全部回复都会被跳过。
- `blocked_reason()`：统一返回跳过原因，由 Hook 写进日志（`跳过语音：<原因>`），便于定位为什么没转。

#### 文本清洗

- `clean_for_tts()`：去 Markdown 标记与占位符（`[...]` / `【...】`）、去 URL、去 `*` `_` `~` 反引号等修饰符、清连续英文词、空格换行改逗号、收敛连续标点、补句末句号、剥离首尾标点。
- `strip_latin`：默认开启，用 `[A-Za-z]+(?:[\s\-]+[A-Za-z]+)+` 清除两个以上被空格或连字符分隔的英文单词，规避部分模型的空音频 bug。
- `clean_punctuation`：默认开启，解决模型输出「空格分词、无标点」导致的念经式朗读。
- `style`：多套风格指令用 `|` 分隔，`style_rotate` 控制是否随机轮换。
- `oral_rewrite` / `oral_model`：可选的 LLM 口语化改写，默认关闭以免增加延迟。

#### 音频后处理

- `core/audio_post.py` 零相位处理链，全部基于 numpy + scipy，不依赖 ffmpeg / pydub / librosa / soundfile：
  - 高通 160Hz 四阶 Butterworth（`sosfiltfilt`）—— 去喷麦低频轰鸣。
  - 高频搁架 6kHz、-6dB、Q=0.707（RBJ cookbook 二阶，`filtfilt`）—— 压声码器电流音。
  - 低通 11.5kHz 四阶（`sosfiltfilt`）—— 砍 Nyquist 附近毛刺。
  - 峰值 -1.5dBFS + 响度 -18dBFS 归一化，tanh 软限幅不引入新失真。
- 短于 480 采样点（20ms @ 24kHz）的音频跳过滤波，只做增益，避免边缘瞬态。
- `audio.denoise` 总开关；`peak_db ≥ 0` 表示整个跳过后处理。

#### 口音守卫

- `core/accent_guard.py`：自相关跟踪 F0，按 150ms 静音间隔切分有声段，取每段末尾 30% 的 F0 半音斜率中位数。
- 风险公式：`risk = max(0, tail_slope_st) × 3 + tail_rising_ratio × 2 + max(0, 8 - f0_range_st) × 0.5`。
- `audio.accent_retry`：检测不通过时的重试次数，默认 0 表示不检测。
- `audio.accent_risk_max`：风险阈值，默认 10.0，越低越严格。

#### 管理与界面

- 管理命令：`/语音`（查看状态）、`/语音频率 <0-1>`、`/语音冷却 <秒>`、`/语音上限 <条>`、`/语音群 <群号>`、`/语音开关`、`/语音测试 <文本>`。
- WebUI 首页卡片（`@HomeCard`）：列出常用命令与引擎清单，带使用文档链接。内容为静态声明，实时状态走 `/语音` 命令。
- 配置页运行时控件（`ConfigWidget`）：健康探测按钮。cvcpp 模式探 HTTP 健康接口；云端模式真发一句「测试一下。」验证端到端链路，结果按成功/失败着色。
- 全部配置字段带 `json_schema_extra` 的 `label` 与 `hint`，WebUI 配置页据此渲染中文标签与提示。

#### 工程

- Manifest v2（`_manifest.json`）。
- `config.example.toml` 配置模板，覆盖全部六个配置节。
- `tests/` 单元测试目录。
- `requirements.txt` 仅声明 `aiohttp>=3.9`，numpy / scipy 由宿主提供。
- `.gitignore` 忽略 `config.toml`、`__pycache__/`、`*.pyc`、`.venv/`、`voices/*.wav`、`voices/*.mp3`、`.DS_Store`。

### 变更

- `abort` 必须携带 `reason`。Hook 返回 `{"action": "abort"}` 时若不给原因，MaiBot 会生成「本次操作被插件主动拦截：未提供原因」的工具结果，Planner 会把它脑补成插件故障并在下一轮对话中复述，暴露内部机制。因此 `reason` 写明确的人话。
- `abort` 的触发时机固定为「合成 → 发送 → 确认成功 → abort」。任何一步失败一律返回 `{"action": "continue"}` 保留文字，避免用户什么都收不到。
- `min_chars` 默认 12。3-6 字短句只有约 1.6 个有声段、音域被压到 9.6 半音，没有起伏空间；20-30 字才有约 4 个有声段、音域 12 半音。12 是折中值。
- `allow_components` 默认包含 `emoji`。MaiBot 回复大量带表情包，白名单不含 `emoji` 会导致几乎所有回复被跳过。
- `strip_latin` 默认开启。CosyVoice2 等模型遇到 `dsh go 没脑子` 这类连续英文词会返回空音频。
- 音频后处理全部采用零相位滤波（`sosfiltfilt` / `filtfilt`），不留相位失真；每级滤波带 `f < nyquist × 0.9`（低通 `× 0.95`）的采样率守卫，24kHz 音频自动跳过 11.5kHz 低通。
- 容器内不可用的 ffmpeg / pydub / librosa / soundfile 一律不作为依赖，改用 numpy + scipy 直接操作 16-bit PCM WAV。

### 修复

1.0.0 是首个公开版本，本节记录开发期内部迭代中发现并解决的问题。

- 修复 `data:` 前缀音频 base64 长度计算错误导致的解码失败，新增 `normalize_audio_base64()` 做归一化与 padding 补齐。
- 修复 SiliconFlow `instructions` 参数被服务端静默忽略造成的预期偏差，文档明确说明语气只能靠文本本身与标点节奏控制。
- MiMo `voicedesign` 模型没有 `audio.voice` 字段，音色描述改写进 user 消息的 content 前缀，与 `style_instruction` 用分号拼接。
- `clone` 模式的参考音频经 base64 后不得超过 10MB（对应约 30 秒单声道 WAV），超限时请求直接被服务端拒绝；已在配置项提示与 README 中明确约束格式。
- 修复短音频（< 480 采样点）滤波产生的边缘瞬态，改为跳过滤波只做增益。
- 修复模型输出纯标点文本时仍进入合成的问题，`clean_for_tts()` 在无有效内容时返回空字符串并中止。
- 限流计数改用 `deque` 只保留最近一小时时间戳，避免长跑进程的字典无界增长。

[1.0.0]: https://github.com/xingranya/maibot-voice-reply/releases/tag/v1.0.0
