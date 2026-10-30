"""真模型整合測試（pytest -m slow）。需要先跑過 livesub-setup；模型不在就跳過。

測試音檔用 Windows 內建 TTS 現場合成（非 Windows 或沒有中文語音就跳過）。
"""

import subprocess
import sys
import time
from difflib import SequenceMatcher

import numpy as np
import pytest

from livesub import models, profiles
from livesub.config import Settings
from livesub.core.audio import FileSource, read_wav

pytestmark = pytest.mark.slow

ZH = "大家好，歡迎參加今天的工作坊。我們今天要介紹圖書館的數位資源。"
EN = "Good morning everyone. Today we will talk about open access publishing."


def _need(*keys):
    missing = [k for k in keys if not models.is_downloaded(k)]
    if missing:
        pytest.skip(f"模型未下載：{missing}")


def _tts(tmp_path, text, culture, name):
    if sys.platform != "win32":
        pytest.skip("需要 Windows TTS")
    out = tmp_path / name
    ps = (
        "Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$v=$s.GetInstalledVoices()|?{{$_.VoiceInfo.Culture.Name -like '{culture}*'}}|select -First 1;"
        "if(-not $v){exit 3};$s.SelectVoice($v.VoiceInfo.Name);"
        "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono);"
        f"$s.SetOutputToWaveFile('{out}',$f);$s.Speak('{text}');$s.Dispose()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60)
    if r.returncode != 0 or not out.exists():
        pytest.skip(f"沒有 {culture} 語音")
    return out


def _sim(a, b):
    norm = lambda s: "".join(c for c in s.lower() if c.isalnum())  # noqa: E731
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


@pytest.fixture(scope="module")
def tts(tmp_path_factory):
    d = tmp_path_factory.mktemp("tts")
    return {"zh": _tts(d, ZH, "zh", "zh.wav"), "en": _tts(d, EN, "en", "en.wav")}


def test_faster_whisper_cpu_base(tts):
    _need("whisper-base")
    from livesub.core.asr import build_asr
    from livesub.core.textproc import clean

    eng = build_asr(profiles.get("cpu-low"))
    r = eng.transcribe(read_wav(tts["en"]), language=None)
    assert r.language == "en" and _sim(r.text, EN) > 0.8
    r = eng.transcribe(read_wav(tts["zh"]), language="zh")
    assert _sim(clean(r.text), ZH) > 0.7
    assert r.avg_logprob is not None and r.no_speech_prob is not None


def test_openvino_gpu(tts):
    _need("ov-whisper-large-v3-turbo")
    if not profiles.probe_openvino_gpu():
        pytest.skip("OpenVINO 看不到 GPU")
    from livesub.core.asr import build_asr
    from livesub.core.textproc import clean

    eng = build_asr(profiles.get("intel-gpu"))
    r = eng.transcribe(read_wav(tts["zh"]), language=None)
    assert r.language == "zh" and _sim(clean(r.text), ZH) > 0.8
    assert r.avg_logprob is None  # 能力表：OpenVINO 不提供信心值
    r = eng.transcribe(read_wav(tts["en"]), language=None)
    assert r.language == "en" and _sim(r.text, EN) > 0.8


def test_quickmt_both_directions():
    _need("mt-zh-en", "mt-en-zh")
    from livesub.core.translate import CT2Translator

    tr = CT2Translator(models.resolve("mt-zh-en"), models.resolve("mt-en-zh"))
    en = tr.translate("大家好，歡迎參加今天的工作坊。", "zh", "en")
    assert "workshop" in en.lower()
    zh = tr.translate("Welcome to the library workshop.", "en", "zh")
    assert "圖書館" in zh and "图" not in zh  # 台灣正體


def test_full_pipeline_with_real_models(tts, tmp_path):
    _need("whisper-base", "mt-zh-en", "mt-en-zh")
    from livesub.core.pipeline import Pipeline
    from livesub.core.status import PipelineState

    # 中文 + 停頓 + 英文串成一個檔
    import wave

    a = np.concatenate([np.zeros(8000, np.float32), read_wav(tts["zh"]), np.zeros(16000, np.float32),
                        read_wav(tts["en"])])
    mix = tmp_path / "mix.wav"
    with wave.open(str(mix), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes((a * 32767).astype("<i2").tobytes())

    snaps = []
    s = Settings()
    s.display.mode = "bilingual"
    s.display.clear_after_s = 0
    p = Pipeline(s, profiles.get("cpu-low"), snaps.append, lambda st: None,
                 source_factory=lambda _s: FileSource(mix, realtime=False))
    p.start()
    end = time.monotonic() + 120
    while time.monotonic() < end:
        last = snaps[-1] if snaps else None
        # 等到全部都是確認且已翻譯的字幕（沒有淡色的 partial／待翻譯）
        if last and last.blocks and "publishing" in last.blocks[1].text \
                and not any(sp.dim and sp.text.strip() for b in last.blocks for sp in b.spans):
            break
        time.sleep(0.2)
    p.stop(wait=True)
    assert p.state == PipelineState.IDLE
    zh_block = next(sn for sn in reversed(snaps) if sn.blocks and not sn.empty).blocks[0].text
    en_block = next(sn for sn in reversed(snaps) if sn.blocks and not sn.empty).blocks[1].text
    print(zh_block, "||", en_block)
    assert _sim(zh_block, ZH) > 0.6 and "早" in zh_block  # 英文句被譯成中文（base 模型會有錯字，只比相似度）
    assert "Good morning" in en_block and "publishing" in en_block and "welcome" in en_block.lower()
    assert "０" not in en_block
