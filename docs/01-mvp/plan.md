# 01-mvp 設計規劃

## 目標

講者在台上用中文或英文（可中英夾雜）說話，電腦桌面最上方或最下方即時出現字幕，可選擇顯示原文、只顯示中文、只顯示英文，或中英雙語同時顯示。

這個程式要能裝在各種電腦上。安裝時自動判斷設備等級，裝上對應的推論後端和模型。

### MVP 範圍

1. 語音轉字幕
2. 中英互譯，以及雙語同時顯示
3. 可隱藏的小型控制面板
4. 字幕列在畫面上方或下方；樣式可選「直接顯示（文字描邊）」或「半透明底框」；字體大小可調

### 不做（之後再說）

- 系統音訊 loopback
- 錄影或輸出 .srt
- 多講者辨識
- 術語表 UI
- macOS/Linux 安裝程式的完整驗證（程式碼保持跨平台，安裝腳本先以 Windows 為主）

### 硬性限制

- 不使用任何中國政府或中國資本的模型。由 `models.py` 的 registry 搭配 policy 測試強制把關。
- 預設完全離線執行，模型在安裝時下載完畢。

## 架構

```
mic ─▶ AudioSource ─▶ VAD(Silero onnx, 32ms) ─▶ Segmenter ─┬─ PartialRequest ─▶ ASR worker ─┐
                                                            └─ FinalRequest   ─▶ ASR worker ─┤
                                                                                              ▼
                                       TextProc(OpenCC s2twp / 幻覺過濾 / 重複截斷 / 語言判斷)
                                                                                              ▼
                                       CaptionBoard ◀── Translator worker（只翻 final）◀─────┘
                                            │ (Qt signal, queued)
                                            ▼
                                  OverlayWindow（字幕列） + ControlPanel + Tray
```

- **核心層（`livesub/core`）** 完全不依賴 Qt。對外以 callback 輸出 `CaptionEvent`，方便單元測試。
- **UI 層（`livesub/ui`）** 使用 PySide6。用 `QtBridge(QObject)` 把 callback 轉成 queued signal，跨執行緒時安全。

### 執行緒

| 執行緒 | 工作 |
|---|---|
| audio callback | 由 sounddevice 呼叫，只負責把資料放進 queue |
| pipeline | VAD 加上 segmenter |
| ASR worker | 單一執行緒；final 永遠優先於 partial，佇列裡過時的 partial 直接丟棄 |
| translate worker | 單一執行緒，按順序處理 |
| Qt 主執行緒 | 畫面 |

### 斷句策略（Segmenter，數值都可以在 profile 調整）

- **語音開始**：VAD 機率 ≥ 0.5，且連續 ≥ 3 幀（約 96 ms）。保留 400 ms pre-roll。
- **句尾**：靜音 ≥ 600 ms 時送出 FinalRequest（整段音訊）。
- **強制切段**：一段語音累積到 `max_segment_s`（預設 8 s）時，在最後 1.5 s 內 VAD 機率最低的位置切開送 final，剩下的音訊延續到下一段。
- **太短不送**：語音不到 0.35 s 直接丟棄，避免咳嗽或雜音觸發幻覺。
- **partial**：說話中每隔 `partial_interval_s` 送一次目前累積的音訊，這個值依等級設定，0 代表關閉。ASR worker 忙碌時，partial 自動被覆蓋，不會堆積。

### ASR

- 介面：`ASREngine.transcribe(audio: np.ndarray, language: str|None, prompt: str|None) -> AsrResult(text, language, avg_logprob, no_speech_prob)`。
- **`FasterWhisperEngine`**：`beam_size` 依等級設定（final 用 1–5，partial 固定用 1），並設 `condition_on_previous_text=False`、`no_speech_threshold=0.7`、`compression_ratio_threshold=2.4`、`log_prob_threshold=-1.0`。
- **`OpenVINOWhisperEngine`**：使用 openvino_genai 的 `WhisperPipeline(path, "GPU", CACHE_DIR=...)`。
- **語言**：設定項 `source_language` 可選 auto / zh / en。auto 時 final 讓 Whisper 自動判斷；同一句話的 partial 沿用該句第一次 partial 判斷出的語言。
- **prompt**：繁中提示句（帶台灣用語），再加上最近 1–2 句已確認的文字，總長上限 150 字。

### 文字後處理（TextProc）

