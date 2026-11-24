"""core/text.py 单元测试。"""

from __future__ import annotations

import pytest

from core import text as T


class TestChineseRatio:
    def test_pure_chinese(self) -> None:
        assert T.chinese_ratio("你好世界") == 1.0

    def test_mixed(self) -> None:
        assert T.chinese_ratio("你好abc") == pytest.approx(0.4)

    def test_empty(self) -> None:
        assert T.chinese_ratio("") == 0.0

    def test_only_spaces(self) -> None:
        assert T.chinese_ratio("   ") == 0.0


class TestCleanForTTS:
    def test_spaces_become_commas(self) -> None:
        assert T.clean_for_tts("今天 下班 早") == "今天，下班，早。"

    def test_existing_punctuation_kept(self) -> None:
        assert T.clean_for_tts("你好。我来了") == "你好。我来了。"

    def test_markdown_stripped(self) -> None:
        out = T.clean_for_tts("**重要** 这件事")
        assert "*" not in out
        assert "重要" in out

    def test_url_removed(self) -> None:
        out = T.clean_for_tts("看这个 https://b23.tv/x 笑死")
        assert "b23" not in out
        assert "笑死" in out

    def test_multi_latin_removed(self) -> None:
        out = T.clean_for_tts("这个 dsh go 没脑子")
        assert "dsh" not in out
        assert "没脑子" in out

    def test_single_latin_kept(self) -> None:
        out = T.clean_for_tts("喝杯 coco 吧")
        assert "coco" in out

    def test_placeholder_removed(self) -> None:
        out = T.clean_for_tts("【图片】看到了 [文件] 收到")
        assert "图片" not in out
        assert "收到" in out

    def test_empty_after_clean(self) -> None:
        assert T.clean_for_tts("https://x.com") == ""

    def test_punctuation_only(self) -> None:
        assert T.clean_for_tts("。。。") == ""

    def test_no_fix_punctuation(self) -> None:
        out = T.clean_for_tts("今天 下班", fix_punctuation=False)
        assert out == "今天 下班"

    def test_no_strip_latin(self) -> None:
        out = T.clean_for_tts("这个 dsh go 没脑子", strip_latin=False)
        assert "dsh" in out

    def test_trailing_period_added(self) -> None:
        assert T.clean_for_tts("走了").endswith("。")

    def test_exclamation_kept(self) -> None:
        out = T.clean_for_tts("太好了！")
        assert out.endswith("！")

    def test_multiple_spaces_collapsed(self) -> None:
        assert "，，" not in T.clean_for_tts("今天   下班")


class TestComponentTypes:
    def test_extract(self) -> None:
        msg = {"raw_message": [{"type": "text"}, {"type": "Emoji"}]}
        assert T.component_types(msg) == {"text", "emoji"}

    def test_missing_raw(self) -> None:
        assert T.component_types({}) == set()

    def test_raw_not_list(self) -> None:
        assert T.component_types({"raw_message": "x"}) == set()


class TestParseCsv:
    def test_comma(self) -> None:
        assert T.parse_csv("1,2,3") == ["1", "2", "3"]

    def test_chinese_comma(self) -> None:
        assert T.parse_csv("1，2") == ["1", "2"]

    def test_empty(self) -> None:
        assert T.parse_csv("") == []

    def test_whitespace(self) -> None:
        assert T.parse_csv(" 1 , 2 ") == ["1", "2"]


class TestBlockedReason:
    def _msg(self, *types: str) -> dict:
        return {"raw_message": [{"type": t} for t in types]}

    def test_pass(self) -> None:
        reason = T.blocked_reason(
            "今天下班早我买了点水果",
            self._msg("text"),
            skip_regex=None,
            min_chinese_ratio=0.6,
            allowed_components=["text", "emoji"],
        )
        assert reason == ""

    def test_url_blocked(self) -> None:
        regex = T.compile_skip_regex(r"https?://")
        reason = T.blocked_reason(
            "看 https://x.com",
            self._msg("text"),
            skip_regex=regex,
            min_chinese_ratio=0.0,
            allowed_components=["text"],
        )
        assert "正则" in reason

    def test_component_blocked(self) -> None:
        reason = T.blocked_reason(
            "今天下班早",
            self._msg("text", "image"),
            skip_regex=None,
            min_chinese_ratio=0.0,
            allowed_components=["text"],
        )
        assert "image" in reason

    def test_chinese_ratio_blocked(self) -> None:
        reason = T.blocked_reason(
            "abc def",
            self._msg("text"),
            skip_regex=None,
            min_chinese_ratio=0.6,
            allowed_components=["text"],
        )
        assert "中文占比" in reason

    def test_empty_allowed_passes_all(self) -> None:
        reason = T.blocked_reason(
            "abc",
            self._msg("image"),
            skip_regex=None,
            min_chinese_ratio=0.0,
            allowed_components=[],
        )
        assert reason == ""


class TestCompileSkipRegex:
    def test_valid(self) -> None:
        assert T.compile_skip_regex(r"\d+") is not None

    def test_empty(self) -> None:
        assert T.compile_skip_regex("") is None

    def test_invalid(self) -> None:
        assert T.compile_skip_regex("([") is None
