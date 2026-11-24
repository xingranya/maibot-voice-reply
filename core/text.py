"""文本清洗与内容过滤。

模型生成的回复常常是「空格分词、无标点、带 Markdown 标记」的形态，直接送进 TTS
会念成一口气没有停顿的长句，甚至会因为连续英文单词触发部分模型的空音频 bug。
这里统一做归一化，并给出「这条该不该转语音」的判断。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# Markdown / 占位符残留：[图片] 【文件】 *粗体* `代码` 等
_MARKUP_RE = re.compile(r"\[[^\]]{0,40}\]|【[^】]{0,40}】")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_STAR_RE = re.compile(r"[*_~`]+")
# 两个以上被空格/连字符分隔的英文单词：部分模型会直接返回空音频
_MULTI_LATIN_RE = re.compile(r"[A-Za-z]+(?:[\s\-]+[A-Za-z]+)+")
_SPACE_RE = re.compile(r"[\s\u3000]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def chinese_ratio(text: str) -> float:
    """中文字符占非空白字符的比例，0~1。"""
    stripped = re.sub(r"\s+", "", text or "")
    if not stripped:
        return 0.0
    return len(_CJK_RE.findall(stripped)) / len(stripped)


def clean_for_tts(
    text: str,
    *,
    fix_punctuation: bool = True,
    strip_latin: bool = True,
) -> str:
    """把回复文本整理成适合朗读的形态。

    Args:
        text: 原始回复文本。
        fix_punctuation: 是否把空格/换行改成逗号并补句末标点。
        strip_latin: 是否清理连续英文单词。

    Returns:
        str: 清洗后的文本；无有效内容时返回空字符串。
    """
    if not text:
        return ""

    out = str(text)
    out = _MARKUP_RE.sub(" ", out)
    out = _URL_RE.sub(" ", out)
    out = _STAR_RE.sub("", out)
    if strip_latin:
        out = _MULTI_LATIN_RE.sub(" ", out)

    if fix_punctuation:
        # 空格与换行改成逗号，连续标点收敛成一个
        out = _SPACE_RE.sub("，", out)
        out = re.sub(r"，{2,}", "，", out)
        out = re.sub(r"[，、]{1}(?=[。！？!?])", "", out)
        # 只剥开头的标点，末尾的句号问号要留着——strip 会把「太好了！」削成「太好了」
        out = out.lstrip("，、。！？!?；;：: ")
        out = out.rstrip("，、；;：: ")
        if out and out[-1] not in "。！？!?～~…":
            out += "。"
    else:
        out = _SPACE_RE.sub(" ", out).strip()

    # 只有标点没有实际内容
    if not re.search(r"[\w\u4e00-\u9fff]", out):
        return ""
    return out


def component_types(message: dict[str, Any]) -> set[str]:
    """取出原始消息里出现的组件类型集合。"""
    raw = message.get("raw_message")
    if not isinstance(raw, list):
        return set()
    kinds: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            kind = str(item.get("type") or "").strip().lower()
            if kind:
                kinds.add(kind)
    return kinds


def parse_csv(value: str) -> list[str]:
    """把逗号/中文逗号分隔的字符串拆成去空的列表。"""
    if not value:
        return []
    parts = re.split(r"[,，]", str(value))
    return [p.strip() for p in parts if p.strip()]


def blocked_reason(
    text: str,
    message: dict[str, Any],
    *,
    skip_regex: Optional[re.Pattern[str]],
    min_chinese_ratio: float,
    allowed_components: Iterable[str],
) -> str:
    """判断本条消息是否应当跳过自动语音，返回跳过原因（空串表示可以发）。

    Args:
        text: 待朗读文本。
        message: 序列化后的消息字典。
        skip_regex: 已编译的跳过正则。
        min_chinese_ratio: 中文占比下限，0 表示不限制。
        allowed_components: 允许的组件类型集合。
    """
    if skip_regex is not None and skip_regex.search(text):
        return "命中跳过正则（链接/占位符/转发）"

    allowed = {c.strip().lower() for c in allowed_components if c and c.strip()}
    if allowed:
        extra = component_types(message) - allowed
        if extra:
            return f"含不允许的组件: {sorted(extra)}"

    if min_chinese_ratio > 0:
        ratio = chinese_ratio(text)
        if ratio < min_chinese_ratio:
            return f"中文占比 {ratio:.2f} 低于 {min_chinese_ratio:.2f}"

    return ""


def compile_skip_regex(pattern: str) -> Optional[re.Pattern[str]]:
    """编译跳过正则，非法时返回 None（不阻断发送）。"""
    if not pattern or not pattern.strip():
        return None
    try:
        return re.compile(pattern)
    except re.error:
        return None