- 中文一律經 OpenCC `s2twp`，簡體轉台灣正體並換成台灣用語。
- **幻覺過濾**：整段文字與清單相似度 ≥ 0.75 就丟棄。清單包括：謝謝觀看、字幕由Amara.org社區提供、請不吝點贊訂閱轉發打賞、Thank you.、Thanks for watching 等。另外 `no_speech_prob` > 0.6 且 `avg_logprob` < -1 的結果也丟棄。
- **重複截斷**：同一個字連續 ≥ 4 次，或 n-gram 連續重複 ≥ 3 次，只保留一次。
- **語言判斷**：CJK 字元佔比 ≥ 30% 視為 zh，否則視為 en。用來決定翻譯方向。

### 翻譯

- 介面：`Translator.translate(text, src, tgt, context: list[str]) -> str`。
- **`CT2Translator`**（預設，所有等級都用）：quickmt zh-en / en-zh，int8 或 float16。en→zh 的輸出再經 `s2twp`。
- **`OllamaTranslator`**（選用）：呼叫 `http://localhost:11434/api/chat`，模型在 allowlist 內。prompt 裡帶上一句作為上下文。
- **只翻 final**。翻譯在背景執行，完成後回填到對應的字幕行（以 `line_id` 對應），所以不會亂序。

### 字幕板（CaptionBoard，純邏輯）

- 每一行字幕：`CaptionLine(id, original, lang, zh, en, is_final, ts)`。
- **顯示模式**：

  | 模式 | 顯示內容 |
  |---|---|
  | `original` | 原文 |
  | `zh` | 中文：原文是中文就顯示原文，否則顯示譯文 |
  | `en` | 英文：同上 |
  | `bilingual` | 中文行加英文行 |

- 未完成翻譯時，譯文位置顯示「…」。在 `zh` 或 `en` 模式下，若譯文還沒出來，先顯示原文，等譯文到了再替換。
- **保留行數**：只保留最近 `max_lines`（1–3）句。partial 用淡色顯示在最後一行。
- **自動清除**：最後一句 final 之後 `clear_after_s`（預設 6 s）沒有新內容，字幕淡出。

### UI

- **OverlayWindow**
  - 無邊框、置頂、`Tool` 視窗（不出現在工作列）、背景透明、點擊穿透（`WindowTransparentForInput`）。
  - 寬度為螢幕寬度 × `width_pct`（預設 80%），水平置中，可選上方或下方，與邊緣的距離為 `margin_px`。
  - 高度依字體大小與行數**固定計算**，不隨文字自動伸縮。
  - 文字樣式有兩種：
    - `outline`：直接顯示。白字加黑色描邊與陰影，無背景。
    - `box`：半透明黑底，透明度 0–100%，可調。
  - 字體大小 16–96 px。雙語時譯文行可以設成主行的 0.8 倍（設定 `secondary_scale`）。
  - 可選擇顯示在哪一個螢幕。
- **ControlPanel**（小視窗，約 360×520）
  - 開始／停止
  - 麥克風選擇（只列出單一 host API 的裝置，附音量表）
  - 來源語言：自動 / 中文 / 英文
  - 顯示模式：原文 / 中文 / 英文 / 雙語
  - 位置：上 / 下
  - 樣式：直接顯示 / 底框，以及底框透明度
  - 字體大小
  - 行數
  - 螢幕
  - 「隱藏面板」按鈕
  - 狀態列：等級名稱、模型名稱、延遲
- **系統匣（Tray）**：顯示或隱藏面板、開始或停止、結束程式。
- **全域熱鍵**：Windows 用 `RegisterHotKey`。`Ctrl+Alt+H` 切換面板，`Ctrl+Alt+S` 開始或停止。其他平台只能透過系統匣操作。
- **設定保存**：存在 `%APPDATA%/live-subtitle/config.toml`（以 `platformdirs` 決定路徑）。任何改動立即寫入。

## 硬體等級與自動安裝

### 偵測（`hardware.py`，只用標準函式庫）

- NVIDIA：`nvidia-smi --query-gpu=name,memory.total`
- 其他顯示卡：Windows 用 WMI（PowerShell `Get-CimInstance Win32_VideoController`）；Linux 用 `lspci`；macOS 用 `system_profiler`
- CPU 實體核心數、RAM、作業系統、架構（arm64/x64）

### 等級表（`profiles.py`，純函式 `select_profile(hw) -> Profile`）

