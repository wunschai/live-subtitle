import pytest

from livesub.core import textproc as tp


def test_simplified_to_taiwan():
    assert tp.to_traditional("我们今天要介绍图书馆的软件") == "我們今天要介紹圖書館的軟體"
    assert tp.to_traditional("软件", taiwan_phrases=False) == "軟件"


def test_fix_spacing_fullwidth_punct():
    assert tp.fix_spacing("大家好,歡迎參加.") == "大家好，歡迎參加。"
    assert tp.fix_spacing("Hello,  world.") == "Hello, world."
    assert tp.fix_spacing("版本 3.5 很好") == "版本 3.5 很好"


@pytest.mark.parametrize("text", [
    "謝謝觀看", "谢谢观看!", "字幕由Amara.org社区提供", "請不吝點贊訂閱轉發打賞支持明鏡與點點欄目",
    "Thanks for watching!", "[音樂]", "(掌聲)", "   ", "...",
])
def test_strong_hallucinations_always_dropped(text):
    assert tp.clean(text, confident=True) == ""


@pytest.mark.parametrize("text", ["謝謝大家", "Thank you.", "謝謝"])
def test_weak_hallucinations_kept_when_confident(text):
    assert tp.clean(text, confident=True) != ""
    assert tp.clean(text, confident=False) == ""


@pytest.mark.parametrize("text", [
    "謝謝大家今天來參加工作坊", "Thank you for coming to the workshop today",
    "我們今天的主題是開放取用",
])
def test_normal_sentences_kept_even_low_confidence(text):
    assert tp.clean(text, confident=False) != ""


def test_collapse_repeats_only_loops():
    assert tp.collapse_repeats("我我我我我想說") == "我想說"
    assert tp.collapse_repeats("好的好的好的好的好的") == "好的"
    assert tp.collapse_repeats("the the the the cat") == "the cat"
    # 3 次以內是正常強調，保留
    assert tp.collapse_repeats("好好好") == "好好好"
    assert tp.collapse_repeats("對對對，就是這樣") == "對對對，就是這樣"
    assert tp.collapse_repeats("very very good") == "very very good"


@pytest.mark.parametrize("text,lang", [
    ("大家好，歡迎參加今天的工作坊", "zh"),
    ("Good morning everyone", "en"),
    ("這個 API 的回應時間大概是兩百毫秒", "zh"),
    ("我們用 open access 的方式出版", "zh"),
    ("The 圖書館 is open", "en"),
    ("", "zh"),
])
def test_detect_lang(text, lang):
    assert tp.detect_lang(text) == lang


def test_detect_lang_hint_for_empty():
    assert tp.detect_lang("  ", hint="en") == "en"


def test_clean_converts_and_formats():
    assert tp.clean("大家好,欢迎参加今天的工作坊。") == "大家好，歡迎參加今天的工作坊。"
    assert tp.clean(" Good morning everyone. ") == "Good morning everyone."


@pytest.mark.parametrize("text", [
    "預算是10000元", "人口大約23000000人", "The price is 10000 dollars.", "電話0800000123",
    "代碼12121212", "版本 1.1.1.1", "2000年",
])
def test_numbers_are_never_collapsed(text):
    """回歸：審查員 A 發現 10000 被截成 10（字幕數字錯一個數量級）。"""
    assert tp.collapse_repeats(text) == text
    assert any(ch.isdigit() for ch in tp.clean(text))
    assert "".join(c for c in tp.clean(text) if c.isdigit()) == "".join(c for c in text if c.isdigit())
