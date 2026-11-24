# maibot-voice-reply

MaiBot 的 AI 语音回复插件。把机器人的文字回复自动转成语音发出去，支持本地离线 TTS、云端音色克隆、频率控制、群排除与音频后处理。

---

## 项目简介

MaiBot 默认只发文字。群里聊天时文字读不出情绪，长句也没人愿意看。这个插件在 `send_service.before_send` 上挂一个 Hook，把已经生成的文字回复按概率拦下来改成语音；同时注册一个 `send_voice_reply` Tool，让模型可以在需要的时候主动说话。

设计目标是「不打断原有对话流程」：语音发不出去就保留文字，绝不出现用户什么都收不到的情况。

---

## 功能特性

| # | 功能 | 说明 |
|---|---|---|
| 1 | `send_voice_reply` Tool | 模型自主调用发语音，`timeout_ms=180000`，为本地合成留足时间 |
| 2 | `voice_probability_gate` Hook | 挂在 `send_service.before_send`，按概率把文字回复改发语音 |
| 3 | 五种合成引擎 | 本地 cvcpp / SiliconFlow / MiMo 复刻 / MiMo 预置 / MiMo 设计 |
| 4 | 群排除 | `auto.exclude_groups` 填群号，这些群只发文字和表情包 |
| 5 | 频率控制 | 概率、同会话冷却、每小时上限三重限制 |
| 6 | 字数窗口 | `min_chars` / `max_chars`，0 表示不限制 |
| 7 | 内容过滤 | 链接、合并转发、占位符正则跳过；中文占比下限；组件白名单 |
| 8 | 表情包保留 | 转语音后把原消息的表情单独补发 |
| 9 | 文本清洗 | 空格换行改逗号、补句号、清 Markdown、清连续英文词 |
| 10 | 音频后处理 | 高通去喷麦、搁架压电流音、低通砍毛刺、峰值与响度归一化 |
| 11 | 口音守卫 | 检测句尾上扬（台湾腔）风险，超阈值重试 |
| 12 | 管理命令 | `/语音` 系列，运行时改频率、冷却、上限、开关 |
| 13 | WebUI 状态卡 | `@HomeCard` 首页卡片，列出常用命令与引擎清单（静态内容） |
| 14 | 配置页运行时控件 | `ConfigWidget` 健康探测按钮：cvcpp 探 HTTP，云端真发一句「测试一下。」 |
| 15 | 跨插件合成 API | `voice_synthesize`（`public=True`），返回 WAV base64 但不发送，供其它插件复用 |

---

## 架构

```
  ┌──────────────────────────────────────────────────────────────┐
  │ MaiBot 主程序  →  Planner 生成文字回复                       │
  └──────────────────────────────────────────────────────────────┘
                                  │
                                  │  两条入口
                                  ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ ① send_voice_reply Tool        timeout_ms=180000             │
  │ ② voice_probability_gate Hook  send_service.before_send      │
  │    BLOCKING + LATE + ErrorPolicy.SKIP, timeout_ms=130000     │
  └──────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ 前置判断链（任一不通过即 continue，保留文字）                │
  │   filter    : 跳过正则 / 中文占比下限 / 组件白名单           │
  │   auto      : 字数窗口 / 群排除 / 群聊私聊开关               │
  │   ratelimit : 同会话冷却 / 每小时上限 / 概率闸门             │
  └──────────────────────────────────────────────────────────────┘
                                  │
                                  │  全部通过
                                  ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ core/text.py   clean_for_tts()                               │
  │ 去 Markdown、补句末标点、清连续英文词                        │
  └──────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ core/tts.py   TTSService                                     │
  │ cvcpp | sf | clone | preset | design                         │
  └──────────────────────────────────────────────────────────────┘
                                  │
                                  │  WAV bytes
                                  ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ core/accent_guard.py                                         │
  │ risk > accent_risk_max ? 是 → 重新合成                       │
  └──────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ core/audio_post.py                                           │
  │ 160Hz 高通 → 6kHz 搁架 -6dB → 11.5kHz 低通                   │
  │ 峰值 -1.5dBFS + 响度 -18dBFS                                 │
  └──────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ 发送语音 → 确认成功                                          │
  │ 失败 → continue（保留文字）                                  │
  │ 成功 → mark_sent() 记账 → 补发表情 → abort(reason)           │
  └──────────────────────────────────────────────────────────────┘
                                  │
                                  │  keep_emoji 为真
                                  ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ 单独补发原消息里的 emoji 组件                                │
  │ 补发异常只记日志，不影响 abort                               │
  └──────────────────────────────────────────────────────────────┘
```

### 目录结构

