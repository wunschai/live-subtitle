import numpy as np

from livesub.core.segmenter import FRAME_S, Segmenter, SegmenterConfig
from livesub.core.vad import FRAME


def frames(n, value=0.0):
    return np.full((n, FRAME), value, np.float32)


def run(seg, probs):
    """逐幀餵入，每幀的音訊值設為幀序號，方便檢查切點。"""
    events = []
    for i, p in enumerate(probs):
        events += seg.feed(np.full((1, FRAME), float(i), np.float32), np.array([p]))
    return events


def secs(s):
    return round(s / FRAME_S)


def test_silence_produces_nothing():
    seg = Segmenter()
    assert run(seg, [0.0] * secs(5)) == []


def test_single_utterance_final_with_preroll_and_tail():
    cfg = SegmenterConfig()
    seg = Segmenter(cfg)
    probs = [0.0] * secs(1) + [0.9] * secs(1.0) + [0.0] * secs(1)
    ev = run(seg, probs)
    assert [e.kind for e in ev] == ["final"]
    f = ev[0]
    first_frame = int(f.audio[0])
    speech_start = secs(1)
    # pre-roll：包含語音開始前約 0.4 秒
    assert speech_start - secs(cfg.preroll_s) - 1 <= first_frame < speech_start
    # 句尾保留約 tail_pad_s 的靜音
    last_frame = int(f.audio[-1])
    speech_end = speech_start + secs(1.0) - 1
    assert speech_end < last_frame <= speech_end + secs(cfg.tail_pad_s) + 1
    assert f.voiced_ratio > 0.5 and not f.forced


def test_blip_shorter_than_start_frames_ignored():
    seg = Segmenter()
    assert run(seg, [0.0] * 10 + [0.9, 0.9] + [0.0] * secs(2)) == []


def test_short_word_still_emitted():
    # 「好」約 0.25 秒：min_speech_s=0.2 不能把它丟掉
    seg = Segmenter()
    ev = run(seg, [0.0] * 10 + [0.9] * secs(0.25) + [0.0] * secs(1))
    assert [e.kind for e in ev] == ["final"]


def test_two_utterances_get_increasing_ids():
    seg = Segmenter()
    probs = ([0.9] * secs(1) + [0.0] * secs(0.8)) * 2
    ev = run(seg, [0.0] * 5 + probs)
    assert [e.kind for e in ev] == ["final", "final"]
    assert ev[0].utt_id < ev[1].utt_id


def test_batch_feed_keeps_event_order():
    """同一批音訊內同時有句尾與新句開始：兩個事件都要依序出現（WhisperLiveKit #363 的教訓）。"""
    cfg = SegmenterConfig(partial_interval_s=0.3, partial_min_s=0.3)
    seg = Segmenter(cfg)
    probs = [0.9] * secs(1) + [0.0] * secs(0.7) + [0.9] * secs(1) + [0.0] * secs(0.8)
    ev = seg.feed(frames(len(probs)), np.array(probs))
    kinds = [e.kind for e in ev]
    assert kinds.count("final") == 2
    ids = [e.utt_id for e in ev]
    assert ids == sorted(ids)


def test_force_cut_at_max_length_prefers_gap():
    cfg = SegmenterConfig(max_segment_s=4.0)
    seg = Segmenter(cfg)
    # 3.0 秒時有一個 4 幀的小停頓（低於 end_threshold，但不到句尾靜音長度）
    probs = [0.9] * secs(3.0) + [0.1] * 4 + [0.9] * secs(3.0) + [0.0] * secs(1)
    ev = run(seg, [0.0] * 3 + probs)
    finals = [e for e in ev if e.kind == "final"]
    assert finals[0].forced
    cut_frame = int(finals[0].audio[-1])
    gap_start = 3 + secs(3.0)
    assert gap_start <= cut_frame <= gap_start + 4
    # 剩下的音訊延續到下一段（不遺失）
    assert int(finals[1].audio[0]) == cut_frame + 1
    assert finals[1].utt_id > finals[0].utt_id


def test_force_cut_without_gap_uses_min_prob():
    cfg = SegmenterConfig(max_segment_s=3.0)
    seg = Segmenter(cfg)
    probs = [0.95] * secs(5.0)
    probs[secs(2.5)] = 0.6  # 最低點
    ev = run(seg, [0.0] * 3 + probs + [0.0] * secs(1))
    finals = [e for e in ev if e.kind == "final"]
    assert finals[0].forced
    assert len(finals) >= 2
    total = sum(len(f.audio) for f in finals) // FRAME
    assert total >= secs(5.0)  # 所有語音都有被送出


def test_partials_at_interval_and_cancel_on_noise():
    cfg = SegmenterConfig(partial_interval_s=0.5, partial_min_s=0.3, min_speech_s=0.2)
    seg = Segmenter(cfg)
    ev = run(seg, [0.0] * 5 + [0.9] * secs(2.0) + [0.0] * secs(1))
    kinds = [e.kind for e in ev]
    assert kinds[-1] == "final"
    assert 2 <= kinds.count("partial") <= 5
    assert all(e.utt_id == ev[-1].utt_id for e in ev)
    # partial 的音訊只會越來越長
    lens = [len(e.audio) for e in ev if e.kind == "partial"]
    assert lens == sorted(lens)


def test_flush_finishes_current_utterance():
    seg = Segmenter()
    run(seg, [0.9] * secs(1.0))
    ev = seg.flush()
    assert [e.kind for e in ev] == ["final"]
    assert seg.flush() == []


def test_voiced_ratio_low_for_mostly_silence():
    cfg = SegmenterConfig(end_silence_s=2.0, tail_pad_s=2.0)
    seg = Segmenter(cfg)
    # 斷斷續續的短語音，大部分是低機率
    probs = [0.9] * 4 + ([0.2] * 8 + [0.6] * 2) * 6 + [0.0] * secs(2.5)
    ev = run(seg, probs)
    assert ev and ev[-1].voiced_ratio < 0.5
