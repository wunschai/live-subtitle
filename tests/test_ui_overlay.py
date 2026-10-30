from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QAbstractAnimation, Qt
from PySide6.QtGui import QFontMetrics, QGuiApplication

from livesub.config import DisplaySettings
from livesub.core.captions import Block, BoardSnapshot, Span
from livesub.ui.overlay import OverlayWindow
from livesub.ui.theme import OVERLAY_BLOCK_GAP, OVERLAY_PADDING

pytestmark = pytest.mark.gui


def snapshot(text: str, bilingual: bool = False, visible: bool = True):
    blocks = [Block("zh", (Span(text),))]
    if bilingual:
        blocks.append(Block("en", (Span("Latest translation", dim=True),)))
    return BoardSnapshot(tuple(blocks), visible, 1)


@pytest.fixture
def overlay(qtbot):
    window = OverlayWindow(DisplaySettings())
    qtbot.addWidget(window)
    return window


def test_flags(overlay):
    flags = overlay.windowFlags()
    for flag in (
        Qt.WindowType.FramelessWindowHint,
        Qt.WindowType.WindowStaysOnTopHint,
        Qt.WindowType.WindowTransparentForInput,
        Qt.WindowType.WindowDoesNotAcceptFocus,
    ):
        assert flags & flag
    assert flags & Qt.WindowType.WindowType_Mask == Qt.WindowType.Tool
    for attribute in (
        Qt.WidgetAttribute.WA_TranslucentBackground,
        Qt.WidgetAttribute.WA_ShowWithoutActivating,
        Qt.WidgetAttribute.WA_TransparentForMouseEvents,
    ):
        assert overlay.testAttribute(attribute)


@pytest.mark.parametrize("position", ["top", "bottom"])
@pytest.mark.parametrize("width_pct,margin,font_size,max_lines", [
    (30, 0, 16, 1),
    (80, 40, 36, 2),
    (100, 75, 64, 3),
])
def test_geometry(overlay, position, width_pct, margin, font_size, max_lines):
    settings = DisplaySettings(
        position=position,
        width_pct=width_pct,
        margin_px=margin,
        font_size=font_size,
        max_lines=max_lines,
    )
    overlay.apply_settings(settings)
    screen = QGuiApplication.screens()[settings.screen].geometry()
    actual = overlay.target_geometry()
    expected_height = (
        max_lines * QFontMetrics(overlay.font()).lineSpacing()
        + 2 * OVERLAY_PADDING
    )
    assert actual.width() == round(screen.width() * width_pct / 100)
    assert actual.x() == screen.left() + (screen.width() - actual.width()) // 2
    assert actual.height() == expected_height
    expected_y = (
        screen.top() + margin
        if position == "top"
        else screen.bottom() - margin - expected_height
    )
    assert actual.y() == expected_y
    assert overlay.geometry() == actual


def test_height_depends_on_rows_and_blocks(overlay):
    settings = DisplaySettings(max_lines=1)
    overlay.apply_settings(settings)
    line_height = QFontMetrics(overlay.font()).lineSpacing()
    single = overlay.target_geometry().height()

    overlay.apply_settings(replace(settings, max_lines=3))
    assert overlay.target_geometry().height() == single + 2 * line_height

    overlay.show_snapshot(snapshot("中文", bilingual=True))
    assert overlay.target_geometry().height() == (
        6 * line_height + OVERLAY_BLOCK_GAP + 2 * OVERLAY_PADDING
    )

    overlay.show_snapshot(snapshot("中文"))
    assert overlay.target_geometry().height() == 3 * line_height + 2 * OVERLAY_PADDING


def test_preallocate_blocks(overlay):
    overlay.set_block_count(2)
    before = overlay.target_geometry()
    overlay.show_snapshot(snapshot("新內容", bilingual=True))
    assert overlay.target_geometry() == before


def test_long_text_keeps_fixed_height_and_tail(overlay):
    overlay.apply_settings(DisplaySettings(max_lines=2, width_pct=30))
    overlay.show_snapshot(snapshot("短句"))
    expected = overlay.target_geometry()

    text = "舊字幕 " * 3000 + "最新內容 😀"
    overlay.show_snapshot(snapshot(text))
    assert overlay.target_geometry() == expected
    assert overlay._paths
    line_height = QFontMetrics(overlay.font()).lineSpacing()
    for path, _ in overlay._paths:
        assert path.boundingRect().top() >= OVERLAY_PADDING - line_height * 0.25
        assert path.boundingRect().bottom() <= (
            OVERLAY_PADDING + 2 * line_height + line_height * 0.25
        )
    overlay.show()
    assert not overlay.grab().isNull()