```
maibot-voice-reply/
  plugin.py           # 入口，create_plugin() 工厂
  _manifest.json      # Manifest v2
  config.example.toml # 配置模板
  core/
    __init__.py
    config.py         # 配置模型（PluginConfigBase 子类）
    tts.py            # 五种 TTS 引擎客户端
    text.py           # 文本清洗与内容过滤
    audio_post.py     # WAV 后处理（滤波+响度归一化）
    accent_guard.py   # 口音风险检测（台湾腔上扬尾音）
    ratelimit.py      # 每会话冷却与每小时上限
  voices/             # 参考音频目录（.wav/.mp3）
  tests/              # 单元测试
  LICENSE
  NOTICE
  README.md
  CHANGELOG.md
  requirements.txt
  .gitignore
```

---

## 安装

### 1. 环境要求

| 项 | 要求 |
|---|---|
| MaiBot | ≥ 1.4.0 |
| 插件 SDK | ≥ 2.23.0 |
| Python | 3.12+ |
| 必需依赖 | `aiohttp>=3.9` |
| 音频后处理 | `numpy`、`scipy`（MaiBot 容器内已自带 numpy 2.5.5 + scipy 1.18.1） |

容器内**没有** ffmpeg / pydub / librosa / soundfile。所有后处理直接用 numpy + scipy 操作 16-bit PCM WAV，不依赖外部二进制。

### 2. 放置插件

把整个 `maibot-voice-reply/` 目录放进 MaiBot 的插件目录，确认 `_manifest.json` 与 `plugin.py` 同级。

### 3. 生成配置

首次启动时插件会自动把 `config.example.toml` 复制成 `config.toml`（不覆盖已有文件）。也可以手动执行：

```bash
cp config.example.toml config.toml
```

编辑 `config.toml`。`config.toml` 已在 `.gitignore` 里，不会被提交。

改完保存会触发 `on_config_update`：同步 TTS 客户端的 Key 与地址、清空 SF 音色缓存、重扫 `voices/`、重新编译跳过正则，无需重启进程。

### 4. 最小可用配置

本地离线，零成本：

```toml
[engine]
voice_mode = "cvcpp"
cv_endpoint = "http://172.17.0.1:18091"   # 容器访问宿主机
cv_speed = 1.1

[auto]
probability = 0.3
min_chars = 12
max_chars = 50
```

云端，快：

```toml
[engine]
voice_mode = "sf"
sf_api_key = "sk-xxxxxxxx"
sf_voice_uri = "speech:your-voice:xxxx"
sf_ref_audio = "ref"              # voices/ref.wav 或 ref.mp3，不含扩展名
sf_prompt_text = "参考音频的逐字转写"

[auto]
probability = 0.5
```

### 5. 参考音频

`voices/` 目录放 `.wav` 或 `.mp3`，用于 `clone` 与 `sf` 模式：

- 时长 ≤ 30 秒（`clone` 模式 base64 编码后 ≤ 10MB）
- 单人、无背景音乐、无混响
- 语速与目标语气接近
- `sf_prompt_text` 必须是参考音频的逐字转写，填错会把参考音频原话念出来
- `sf_ref_audio` 与 `clone_voice` 都填**不含扩展名**的文件名：插件先找 `voices/<名>.wav`，再找 `voices/<名>.mp3`

### 6. 重启并验证

重启 MaiBot，在私聊里发 `/语音` 查看状态，再发 `/语音测试 今天天气不错` 试听。

---

## 五种引擎

### 对比

| `voice_mode` | 后端 | 模型 / 接口 | 延迟 | 成本 | 适用场景 |
|---|---|---|---|---|---|
| `cvcpp` | 本地 cosyvoice.cpp HTTP 服务 | 自托管 CosyVoice | 4 核 ARM CPU 上 28 字约 20-24 秒 | 免费，占本机 CPU | 隐私敏感、零成本、能接受等待 |
| `sf` | SiliconFlow `/v1/audio/speech` | `FunAudioLLM/CosyVoice2-0.5B` | 0.7-2.5 秒返回 | ¥0.05/千字符 | 日常群聊主力，快且便宜 |
| `clone` | 小米 MiMo `mimo-v2.5-tts-voiceclone` | 参考音频 base64 直传 | 秒级 | MiMo 计费 | 要高度还原某个音色 |
| `preset` | 小米 MiMo `mimo-v2.5-tts` | 预置音色 | 秒级 | MiMo 计费 | 不想准备参考音频 |
| `design` | 小米 MiMo `mimo-v2.5-tts-voicedesign` | 文字描述生成音色 | 秒级 | MiMo 计费 | 想用文字描述出音色 |

`preset` 可选音色：`mimo_default` / 冰糖 / 茉莉 / 苏打 / 白桦 / Mia / Chloe / Milo / Dean。

三个 MiMo 引擎都按官方文档调用（<https://mimo.mi.com/docs/zh-CN/api/audio/tts-post>）：

