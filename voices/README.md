# 参考音频目录

把音色复刻用的音频放在这里，支持 `.wav` 与 `.mp3`。

## 命名

文件名（不含扩展名）就是配置里引用的音色名。例如放 `friend.wav`，
配置写 `clone_voice = "friend"`。

## 规格

| 项 | 建议 |
|---|---|
| 时长 | 10–60 秒。超过 30 秒时用 mp3 压到 1MB 以内，否则 base64 会超过 MiMo 的 10MB 上限 |
| 采样率 | 24kHz 以上，单声道 |
| 内容 | 一段连续自然的口语，不要念稿、不要带背景音乐 |
| 音量 | 峰值 −3dB 到 −6dB，避免削波 |

## 转换

```bash
# wav 转 mp3，32kbps 单声道，60 秒约 240KB
ffmpeg -i input.wav -ac 1 -ar 24000 -b:a 32k friend.mp3
```

## 不入库

`.gitignore` 已排除 `voices/*.wav` 与 `voices/*.mp3`。音频涉及个人声音样本，
不随仓库分发，需要自行放置。
