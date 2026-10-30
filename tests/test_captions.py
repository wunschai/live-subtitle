import pytest

from livesub.core.captions import PENDING_MARK, CaptionBoard, composition


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def texts(snap):
    return [b.text for b in snap.blocks]


def test_modes_with_translation():
    b = CaptionBoard()
    line = b.add_final(1, "raw", "大家好。", "zh")
    b.set_translation(line.id, "en", "Hello everyone.")
    assert texts(b.snapshot("original")) == ["大家好。"]
    assert texts(b.snapshot("zh")) == ["大家好。"]
    assert texts(b.snapshot("en")) == ["Hello everyone."]
    assert texts(b.snapshot("bilingual")) == ["大家好。", "Hello everyone."]
    assert [blk.lang for blk in b.snapshot("bilingual").blocks] == ["zh", "en"]


def test_pending_translation_display():
    b = CaptionBoard()
    b.add_final(1, "raw", "Good morning.", "en")
    # 單語模式：先以淡色原文暫代
    zh = b.snapshot("zh").blocks[0]
    assert zh.text == "Good morning." and zh.spans[0].dim
    # 雙語模式：中文行顯示「…」
    bi = b.snapshot("bilingual")
    assert bi.blocks[0].text == PENDING_MARK and bi.blocks[1].text == "Good morning."


def test_translation_failure_falls_back_to_original():
    b = CaptionBoard()
    line = b.add_final(1, "raw", "Good morning.", "en")
    b.set_translation(line.id, "zh", None)
    blk = b.snapshot("bilingual").blocks[0]
    assert blk.text == "Good morning." and blk.spans[0].dim


def test_partial_rejected_after_final():
    b = CaptionBoard()
    assert b.set_partial(1, "大家", "zh")
    b.add_final(1, "raw", "大家好。", "zh")
    assert b.partial is None
    assert not b.set_partial(1, "大家好嗎", "zh")  # 晚到的舊 partial
    assert texts(b.snapshot("zh")) == ["大家好。"]
    assert b.set_partial(2, "今天", "zh")


def test_partial_for_older_utt_rejected():
    b = CaptionBoard()
    b.add_final(5, "raw", "第五句", "zh")
    assert not b.set_partial(3, "舊的", "zh")


def test_partial_shown_dim_and_routed_by_language():
    b = CaptionBoard()
    b.add_final(1, "r", "大家好。", "zh")
    b.set_partial(2, "Today we", "en")
    snap = b.snapshot("bilingual")
    assert snap.blocks[0].text == "大家好。"  # 中文行不出現英文 partial
    assert snap.blocks[1].spans[-1].text == "Today we" and snap.blocks[1].spans[-1].dim
    # 單語模式任何語言的 partial 都先顯示
    assert snap.blocks and b.snapshot("zh").blocks[0].spans[-1].text == "Today we"


def test_set_translation_on_evicted_line():
    b = CaptionBoard(keep_lines=2)
    first = b.add_final(1, "r", "一", "zh")
    b.add_final(2, "r", "二", "zh")
    b.add_final(3, "r", "三", "zh")
    assert b.get(first.id) is None
    assert not b.set_translation(first.id, "en", "one")


def test_empty_final_only_clears_partial():
    b = CaptionBoard()
    b.set_partial(1, "嗯", "zh")
    assert b.add_final(1, "嗯", "", "zh") is None
    assert b.partial is None and b.lines == []
    assert b.snapshot("zh").empty


def test_cancel_partial():
    b = CaptionBoard()
    b.set_partial(4, "咳", "zh")
    b.cancel_partial(4)
    assert b.partial is None
    assert not b.set_partial(4, "咳咳", "zh")


def test_visibility_fades_after_idle():
    clock = Clock()
    b = CaptionBoard(clock=clock)
    b.add_final(1, "r", "大家好", "zh")
    assert b.snapshot("zh", clear_after_s=6).visible
    clock.t += 6.5
    assert not b.snapshot("zh", clear_after_s=6).visible
    b.set_partial(2, "下", "zh")
    assert b.snapshot("zh", clear_after_s=6).visible
    clock.t += 100
    assert b.snapshot("zh", clear_after_s=6).visible  # 有 partial 時不淡出
    assert b.snapshot("zh", clear_after_s=0).visible  # 0 = 不自動清除


def test_translation_counts_as_activity():
    clock = Clock()
    b = CaptionBoard(clock=clock)
    line = b.add_final(1, "r", "大家好", "zh")
    clock.t += 5
    b.set_translation(line.id, "en", "Hello")
    clock.t += 3
    assert b.snapshot("en", clear_after_s=6).visible


def test_english_joined_with_spaces_chinese_without():
    b = CaptionBoard()
    b.add_final(1, "r", "Hello.", "en")
    b.add_final(2, "r", "World.", "en")
    b.add_final(3, "r", "你好。", "zh")
    b.add_final(4, "r", "世界。", "zh")
    orig = b.snapshot("original").blocks[0].text
    assert "Hello. World." in orig and "你好。世界。" in orig


def test_revision_increases():
    b = CaptionBoard()
    r0 = b.revision
    b.set_partial(1, "x", "en")
    assert b.revision > r0


@pytest.mark.parametrize("text,comp", [
    ("大家好，歡迎參加工作坊", "zh"),
    ("Good morning everyone", "en"),
    ("我們用 open access 的方式出版這本書", "mixed"),
    ("這個 API 的回應時間大概是兩百毫秒", "zh"),
])
def test_composition(text, comp):
    assert composition(text) == comp