| 要点 | 实现 |
|---|---|
| 目标文本位置 | `role: assistant` 的 `content`。放 `user` 会被当成指令念出来 |
| 风格指令位置 | `role: user` 的 `content`，可选；`voicedesign` 时必填（音色描述就写这里） |
| `audio.format` | 固定 `wav`。流式才需要 `pcm16`，本插件走非流式 |
| `audio.voice` | `preset` 填音色 ID；`clone` 填音频 base64（`data:audio/mpeg;base64,` 前缀，仅 mp3/wav，编码后 ≤10MB）；`voicedesign` 不支持该字段 |
| `audio.speed` | 由 `engine.mimo_speed` 控制，`0` 表示不传该字段走服务端默认 |
| `audio.optimize_text_preview` | 仅 `voicedesign` 支持，由 `engine.mimo_optimize_text_preview` 控制 |
| 音频标签 | `engine.mimo_tag` 拼在 assistant 文本开头，如 `（开心）`。官方支持半角 `()`、全角 `（）`、`[]` |

### 选择建议

| 你的情况 | 选 |
|---|---|
| 有闲置机器、不想付钱、语音延迟无所谓 | `cvcpp` |
| 群聊要即时响应、量大 | `sf` |
| 有 30 秒干净参考音频、要一模一样的声音 | `clone` |
| 懒得准备音频、要稳定可预期的音色 | `preset` |
| 想要「二十岁出头女生、声音清亮偏甜」这种定制 | `design` |
| 白天用云端、夜间用本地省钱 | 改 `voice_mode` 后重启，或做两套配置切换 |

`cvcpp` 的延迟是硬伤：28 字约 20-24 秒。它适合私聊或低频场景，不适合热闹的群。`sf` 的 0.7-2.5 秒是体感分界线，超过 3 秒用户就会觉得机器人在卡。

`sf` 的 `instructions` 参数会被服务端静默忽略，语气只能靠文本本身和 `style` 里的标点节奏控制。

### 参数如何分派

`_synthesize()` 按 `voice_mode` 分发，各引擎实际吃到的参数不同：

| `voice_mode` | 文本 | 音色来源 | 语气 `style` | 语速 | 采样率 |
|---|---|---|---|---|---|
| `cvcpp` | `text` | `cv_endpoint`（服务端固定音色） | 不使用 | `cv_speed` | 服务端决定 |
| `sf` | `text` | `sf_voice_uri`，缺失时用 `sf_ref_audio` 现建 | 不使用 | 固定 1.0 | `sf_sample_rate` |
| `clone` | `text` | `clone_voice` 对应参考音频，base64 直传 | 使用 | `mimo_speed` | 服务端决定 |
| `preset` | `text` | `preset_voice` 名称 | 使用 | `mimo_speed` | 服务端决定 |
| `design` | `text` | `design_voice_description`，与 `style` 用 `；` 拼进 user 消息 | 使用（拼进描述） | `mimo_speed` | 服务端决定 |

语速只有 `cvcpp` 的 `cv_speed` 和三个 MiMo 引擎的 `mimo_speed` 可调。`sf` 的语速固定 1.0（服务端不开放该参数），要改只能从 `style` 描述里加「语速偏快」这类指令。

---

## 配置项

配置文件分六节：`plugin` / `engine` / `style` / `auto` / `filter` / `audio`。所有字段在 WebUI 配置页都有中文标签与提示。

### `[plugin]` 总开关

| 名称 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 是否启用本插件 |
| `config_version` | str | `"1.0.0"` | 配置版本，用于后续迁移 |

### `[engine]` 引擎与音色

| 名称 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `voice_mode` | enum | `"cvcpp"` | 合成引擎：`cvcpp` / `sf` / `clone` / `preset` / `design` |
| `cv_endpoint` | str | `"http://172.17.0.1:18091"` | cvcpp 服务地址。容器内访问宿主机用 `172.17.0.1`，同机部署可填 `127.0.0.1` |
| `cv_speed` | float | `1.1` | cvcpp 语速倍率，`1.0` 为原速 |
| `cv_timeout_seconds` | float | `110.0` | cvcpp 单次合成超时（秒）。本地 CPU 推理，长句需要更久 |
| `sf_api_key` | str | `""` | SiliconFlow API Key（`sk-` 开头）。留空则 `sf` 模式不可用 |
| `sf_api_base_url` | str | `"https://api.siliconflow.cn/v1"` | SiliconFlow API 地址 |
| `sf_model` | str | `"FunAudioLLM/CosyVoice2-0.5B"` | 可选 `FunAudioLLM/CosyVoice2-0.5B` 或 `fnlp/MOSS-TTSD-v0.5` |
| `sf_voice_uri` | str | `""` | 已建立的音色 URI（`speech:xxx`） |
| `sf_ref_audio` | str | `""` | 参考音频名（`voices/` 下的文件名，不含扩展名，≤30s）。填写后首次合成自动上传建音色，URI 缓存在内存里；日志会打印 URI，建议手动填进 `sf_voice_uri` 以免重启后重复上传 |
| `sf_prompt_text` | str | `""` | 参考音频的逐字转写文本，必须与音频内容完全一致。为空时建音色直接失败；填错会把参考音频原话念出来 |
| `sf_sample_rate` | int | `32000` | SiliconFlow 输出采样率 |
| `mimo_api_key` | str | `""` | 小米 MiMo API Key。`clone` / `preset` / `design` 需要 |
| `mimo_api_base_url` | str | `"https://api.xiaomimimo.com/v1"` | MiMo API 地址 |
| `preset_voice` | str | `"冰糖"` | MiMo 预置音色名：冰糖 / 茉莉 / 苏打 / 白桦 / Mia / Chloe / Milo / Dean |
| `clone_voice` | str | `""` | 复刻使用的参考音频名（`voices/` 下的文件名，不含扩展名，base64 后 ≤10MB） |
| `design_voice_description` | str | `""` | 文字设计音色的描述，如「二十岁出头的女生，声音清亮偏甜，语速偏快」 |
| `mimo_speed` | float | `0.0` | MiMo 语速倍率，写进 `audio.speed`。`0` 表示不传该字段。实测 `0.85` 偏慢、`1.15` 偏快 |
| `mimo_tag` | str | `""` | 音频标签，拼在 assistant 文本开头，如 `（开心）`。留空不加 |
| `mimo_optimize_text_preview` | bool | `false` | 播报文本智能润色，仅 `design` 模式有效 |
| `voices_dir` | str | `"voices"` | 参考音频目录（相对插件目录） |
| `default_voice` | str | `""` | 默认音色标识。留空时按引擎推导：`cvcpp`→`cv_endpoint`；`sf`→`sf_voice_uri` 或 `sf_ref_audio`；`clone`→`clone_voice` 或 `voices/` 里第一个；`preset`→`preset_voice`；`design`→描述文本 |