| 等級 | 條件 | ASR | final beam | partial | 翻譯 |
|---|---|---|---|---|---|
| `nvidia-high` | NVIDIA VRAM ≥ 8 GB | Breeze-ASR-25 CT2 fp16（cuda float16） | 5 | 0.6 s | quickmt cuda |
| `nvidia-mid` | NVIDIA 4–8 GB | large-v3-turbo（cuda int8_float16） | 3 | 0.8 s | quickmt cuda |
| `nvidia-low` | NVIDIA < 4 GB | small（cuda int8_float16） | 3 | 0.8 s | quickmt cpu |
| `intel-gpu` | Intel Arc/Iris Xe 內顯，且 OpenVINO 看得到 GPU；RAM ≥ 16 GB | OpenVINO large-v3-turbo-int8（GPU） | – | 1.5 s | quickmt cpu |
| `intel-gpu-lite` | 同上，但 RAM < 16 GB | OpenVINO small-int8（GPU） | – | 1.0 s | quickmt cpu |
| `cpu-high` | 沒有可用 GPU，實體核心 ≥ 6 且 RAM ≥ 8 GB | small int8 | 1 | 關 | quickmt cpu |
| `cpu-low` | 其他 | base int8 | 1 | 2.0 s | quickmt cpu |

- **自我校正**：安裝程式用內建 TTS 測試音（或隨附的 `assets/bench_zh.wav`）跑一次解碼。若 5 秒音訊的解碼時間超過 2.5 s，自動降一級，並記錄到 config。
- **手動指定**：可用 `--profile` 強制指定等級，也可以之後在 config 裡改。

### 安裝流程

- **Windows**：`install.ps1`
  1. 若沒有 uv，自動安裝。
  2. `uv run --no-project python src/livesub/hardware.py --print-extra` 取得要裝的 extra（`cuda`、`intel` 或 `none`）。
  3. `uv sync --extra <x>` 安裝相依套件。
  4. `uv run livesub-setup`：重新偵測、選擇等級、下載模型到 `%LOCALAPPDATA%/live-subtitle/models`、跑校正、寫入 config。
  5. 建立桌面捷徑與開始選單捷徑。
- **macOS/Linux**：`install.sh`，流程相同，先以 CPU 等級為主。
- **pyproject extras**
  - `cuda`：nvidia-cublas-cu12、nvidia-cudnn-cu12 9.x。程式啟動時用 `os.add_dll_directory` 把 DLL 路徑加進來。
  - `intel`：openvino-genai。
- **NVIDIA 失敗回退**：等級是 NVIDIA 但 CUDA 初始化失敗時，執行期自動退到 `cpu-high`，面板上顯示警告。

## 模組配置

```
src/livesub/
  __main__.py        python -m livesub [--source file.wav] [--profile X] [--config path]
  paths.py           app 資料夾路徑
  config.py          Settings（UI 設定）+ RuntimeConfig（profile、模型路徑）；TOML 讀寫
  hardware.py        偵測（stdlib only，可直接執行）
  profiles.py        等級表 + select_profile
  models.py          模型 registry（repo、授權、來源）+ 下載
  setup_cli.py       livesub-setup：下載 + 校正 + 寫 config + 捷徑
  core/
    audio.py         MicSource（sounddevice）、FileSource（wav，可依即時速度播放）、裝置清單、重採樣
    vad.py           SileroVAD（onnxruntime，使用 faster_whisper/assets/silero_vad_v6.onnx）
    segmenter.py     狀態機
    asr.py           ASREngine、FasterWhisperEngine、OpenVINOWhisperEngine、build_asr(profile)
    textproc.py      s2twp、幻覺過濾、重複截斷、語言判斷
    translate.py     Translator、CT2Translator、OllamaTranslator、build_translator
    captions.py      CaptionBoard（顯示邏輯）
    pipeline.py      Pipeline：組合以上元件，管理 worker 執行緒，輸出 CaptionEvent
  ui/
    app.py           QApplication、bridge、組裝
    overlay.py       OverlayWindow
    panel.py         ControlPanel
    tray.py          TrayIcon
    hotkey.py        Windows 全域熱鍵
    theme.py         面板樣式
```

## 測試策略

- **單元測試（pytest）**：profiles 等級表、hardware 解析（假的 nvidia-smi/WMI 輸出）、config 存取來回、textproc、segmenter（合成的 VAD 機率序列）、CaptionBoard 的各種模式、pipeline（假的 ASR/翻譯 + 檔案來源）、模型 policy（禁用名單）。
- **整合測試（`-m slow`）**：用真模型跑 TTS 測試音，包括 faster-whisper base、quickmt，以及（若可用）OpenVINO。
- **UI 測試（pytest-qt，offscreen）**：面板操作會正確改變設定和 overlay 的幾何、樣式。
- **monkey 測試**：固定 10 個種子，隨機操作所有控制項（連點、亂改、開始停止交錯），全程不能出現例外，overlay 幾何不能變成 NaN 或負值。
- **實機 E2E**：用 `--source zh.wav` 啟動真視窗，截圖確認字幕確實出現在上方或下方，且雙語正確。

