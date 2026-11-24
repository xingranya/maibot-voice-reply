# 测试

175 项单元测试，不依赖 MaiBot 运行时、不发真实网络请求。

## 跑法

```bash
python3 -m venv .venv
.venv/bin/pip install pytest pydantic aiohttp numpy scipy
.venv/bin/python -m pytest tests/ -q
```

## 结构

| 文件 | 覆盖 |
|---|---|
| `test_text.py` | 中文占比、文本清洗（空格改逗号、Markdown 剥离、连续英文清理）、组件类型提取、内容过滤判定、正则编译 |
| `test_ratelimit.py` | 冷却、每小时配额、时间窗滑动、多会话隔离、统计与重置 |
| `test_audio_post.py` | 峰值归一化、响度、立体声与采样率保留、高通/低通滤波效果、静音与削波边界 |
| `test_accent_guard.py` | 时长与格式守卫、音域检测、扫频信号的尾音斜率符号、风险值单调性 |
| `test_tts.py` | base64 归一化、默认端点、无 Key 守卫、MiMo 请求体结构（文本在 assistant、标签前缀、speed 省略、voicedesign 无 voice 字段）、响应解析 |
| `test_plugin.py` | 钩子判定链（开关/概率/字数/群私聊/排除群/过滤/限流）、失败一律保留文字、abort 带 reason、表情补发、7 个命令、Tool 边界、音色解析、风格轮换、API |

## 桩

`tests/stubs/maibot_sdk/` 是 SDK 的最小替身：`Field` 转发 pydantic，`PluginConfigBase`
继承 `BaseModel`（保证嵌套配置段与校验行为一致），装饰器只附着元数据不注册。

`tests/conftest.py` 以包名 `maibot_voice_reply` 加载 `plugin.py`，让其中的
`from .core import ...` 相对导入可用，同时把模块暴露成 `plugin`。