### `[style]` 语气与文本

| 名称 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `style` | str | 三套内置描述 | 风格指令，多套用 `\|` 分隔轮换使用。写给 TTS 的语气描述，像给演员说戏 |
| `style_rotate` | bool | `true` | 是否在多套风格之间随机轮换 |
| `clean_punctuation` | bool | `true` | 把空格换行改成逗号、补齐句末标点、去掉 Markdown 标记 |
| `strip_latin` | bool | `true` | 清理连续英文单词（部分模型遇到会返回空音频） |
| `oral_rewrite` | bool | `false` | 合成前用 LLM 把书面语改写成口语。多一次 LLM 调用，增加延迟 |
| `oral_model` | str | `""` | 口语化改写使用的模型任务名，留空用 `fast_model` |

默认 `style` 值：

```
像跟熟人随口聊天，带点笑意和真实的情绪，语速自然，句子说完音调自然往下走|像跟熟人聊天带笑意，语速自然有快有慢，逗号处自然停顿，句子说完音调下沉|像跟朋友当面聊天，语气放松带点懒散，语速自然，句子说完音调往下落
```

### `[auto]` 自动转语音

| 名称 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `probability` | float | `0.0` | 文字回复自动转语音的概率。`0` 关闭，`1` 每条都转。配置模型默认 `0.0`，`config.example.toml` 里预置为 `0.3` |
| `min_chars` | int | `12` | 自动转语音的最短字数。太短的回复（如「好」）仍发文字 |
| `max_chars` | int | `50` | 最长字数，`0` 表示不限制。本地引擎约 1 字/秒 |
| `in_group` | bool | `true` | 群聊是否参与自动转语音 |
| `in_private` | bool | `true` | 私聊是否参与自动转语音 |
| `exclude_groups` | str | `""` | 这些群不转语音，只发文字和表情包。多个群号用逗号分隔，如 `123456,789012` |
| `keep_emoji` | bool | `true` | 转语音后把原消息里的表情包单独补发出去 |
| `cooldown_seconds` | int | `30` | 同一会话两次自动语音的最小间隔（秒），`0` 不限制 |
| `max_per_hour` | int | `60` | 同一会话每小时最多自动语音条数，`0` 不限制 |

### `[filter]` 内容过滤

| 名称 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `skip_patterns` | str | 见下 | 一条 Python 正则，用 `\|` 表示「或」。命中任一分支就跳过自动语音 |
| `min_chinese_ratio` | float | `0.6` | 中文字符占比低于该值就跳过，避免朗读纯英文或符号串。`0`~`1`，`0` 不限制 |
| `allow_components` | str | `"text,at,reply,emoji"` | 允许的原始消息组件类型，逗号分隔。出现其它类型就跳过 |

默认 `skip_patterns`：

```
https?://|www\.|b23\.tv|t\.cn|\[CQ:|【合并转发|【图片|【文件|【语音|【链接|【卡片|【视频|【音乐|\[图片|\[文件|\[转发|\[链接
```

可选组件类型：`text` / `at` / `reply` / `emoji` / `image` / `file` / `voice` / `forward`。

### `[audio]` 音频后处理