## 分工（Claude ↔ GPT/codex）

| 階段 | Claude | codex |
|---|---|---|
| 規劃 | research、plan、spec、tasks | 獨立審查 plan 與 spec |
| 實作 | 核心層、安裝程式、整合 | UI 層（overlay、panel、tray、theme），依 `ui_contract.md`，以唯讀沙箱輸出檔案交接 |
| 審查 | 兩個 Claude reviewer subagent | 一份獨立 codex review |
| 驗證 | 測試、monkey、實機截圖 | 審查截圖 |

---

## 審查後決策（回應 `docs/reviews/01-plan-review-codex.md`）

以下修訂優先於上文。

| # | GPT 意見 | 決策 |
|---|---|---|
| 1 | 缺少延遲目標，校正方式不具代表性 | **採用**。spec 定義各等級的 p50 延遲目標（見 spec AC-LAT）。校正改成用「中英 TTS + 停頓 + 連講」的混合音檔，跑完整 pipeline（VAD → ASR → 翻譯），量測每句 final 的延遲，未達標就沿 fallback 鏈降級。CPU 等級在 UI 上明確標示為「分段字幕」模式（沒有 partial 或 partial 稀疏）。 |
| 2 | final 無法搶占執行中的 partial；舊 partial 可能晚到 | **採用**。每個請求帶 `session` 和 `utt_id`。coordinator 收到 final 之後，拒收同一 utt 或更早的 partial。partial 只有一個待處理槽。partial 間隔改成**自適應**：`max(設定值, 1.3 × 最近 partial 平均耗時)`。final 佇列上限 4，超出時合併成一段。 |
| 3 | partial 穩定策略未定義 | **MVP 採最小契約**：已 final 的行內容與位置固定，partial 只替換「當前行」，以淡色顯示。LocalAgreement 列為後續項目。 |
| 4 | 強制切段可能切在音節中間 | **採用**。優先在最後 2 秒內找「連續 ≥ 3 幀低於 end_threshold」的低語音區間，切在區間中央；找不到才在機率最低的幀硬切。下一段的 prompt 帶上一段的文字，降低斷句造成的語意斷裂。重疊去重需要 word timestamps，列為後續項目。 |
| 5 | 丟棄 < 0.35 s 會漏掉短句 | **採用**。`min_speech_s` 降到 0.2 s。短句不再單看時長，改由 ASR 信心加上弱幻覺清單判斷。 |
| 6 | 整句二分語言，處理不了中英夾雜 | **部分採用**。文字組成分成 `zh`、`en`、`mixed`；`mixed` 定義為 CJK 佔比 30–70% 且英文詞 ≥ 2。台灣的「中文夾英文術語」本來就是正常中文，所以 `zh` 模式顯示 `zh`/`mixed` 原文，翻譯方向一律依 CJK 佔比 ≥ 30% 判定為 zh→en。partial 的語言**不鎖定**，每次 final 都重新判斷。 |
| 7 | prompt 與 OpenCC 會改變原文 | **採用**。繁中靜態提示只在 `source_language=zh`，或上一句 final 是中文時才加。上下文在停頓 > 10 s、停止或重新開始時清除，長度上限 120 字。CaptionLine 同時保存 `raw` 與顯示用文字。台灣用語轉換做成設定項 `taiwan_phrases`（s2twp / s2tw）。 |
| 8 | 幻覺過濾會刪掉正常發言 | **採用**。分成強清單（字幕組、訂閱這類，必丟）與弱清單（謝謝、Thank you 等）。弱清單只在「信心低」時丟，信心低的定義是 `no_speech_prob > 0.5`，或 `avg_logprob < -0.8`，或 VAD 語音覆蓋率 < 40%。重複截斷只處理 ≥ 4 次的迴圈。被丟掉的結果寫入 debug log 並註明原因。 |
| 9 | 兩個 ASR 後端能力不同 | **採用**。`AsrResult` 的信心欄位改為 Optional，OpenVINO 回傳 None，處理層看到 None 就跳過相關規則。已實測 OpenVINO 支援 `initial_prompt`、自動語言（`r.language`）、numpy 輸入、`CACHE_DIR`。 |
| 10 | 翻譯佇列與字幕生命週期 | **採用**。翻譯請求帶 `session` 和 `line_id`，回填前先驗證行仍存在。佇列上限 6，超出時丟最舊的並把該行標為「略過」。Ollama 設 8 s timeout，失敗就退回 CT2。`zh`/`en` 模式下，等待譯文時先以淡色顯示原文，譯文到了再替換。 |
| 11 | CaptionBoard 的擁有者與停止流程 | **採用**。由單一 coordinator 執行緒擁有 CaptionBoard；ASR 和翻譯 worker 只送事件進 coordinator 佇列；UI 只收不可變的 `BoardSnapshot`。狀態機為 `idle → loading → running → stopping → idle`，以及 `failed`。模型載入和 worker join 都在背景執行緒進行。 |
| 12 | 音訊 callback 與資源競爭 | **採用**。callback 內 `copy()` 後用 `put_nowait` 放進上限 200 塊的佇列，溢位時計數並在狀態列顯示。執行緒預算：VAD 1、ASR 用 profile 的 `cpu_threads`、MT 2。重採樣以 numpy 實作。 |
| 13 | Windows 多螢幕與 DPI | **採用**。一律使用 Qt 邏輯像素，字體以 px 為單位。用所選螢幕的 `geometry()`，演講時字幕要能蓋過工作列。監聽 `screenAdded`/`screenRemoved`/`geometryChanged`，螢幕消失時回到主螢幕。視窗設 `WA_ShowWithoutActivating` 和 `WindowDoesNotAcceptFocus`。實機截圖驗證。 |
| 14 | 固定高度下長字幕與雙語的排版 | **採用**。`max_lines` 改為「每個語言區塊的最大**視覺**行數」（1–3）。超出時保留最新的尾端，前面以「…」截掉。雙語固定**中文在上、英文在下**，避免講者換語言時上下對調，觀眾比較好讀。刪除 `secondary_scale`，兩行同字級。 |
| 15 | 面板太大、隱藏後回不來 | **採用**。精簡面板約 320×260：開始/停止、狀態、顯示模式、位置、字級、隱藏。其他選項收在「進階」展開區。熱鍵或系統匣不可用時停用「隱藏」，只能最小化。清除計時從最近一次活動（partial、final 或譯文）起算。設定以 500 ms debounce 加原子替換寫入。 |
| 16 | 硬體名稱不代表後端可用 | **採用**。偵測分兩階段：安裝前用標準工具粗篩，裝完後 `livesub-setup` 實際載入模型並跑一次短推論。系統指令都設 timeout，探測失敗的原因寫進 config 的 `[setup]` 區段。 |
| 17 | 降級與離線回退沒有完整路徑 | **採用**。fallback 圖改為：`nvidia-high → nvidia-mid → nvidia-low → cpu-high → cpu-low`、`intel-gpu → intel-gpu-lite → cpu-high → cpu-low`。setup **一律額外下載 CPU 保底模型**（`whisper-base`）。執行期初始化失敗時，沿鏈退到第一個「模型已下載」的等級。config 分開記錄 `profile`（setup 選定）和 `profile_override`（手動指定）。 |
| 18 | Windows 安裝與 DLL | **採用**。`none` 時執行不帶 extra 的 `uv sync`。固定 Python 3.12 並 commit `uv.lock`。`add_dll_directory` 的 handle 保存到程式結束。捷徑直接執行 `.venv\Scripts\pythonw.exe -m livesub`，啟動時不觸發 sync。WAV 用標準函式庫 `wave` 讀取並重採樣。專案路徑本身就含中文（`文件`），測試會一併涵蓋。 |
| 19 | 禁用名單不足以把關 | **採用**。registry 改成 allowlist：固定 40 碼 revision、必要檔案清單、原始開發者、基底、轉檔者。`docs/MODELS.md` 記錄來源審查證據。測試拒絕未固定的 revision。Ollama 是使用者自選的選用功能，只允許 allowlist 前綴，並在文件中說明無法鎖定 digest。 |
| 20 | 授權與離線完整性 | **採用**。新增 `THIRD_PARTY_NOTICES.md`，包含 quickmt CC-BY 標示、Qt/PySide6 LGPL、各模型授權。下載先放到 `.partial` 資料夾，驗證必要檔案齊全才改名。另有離線測試：設 `HF_HUB_OFFLINE=1` 啟動並完成推論。 |
| 21 | 測試缺少時間與競態 | **採用**。用可控完成順序的假 ASR/翻譯 engine 測：final 先到、舊 partial 晚到、停止後回填、重啟、佇列溢位，以及長時間跑 500 句時佇列與記憶體都有上限。沒有真人錄音，用 TTS 加噪音與中英串接替代，並列為已知限制。 |