def test_out_of_range_screen_uses_primary(overlay):
    settings = DisplaySettings(screen=999, position="top", margin_px=17)
    overlay.apply_settings(settings)
    screen = QGuiApplication.primaryScreen().geometry()
    result = overlay.target_geometry()
    assert result.width() == round(screen.width() * settings.width_pct / 100)
    assert result.x() == screen.left() + (screen.width() - result.width()) // 2
    assert result.y() == screen.top() + 17


def test_visible_false_starts_fade_and_can_reverse(overlay, qtbot):
    overlay.show_snapshot(snapshot("字幕"))
    overlay.fade_animation.setCurrentTime(300)
    assert overlay.windowOpacity() == pytest.approx(1.0, abs=0.01)

    overlay.show_snapshot(snapshot("字幕", visible=False))
    assert overlay.fade_animation.duration() == 300
    assert overlay.fade_animation.endValue() == 0.0
    assert overlay.fade_animation.state() == QAbstractAnimation.State.Running

    qtbot.waitUntil(
        lambda: overlay.fade_animation.state() == QAbstractAnimation.State.Stopped,
        timeout=1500,
    )
    assert overlay.windowOpacity() == pytest.approx(0.0, abs=0.01)
    overlay.show_snapshot(snapshot("下一句"))
    assert overlay.fade_animation.endValue() == 1.0
    assert overlay.fade_animation.state() == QAbstractAnimation.State.Running


def test_empty_box_paints_nothing(overlay):
    overlay.show_snapshot(BoardSnapshot((Block("zh", (Span("   "),)),), True, 1))
    assert overlay._paths == []
    overlay.setWindowOpacity(1.0)
    image = overlay.grab().toImage()
    assert image.pixelColor(image.width() // 2, image.height() // 2).alpha() == 0


def test_dim_and_unicode_spans(overlay):
    overlay.show_snapshot(BoardSnapshot(
        (Block("zh", (Span("😀 正式字幕 "), Span("暫定文字", dim=True))),),
        True,
        1,
    ))
    assert any(dim for _, dim in overlay._paths)
    assert any(not dim for _, dim in overlay._paths)
    assert not overlay.grab().isNull()


# ---- 整句截斷（審查回合 2：第一行不可從句子中間開始） ------------------------

def _block_snapshot(lang, parts, partial=None):
    spans = []
    joiner = " " if lang == "en" else ""
    for i, t in enumerate(parts):
        if i:
            spans.append(Span(joiner))
        spans.append(Span(t))
    if partial:
        spans += [Span(joiner, dim=True), Span(partial, dim=True)]
    return BoardSnapshot(blocks=(Block(lang, tuple(spans)),), visible=True, revision=1)


def _narrow(overlay, max_lines=2):
    overlay.apply_settings(replace(overlay._settings, width_pct=50, font_size=24, max_lines=max_lines))


def test_overflow_drops_whole_old_sentences_en(overlay):
    _narrow(overlay)
    sents = [f"Sentence number {i} is short." for i in range(12)]
    overlay.show_snapshot(_block_snapshot("en", sents))
    shown = overlay._visible_texts[0]
    assert shown.startswith("Sentence number"), shown  # 從句首開始
    assert shown.endswith(sents[-1])  # 最新一句一定在
    assert sents[0] not in shown  # 舊的整句被丟掉


def test_overflow_drops_whole_old_sentences_zh_with_partial(overlay):
    _narrow(overlay)
    sents = [f"第{i}句介紹資料庫。" for i in range(12)]
    overlay.show_snapshot(_block_snapshot("zh", sents, partial="接下來看第二個部分"))
    shown = overlay._visible_texts[0]
    assert shown.startswith("第"), shown
    assert shown.endswith("接下來看第二個部分")
    assert "第0句" not in shown


def test_single_sentence_too_long_keeps_its_tail(overlay):
    _narrow(overlay, max_lines=1)
    long = "This one sentence is far too long to fit on a single narrow line so only its tail is shown END"
    overlay.show_snapshot(_block_snapshot("en", ["Old.", long]))
    shown = overlay._visible_texts[0]
    assert shown.rstrip().endswith("END") and "Old." not in shown


def test_short_content_shown_whole(overlay):
    overlay.show_snapshot(_block_snapshot("en", ["Hello.", "World."]))
    assert overlay._visible_texts[0] == "Hello. World."