| 名称 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `peak_db` | float | `-1.5` | 峰值目标（dBFS）。`≥0` 表示不做后处理 |
| `rms_db` | float | `-18.0` | 响度目标（dBFS） |
| `denoise` | bool | `true` | 启用滤波降噪（高通去喷麦 + 高频搁架压电流音 + 低通砍毛刺） |
| `accent_retry` | int | `0` | 口音检测不通过时的重试次数，`0` 表示不检测 |
| `accent_risk_max` | float | `10.0` | 口音风险阈值，超过则重试。越低越严格 |

---

## 管理命令

`/语音` 与 `/voice` 别名对所有人生效，其余命令需要 `operator` 权限。全部命令的正则锚定行首（`(?<!\S)`），不会误触发群里的普通聊天。

| 命令 | 参数 | 作用 |
|---|---|---|
| `/语音`（别名 `/voice`） | — | 查看当前状态：引擎、默认音色、概率、字数窗口、冷却、每小时上限、排除群、本会话近一小时已发条数 |
| `/语音频率 <0-1>` | 浮点数 | 设置 `auto.probability`，越界会提示合法范围 |
| `/语音冷却 <秒>` | 整数 | 设置 `auto.cooldown_seconds` |
| `/语音上限 <条>` | 整数 | 设置 `auto.max_per_hour` |
| `/语音群 <群号>` | 单个群号（≥5 位数字） | 切换该群的排除状态：已在名单里就移出，不在就加入 |
| `/语音开关` | — | 在 `0` 与上次概率之间切换 `auto.probability`，不动 `plugin.enabled` |
| `/语音测试 <文本>` | 任意文本，超过 120 字截断 | 用当前引擎合成并直接发送，跳过所有过滤与限流 |

运行时修改会同时写回内存模型与 `config.toml`（`_patch_config` → `_write_config_file`），重启不丢。写回只处理 str / int / float / bool 与一层嵌套表，与现有配置结构一致。

`/语音` 的输出形如：

```
引擎：cvcpp
默认音色：http://172.17.0.1:18091
触发概率：30%
字数窗口：12–50
冷却：30 秒｜每小时上限：60
排除群：无
本地服务：就绪（http://172.17.0.1:18091）
本会话近一小时已发：3 条
```

`cvcpp` 模式会额外做一次 HTTP 健康探测；其它引擎不探，直接省略那一行。

---

## 工作原理

### Hook 链路时序

`voice_probability_gate` 挂在 `send_service.before_send`，`HookMode.BLOCKING` + `HookOrder.LATE` + `timeout_ms=130000` + `ErrorPolicy.SKIP`。

```
T+0       Planner 生成文字回复
T+0       send_service 准备发送
T+0       Hook 被调用（LATE，此时其它 Hook 已处理完）
T+0       ├ plugin.enabled == false           → continue
T+0       ├ 无正文 / 无 session_id            → continue
T+0       ├ 字数 < min_chars 或 > max_chars   → continue
T+0       ├ 群聊但 in_group == false          → continue
T+0       ├ 私聊但 in_private == false        → continue
T+0       ├ 群号在 exclude_groups 里          → continue
T+0       ├ 命中 skip_patterns                → continue
T+0       ├ 中文占比 < min_chinese_ratio      → continue
T+0       ├ 组件不在 allow_components         → continue
T+0       ├ 冷却未到 / 本小时超上限           → continue
T+0       ├ random() >= probability           → continue
T+0       └ 全部通过 → 进入合成
T+0.01    clean_for_tts()：去 Markdown、补标点、清连续英文
T+0.01    TTSService 合成（按 voice_mode 分派）
T+20~25   本地引擎返回 WAV；云端 1-3 秒返回
T+25      accent_guard.analyze()：risk > accent_risk_max 且有剩余重试 → 回到合成
T+25      audio_post：滤波 → 峰值/响度归一化
T+25      发送语音消息
T+25          ├ 合成失败 / 超时 / 清洗后为空   → {"action":"continue"}
T+25          ├ 发送失败                       → {"action":"continue"}
T+25          └ 发送成功 → mark_sent() 记账
T+25              ├ keep_emoji 为真 → 单独补发原消息里的表情包
T+25              └ {"action":"abort","reason":"..."}
```

### 为什么 abort 必须带 reason

Hook 返回 `{"action": "abort"}` 时如果不给 `reason`，MaiBot 会生成一条工具结果：

> 本次操作被插件主动拦截：未提供原因

Planner 看到这句话会脑补成「插件故障」或「插件抽风」，然后在下一轮对话里把这件事说出来，把内部机制暴露给用户。

所以 `reason` 写的是：

```
本条回复已改用语音发送，文字部分不需要再发，也不需要重试或道歉。
```

这句话同时否掉三个错误分支：重发、重试、道歉。

### 为什么先发语音成功才 abort

`{"action": "abort"}` 会拦掉原文字消息。如果先 abort 再去合成，合成失败时用户就什么都收不到。所以顺序固定为：

```
合成 → 发送 → 确认成功 → abort
```

任何一步失败，一律 `{"action": "continue"}` 保留文字。

