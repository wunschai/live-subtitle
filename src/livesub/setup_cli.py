"""livesub-setup：偵測硬體 → 選等級 → 下載模型 → 實際載入與校正 → 寫入設定 → 建立捷徑。

    livesub-setup [--profile NAME] [--skip-bench] [--no-shortcut]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from . import config, hardware, models, paths, profiles
from .profiles import Profile

# 5 秒音訊的解碼時間上限（秒）：AC-LAT 目標扣掉句尾靜音 0.6 s 與翻譯餘裕
DECODE_BUDGET = {
    "nvidia-high": 0.8, "nvidia-mid": 0.8, "nvidia-low": 0.8,
    "intel-gpu": 1.9, "intel-gpu-lite": 1.4,
    "cpu-high": 3.4, "cpu-low": 3.4,
}
SAFETY_MODEL = "whisper-base"  # CPU 保底：一律下載，離線時也能退回
BENCH_TEXT = "大家好，歡迎參加今天的工作坊。我們今天要介紹圖書館的數位資源，以及如何查詢期刊論文。"


def say(msg: str) -> None:
    print(msg, flush=True)


def bench_audio() -> tuple[np.ndarray, str | None]:
    """校正用音訊：Windows 用內建 TTS 合成一段中文；其他平台用合成訊號（只量時間）。"""
    from .core.audio import read_wav

    out = paths.cache_dir() / "bench_zh.wav"
    if not out.exists() and sys.platform == "win32":
        out.parent.mkdir(parents=True, exist_ok=True)
        ps = (
            "Add-Type -AssemblyName System.Speech;"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$v=$s.GetInstalledVoices()|?{$_.VoiceInfo.Culture.Name -like 'zh-*'}|select -First 1;"
            "if($v){$s.SelectVoice($v.VoiceInfo.Name)};"
            "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
            "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono);"
            f"$s.SetOutputToWaveFile('{out}',$f);$s.Speak('{BENCH_TEXT}');$s.Dispose()"
        )
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=60, capture_output=True,
                           creationflags=0x08000000)
        except (OSError, subprocess.SubprocessError):
            pass
    if out.exists() and out.stat().st_size > 10000:
        a = read_wav(out)
        return a[: 16000 * 5], BENCH_TEXT
    rng = np.random.default_rng(0)
    t = np.arange(16000 * 5) / 16000
    a = (0.2 * np.sin(2 * np.pi * 180 * t) * (1 + np.sin(2 * np.pi * 3 * t)) + 0.02 * rng.standard_normal(t.size))
    return a.astype(np.float32), None


def ensure_models(p: Profile) -> None:
    for key in dict.fromkeys([p.asr_model, SAFETY_MODEL, "mt-zh-en", "mt-en-zh"]):
        if not models.is_downloaded(key):
            models.download(key, progress=say)


def probe(p: Profile, audio: np.ndarray, expected: str | None) -> dict:
    """實際載入並推論。回傳結果字典（ok / 秒數 / 文字 / 錯誤）。"""
    from .core.asr import build_asr

    t0 = time.perf_counter()
    try:
        eng = build_asr(p)
        load_s = time.perf_counter() - t0
        eng.transcribe(audio[:16000], language="zh", final=False)  # 暖機（OpenVINO 會編譯並快取）
        runs = [eng.transcribe(audio, language="zh", final=True) for _ in range(2)]
    except Exception as e:  # 驅動、DLL、記憶體不足…
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    decode = min(r.seconds for r in runs)
    res = {"ok": True, "load_s": round(load_s, 2), "decode_5s": round(decode, 2), "text": runs[-1].text}
    if expected:
        from difflib import SequenceMatcher

        from .core.textproc import to_traditional

        res["similarity"] = round(SequenceMatcher(None, to_traditional(runs[-1].text), expected).ratio(), 2)
    return res


def probe_translation(p: Profile) -> dict:
    from .core.translate import CT2Translator

    try:
        tr = CT2Translator(models.resolve("mt-zh-en"), models.resolve("mt-en-zh"), p.mt_device,
                           p.mt_compute_type)
        t = time.perf_counter()
        en = tr.translate("大家好，歡迎參加今天的工作坊。", "zh", "en")
        zh = tr.translate("Welcome to today's workshop.", "en", "zh")
        return {"ok": True, "seconds": round((time.perf_counter() - t) / 2, 3), "zh_en": en, "en_zh": zh}
    except Exception as e:
        if p.mt_device == "cuda":
            return probe_translation(replace(p, mt_device="cpu", mt_compute_type="int8"))
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def choose(hw: hardware.HardwareInfo, forced: str | None, skip_bench: bool) -> tuple[Profile, list[dict]]:
    if forced:
        p = profiles.with_threads(profiles.get(forced), hw)
    else:
        ov = profiles.probe_openvino_gpu() if hw.intel_gpu else None
        p = profiles.select_profile(hw, ov)
    say(f"初步等級：{p.name}（{p.label}）")
    audio, expected = bench_audio()
    log = []
    while p is not None:
        ensure_models(p)
        say(f"測試 {p.name}：載入 {p.asr_model} …")
        r = probe(p, audio, expected)
        r["profile"] = p.name
        log.append(r)
        if not r["ok"]:
            say(f"  失敗：{r['error']}")
        else:
            say(f"  5 秒音訊解碼 {r['decode_5s']} 秒（上限 {DECODE_BUDGET[p.name]}），輸出：{r['text'][:40]}")
            if skip_bench or forced or r["decode_5s"] <= DECODE_BUDGET[p.name]:
                return p, log
            say("  太慢，降一級")
        nxt = profiles.downgrade(p)
        p = profiles.with_threads(nxt, hw) if nxt else None
    raise SystemExit("所有等級都無法使用，請查看上方錯誤訊息")


def create_shortcuts(project: Path) -> list[str]:
    if sys.platform != "win32":
        return []
    pyw = project / ".venv" / "Scripts" / "pythonw.exe"
    if not pyw.exists():
        return []
    made = []
    ps_tmpl = (
        "$w=New-Object -ComObject WScript.Shell;$l=$w.CreateShortcut('{lnk}');"
        "$l.TargetPath='{target}';$l.Arguments='-m livesub';$l.WorkingDirectory='{cwd}';"
        "$l.Description='即時字幕';$l.Save()"
    )
    desktop = Path(os.environ.get("USERPROFILE", "~")) / "Desktop"
    start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    for folder in (desktop, start_menu):
        if not folder.is_dir():
            continue
        lnk = folder / "即時字幕.lnk"
        cmd = ps_tmpl.format(lnk=str(lnk).replace("'", "''"), target=str(pyw).replace("'", "''"),
                             cwd=str(project).replace("'", "''"))
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd], timeout=30, capture_output=True,
                           creationflags=0x08000000)
            if lnk.exists():
                made.append(str(lnk))
        except (OSError, subprocess.SubprocessError):
            pass
    return made


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    ap = argparse.ArgumentParser(prog="livesub-setup", description="偵測硬體、下載模型並寫入設定")
    ap.add_argument("--profile", choices=sorted(profiles.PROFILES), help="跳過自動選擇，直接用這個等級")
    ap.add_argument("--skip-bench", action="store_true", help="不因速度降級（只要能載入就用）")
    ap.add_argument("--no-shortcut", action="store_true")
    args = ap.parse_args(argv)

    if models.check_policy():
        raise SystemExit("模型清單不合規：" + "; ".join(models.check_policy()))
    hw = hardware.detect()
    gpus = ", ".join(f"{g.name}{f' {g.vram_mb}MB' if g.vram_mb else ''}" for g in hw.gpus) or "無"
    say(f"CPU：{hw.cpu_name}（{hw.physical_cores} 核）  RAM：{hw.ram_gb} GB  GPU：{gpus}")

    p, log = choose(hw, args.profile, args.skip_bench)
    mt = probe_translation(p)
    say(f"翻譯測試：{mt}")

    s = config.load()
    s.runtime.profile = p.name
    config.save(s)
    info = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "hardware": hw.to_dict(), "profile": p.name,
            "probes": log, "translation": mt, "forced": args.profile}
    (paths.config_dir() / "setup.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), "utf-8")

    if not args.no_shortcut:
        for lnk in create_shortcuts(Path(__file__).resolve().parents[2]):
            say(f"已建立捷徑：{lnk}")
    say(f"完成！使用等級：{p.label}。設定檔：{paths.config_file()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