### 为什么 emoji 组件必须进白名单

MaiBot 的回复大量带表情包。如果 `allow_components` 不含 `emoji`，几乎所有回复都会在组件检查这一步被跳过，表现为「插件装了但从来不触发」。

### 为什么 min_chars 不能太小

3-6 字的短句只有约 1.6 个有声段，音域被压到 9.6 半音，没有起伏空间，听起来就是念稿。20-30 字才有约 4 个有声段、音域 12 半音。默认 12 是折中值。

### 为什么连续英文词会导致空音频

部分模型（如 CosyVoice2）遇到 `dsh go 没脑子` 这种连续英文词会返回空音频。`clean_for_tts` 默认用 `[A-Za-z]+(?:[\s\-]+[A-Za-z]+)+` 匹配两个以上被空格或连字符分隔的英文单词并清除，`strip_latin` 控制这个行为。

### 音频后处理链

全部零相位（`sosfiltfilt` / `filtfilt`），不引入相位失真：

| 步骤 | 参数 | 目的 |
|---|---|---|
| 1 | 高通 160Hz，四阶 Butterworth | 去掉喷麦低频轰鸣 |
| 2 | 高频搁架 6kHz，-6dB，Q=0.707 | 压下声码器产生的电流音（6-12kHz 宽带噪声） |
| 3 | 低通 11.5kHz，四阶 | 砍掉 Nyquist 附近的毛刺 |
| 4 | 峰值 -1.5dBFS + 响度 -18dBFS | 归一化，tanh 软限幅不引入新失真 |

短于 480 采样点（20ms @ 24kHz）的音频跳过滤波，避免边缘瞬态，只做增益。

三级滤波各自带采样率守卫，`f < nyquist × 0.9`（低通是 `× 0.95`）才启用：

| 采样率 | 高通 160Hz | 搁架 6kHz | 低通 11.5kHz |
|---|---|---|---|
| 24000 Hz（nyquist 12000） | 启用 | 启用 | **跳过**（11500 > 11400） |
| 32000 Hz（nyquist 16000） | 启用 | 启用 | 启用 |

24kHz 音频本身没有 11.5kHz 以上的内容，跳过低通不影响结果。

增益逻辑的几个细节：

| 行为 | 规则 |
|---|---|
| 响度容差 | 现有 RMS 已在 `rms_db ± 1.5dB` 内就不动增益，避免每句都做无意义的浮点运算 |
| 峰值优先 | 响度拉起来后若峰值越过 `peak_db`，以峰值为准重新算增益 |
| 软限幅 | 绝对值超过 0.95 的样本用 `tanh` 压入 `[0.95, peak_limit]`，不硬切 |
| 多声道 | 按声道分别滤波后合并，不降混 |
| 非 16-bit WAV | 原样返回，`info["error"]` 记录原因 |
| `peak_db ≥ 0` | 调用方（`_post_process`）直接跳过整个后处理 |

`normalize_wav_bytes()` 返回 `(bytes, info)`，`info` 里有 `peak_before_db` / `peak_after_db` / `rms_before_db` / `rms_after_db` / `gain`，排查「处理后反而更小」时先看这几个数。

### 口音守卫指标

`core/accent_guard.py` 用自相关跟踪 F0，按 150ms 静音间隔切分有声段，取每段末尾 30% 的 F0 半音斜率中位数：

```
risk = max(0, tail_slope_st) × 3 + tail_rising_ratio × 2 + max(0, 8 - f0_range_st) × 0.5
```

| 指标 | 含义 |
|---|---|
| `tail_slope_st` | 有声段末尾 30% 的 F0 半音斜率中位数，正数 = 尾音往上翘 |
| `tail_rising_ratio` | 尾音斜率 > +2 st/s 的有声段占比 |
| `f0_range_st` | F0 的 p90-p10 音域（半音），太窄显得嗲 |
| `voiced_segments` | 参与计算的有声段数量 |

`risk` 超过 `accent_risk_max`（默认 10.0）就重新合成，最多 `accent_retry` 次。全部重试都超标时采用风险最低的那一版，不会因为口音判定丢掉整条语音。命中 `429` / `5xx` 时立即放弃重试，避免把限流放大成雪崩。

`analyze()` 有四个前置条件，任一不满足就返回 `None`，该次合成直接放行、不做判定也不重试：

| 条件 | 门槛 |
|---|---|
| 容器格式 | 单声道 16-bit WAV（`wave` 可解析） |
| 时长 | ≥ 采样率 ÷ 10（24kHz 下即 ≥ 0.1 秒） |
| 有声帧数 | ≥ 8 帧（帧长 40ms，跳步 20ms） |
| 自相关峰值 | ≥ 0.3 × 零延迟自相关，否则该帧不算有声 |

F0 搜索范围 70–400Hz，有声帧判定用 RMS ≥ 0.01。

---

## 常见问题

### 1. 为什么语音没发出来

按顺序排查：

| 检查点 | 命令 / 位置 |
|---|---|
| 插件是否启用 | `plugin.enabled`，`false` 时 Hook 第一行就 `continue` |
| 概率是否为 0 | `/语音` 看「触发概率」。模型默认 `0.0`（全关），示例模板给的是 `0.3` |
| 是否在冷却中 | `/语音` 看「冷却：N 秒」；冷却中时日志会写「冷却中，还需 N 秒」 |
| 本小时是否超上限 | `/语音` 看「本会话近一小时已发：N 条」，与「每小时上限」对比 |
| 字数是否在窗口外 | 默认 12-50 字，短句和长文都不转 |
| 是否命中跳过正则 | 回复里带链接、`【图片】`、`[CQ:` 等都会跳过 |
| 中文占比是否够 | 默认 0.6，纯英文回复不转 |
| 组件是否被拦 | `allow_components` 默认 `text,at,reply,emoji`，带图片/文件/转发的消息跳过 |
| 该群是否在排除列表 | `/语音` 看「排除群号」 |
| 对应 API Key 是否填了 | `sf_api_key` / `mimo_api_key` 为空时对应模式直接失败 |

最直接的验证：`/语音测试 今天天气不错`。这条跳过所有过滤与限流，如果它能出声，说明引擎正常，问题在过滤链上。

### 2. 为什么某些群不发语音

`auto.exclude_groups` 里有这个群号。用 `/语音群 <群号>` 增删，或在 `config.toml` 里改。也可以检查 `auto.in_group` 是否被关掉——关掉后所有群都不转。

### 3. 本地合成太慢怎么办

| 手段 | 做法 | 效果 |
|---|---|---|
| 换云端 | `voice_mode = "sf"` | 20-24 秒 → 0.7-2.5 秒 |
| 缩短上限 | `max_chars = 30` | 字数减半，时间减半 |
| 提高语速 | `cv_speed = 1.3` | 音频变短，合成时间略降 |
| 降低概率 | `probability = 0.1` | 只在高价值回复上用 |
| 换硬件 | 更多核心 / 更高主频 | 线性改善 |

4 核 ARM 上 28 字约 20-24 秒是实测基线，基本是 1 字/秒。`cv_timeout_seconds` 默认 110 秒，如果经常超时说明单句太长，先压 `max_chars`。

### 4. 语音带电流音 / 爆麦怎么办

| 症状 | 原因 | 处理 |
|---|---|---|
| 电流音（滋滋声） | 声码器在 6-12kHz 的宽带噪声 | 确认 `audio.denoise = true`；不够就把 `LOWSHELF_GAIN_DB` 从 -6.0 调到 -9.0（改 `core/audio_post.py`） |
| 爆麦 / 噗噗声 | 低频喷麦 | 确认高通 160Hz 生效；把 `HIGHPASS_HZ` 提到 200 |
| 结尾毛刺 | Nyquist 附近混叠 | 把 `LOWPASS_HZ` 从 11500 降到 10000 |
| 整体太响削波 | 峰值没限住 | `peak_db` 从 -1.5 降到 -3.0 |
| 整体太轻 | 响度没上来 | `rms_db` 从 -18.0 提到 -16.0 |

`peak_db ≥ 0` 会整个跳过后处理，注意别误设成 0。

### 5. Planner 说插件抽风怎么办

这是 `abort` 没带 `reason` 的典型症状。MaiBot 会生成「本次操作被插件主动拦截：未提供原因」，Planner 把它理解成故障并复述给用户。

处理：确认 Hook 返回的 `abort` 字典里带 `reason` 字段，且内容是给人看的话，不是技术术语。本插件的实现是：

```json
{"action": "abort", "reason": "本条回复已改用语音发送，文字部分不需要再发，也不需要重试或道歉。"}
```

这段文字要同时否掉「重发」「重试」「道歉」三个分支，否则 Planner 会自己补一段解释。

### 6. 语音念出来没有停顿，像念经

模型输出的回复常是「空格分词、无标点」。确认 `style.clean_punctuation = true`（默认就是），它会把空格换行改成逗号并补句末句号。

如果还是太平，把 `style` 里的描述改得更具体，例如加「逗号处停顿 0.3 秒」「句末音调明显下沉」。

### 7. 合成返回空音频

两个常见原因：

- 回复里有连续英文单词（如 `dsh go 没脑子`）。确认 `style.strip_latin = true`。
- 参考音频超过 30 秒或格式不对（`clone` 模式要求单声道、≤10MB base64）。

`/语音测试` 时如果某句话必空，把那句话单独缩短再试。

### 8. 音色不像 / 念出了参考音频的原话

`sf_prompt_text` 与参考音频内容不一致。它必须是逐字转写，包括语气词。填错时模型会把参考音频的原话当成要合成的内容念出来。

`clone` 模式不需要转写文本，但参考音频必须干净：单人、无背景音乐、无混响。

### 9. 语音听起来很嗲、尾音往上翘

开 `audio.accent_retry = 2`，`accent_risk_max` 从 10.0 降到 7.0。每次重试都会增加延迟，所以只对短句开启比较划算。

也可以从源头改：`style` 里加「句子说完音调往下落」，`voice_mode` 换成 `preset` 的 `白桦` 或 `Dean`（男声，音域更宽）。

### 10. 表情包丢了

`auto.keep_emoji` 被关成了 `false`。改回 `true` 后，语音发送成功、`abort` 之前会先把原消息里的 `emoji` 组件单独补发出去。

补发只看 `emoji` 组件，图片、文件、转发不会补发。

### 11. 改了配置不生效

按修改入口分：

| 操作 | 生效方式 |
|---|---|
| 用 `/语音频率`、`/语音冷却`、`/语音上限`、`/语音群`、`/语音开关` | 立即生效，同时写回 `config.toml`，重启不丢 |
| 手改 `config.toml` | 触发 `on_config_update`，同步引擎参数、清空 SF 音色缓存、重扫 `voices/`，无需重启进程 |
| WebUI 配置页 | 同样走 `on_config_update` |

如果改了但行为没变，看日志：

- `配置校验失败，改动未生效：...` — 新值没通过 Pydantic 模型校验，多半是类型不对或枚举值拼错。
- `跳过语音：命中跳过正则（链接/占位符/转发）`
- `跳过语音：含…的组件: ['image']` — 方括号里是出现在消息中、但不在 `allow_components` 里的组件类型
- `跳过语音：中文占比 0.31 低于 0.60`
- `跳过语音：冷却中，还需 18 秒`
- `跳过语音：本小时已达上限 60 条`

这些字符串由 `text_utils.blocked_reason()` 与 `RateLimiter.blocked_reason()` 直接生成，搜日志里的「跳过语音」能定位到具体哪一环。

### 12. 提示「未配置 SiliconFlow API Key」

`sf` 模式需要 `engine.sf_api_key` 以 `sk-` 开头。`clone` / `preset` / `design` 需要 `engine.mimo_api_key`。两者不通用。

### 13. 语音里夹着数字、念得像乱码

这是 `cvcpp` 引擎的历史 bug，1.0.0 已修。

aiohttp 的 `json=` 参数默认 `ensure_ascii=True`，中文会被转成 `\u4eca\u5929` 这样的转义序列。早期配套的本地服务端在解码 `\uXXXX` 时只跳过了 `\u` 两个字符，剩下的四位十六进制数字被当成正文追加，于是「今天」变成「今4eca天4e0b」，TTS 照着念出一串数字，音频时长翻三倍。

现象特征：每次合成出的音频比正常长 2–3 倍，每字音频超过 0.4 秒（正常 0.18–0.25 秒）。

修法：客户端改用 `data=json.dumps(..., ensure_ascii=False).encode("utf-8")` 直接发 UTF-8。如果你自己写了对接 `cvcpp` 的服务端，也要检查 `\uXXXX` 的解码逻辑——正确做法是解码后 `p += 6`，跳过 `\u` 加四位数字。

验证方式：同一个短句分别用转义和非转义两种方式请求，音频时长应该基本一致（差异在 ±10% 内属正常随机波动）。

---

## 许可证汇总

| 组件 | 许可证 | 用途 |
|---|---|---|
| 本插件 `maibot-voice-reply` | **MIT** | 插件本体 |
| `core/audio_post.py` | **MIT** | 本项目原创 |
| `core/accent_guard.py` | **MIT** | 本项目原创 |
| `numpy` | **BSD-3-Clause** | 音频数组运算 |
| `scipy` | **BSD-3-Clause** | 滤波器设计与零相位滤波 |
| `aiohttp` | **Apache-2.0** | 异步 HTTP 客户端 |
| MaiBot 主程序（运行宿主） | **GPL-3.0** | 插件运行环境 |
| `maibot-plugin-sdk`（运行宿主） | **LGPL-3.0-only** | 插件 SDK |
| `cosyvoice.cpp`（Lourdle/cosyvoice.cpp，可选后端） | **MIT** | 本地 TTS 服务 |
| Fun-CosyVoice3-0.5B 权重（FunAudioLLM，可选后端） | **Apache-2.0** | 本地模型权重 |
| CosyVoice2-0.5B（SiliconFlow 托管，可选后端） | **Apache-2.0** | 云端 TTS |
| 小米 MiMo TTS（可选后端） | 商业 API，按其服务条款 | 云端 TTS |

### 关于 MIT 与 GPL-3.0 的边界

本插件通过 SDK 的公开接口与主程序交互，不链接、不修改主程序源码，因此以 MIT 分发。

使用者若把本插件与 GPL-3.0 的 MaiBot 一同分发，整体分发物需遵守 GPL-3.0。单独分发本插件不受此约束。

完整第三方声明见 [NOTICE](NOTICE)。

---

## 许可

[MIT](LICENSE) © 2026 xingranya
