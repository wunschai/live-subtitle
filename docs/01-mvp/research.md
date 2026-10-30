# 01-mvp 前期研究

日期：2026-10-30

## 1. 參考專案重點

| 專案 | 授權 | 借鏡的做法 |
|---|---|---|
| [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) | Apache-2.0 | Silero VAD 以 512 樣本（32 ms）為一幀；VAD 事件要回傳**有序事件列表**，不要合併成一個 dict（#363：同一個 chunk 內 end+start 會吃掉 end）；翻譯只吃 committed 文字（`translate_on_complete`），因為一直變動的譯文難以閱讀（#264） |
| [whisper_streaming](https://github.com/ufal/whisper_streaming) | MIT | LocalAgreement-2：連續兩次假設的共同前綴才 commit；只用已 commit 的文字當 prompt，上限約 200 字；長時間執行和長暫停後會變慢（#102/#120） |
| [LiveCaptions-Translator](https://github.com/SakiRinn/LiveCaptions-Translator) | Apache-2.0 | 句尾標點立即送翻譯，否則等文字靜止約 1.2 秒；舊的翻譯請求要取消，不可亂序顯示；字幕視窗固定大小、不自動縮放（#270）；背景透明度要能調到 0（#86）；已顯示的文字不要重排（#278） |
| [Speech-Translate](https://github.com/Dadangdut33/Speech-Translate) | MIT | 幻覺過濾清單（謝謝觀看、字幕由Amara.org社區提供、Thank you. 等），用相似度 ≥0.75 比對整段；`no_speech_threshold` 提高到 0.72；click-through 後要有全域熱鍵叫回控制 |
| [obs-localvocal](https://github.com/locaal-ai/obs-localvocal) | **GPL-2.0（只參考，不抄程式碼）** | 說話中每約 1.1 秒 peek 一次 partial；VAD 結束或累積 7 秒強制 final；只翻譯完整句子，並帶上一句當上下文；字幕靜音後約 3 秒清除 |
| [RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) | MIT | 句尾靜音 0.6 秒才結束；語音開始前保留 1 秒 pre-roll，避免吃掉第一個音節；小模型做即時預覽，大模型在句尾重新辨識整句 |

## 2. 模型（全部排除中國政府／中國資本來源）

已排除：Qwen/Alibaba、FunASR/SenseVoice、DeepSeek、GLM/Zhipu、Yi、InternLM、Baichuan、MiniCPM、TEA-ASR（Qwen 衍生）、Llama-Breeze2（InternVL 架構與模板，有疑慮所以不用）。

| 用途 | 模型 | 來源 | 授權 | 備註 |
|---|---|---|---|---|
| ASR | Systran/faster-whisper-{tiny,base,small,medium} | OpenAI Whisper 的 CT2 轉檔 | MIT | |
| ASR | mobiuslabsgmbh/faster-whisper-large-v3-turbo | 同上，這個 repo 現已轉到 Dropbox org | MIT | faster-whisper 內建的 `turbo` alias 指向它 |
| ASR | phate334/Breeze-ASR-25-ct2（fp16）、-int8-CT2 | MediaTek Breeze-ASR-25 的轉檔（上傳者在台灣） | Apache-2.0 | 針對繁中與中英夾雜強化，基底是 large-v2 |
| ASR（Intel 內顯） | OpenVINO/whisper-{small,large-v3-turbo}-int8-ov | Intel 官方 OpenVINO org | MIT/Apache | 給 openvino-genai 的 WhisperPipeline 使用 |
| 翻譯 | quickmt/quickmt-zh-en、quickmt/quickmt-en-zh | Mark Franey（加拿大），原生 CT2 | **CC-BY-4.0（必須標示出處）** | 實測品質與速度都勝過 OPUS-MT |
| 翻譯（選用） | Ollama：gemma3:4b/12b、phi4-mini、ycchen/breeze-7b-instruct-v1_0 | Google / Microsoft / MediaTek | 各自條款 | 可選的 LLM 翻譯 |
| VAD | Silero VAD v6（faster-whisper 內附 onnx） | Silero | MIT | 不需要 torch |

未採用：NLLB-200 是 CC-BY-NC，非商用限制；M2M100 檔案太大、品質普通。

## 3. 本機實測（Intel Core Ultra 7 155H、Arc 內顯、32 GB RAM、無 NVIDIA）

測試音檔：Windows TTS（zh-TW Hanhan、en-US Zira）。

### faster-whisper CPU int8（8 threads），5 秒音訊一次解碼

| 模型 | 耗時 | 品質 |
|---|---|---|
| tiny | 0.56 s | 會錯字（工作坊→工作房） |
| base | 1.0 s | 會錯字 |
| small | 3.0 s | 正確，但輸出簡體 |
| large-v3-turbo | 約 11–13 s | 正確，太慢 |

Whisper 的 encoder 固定處理 30 秒視窗，所以短音訊也要付出接近固定的成本。在 CPU 上「每秒重解一次」做 partial 不可行。

### OpenVINO GenAI 2026.4 / WhisperPipeline，5 秒音訊

| 模型 | Arc 內顯（GPU） | CPU |
|---|---|---|
| whisper-small-int8-ov | **0.73 s** | 2.2 s |
| whisper-large-v3-turbo-int8-ov | **1.5 s** | 11–13 s |

GPU 第一次載入約 50 秒（kernel 編譯），要設定 `CACHE_DIR` 才能讓之後的載入變快。

**結論：Intel 內顯單獨成為一個等級，走 OpenVINO。**

### 翻譯（CT2 int8 CPU）

| 方向 | 模型 | 每句耗時 | 品質 |
|---|---|---|---|
| zh→en | quickmt | 約 0.1 s | 自然 |
| zh→en | OPUS-MT | 約 0.3–0.6 s | 較差（快取→pick-up） |
| en→zh | quickmt / OPUS-MT | | 皆輸出簡體，要經 OpenCC `s2twp` 轉換 |

OPUS-MT 加 `>>cmn_Hant<<` 雖然會出繁體，但品質較差。

### 環境陷阱

- 主控台是 cp950，Python 要設定 `PYTHONIOENCODING=utf-8` 或 `reconfigure`。
- faster-whisper 1.2.1 的 `decode_audio` 和 av 19 不相容（`metadata_errors`），所以一律以 numpy 陣列餵入，不走檔案解碼。
- huggingface_hub 新版在 `local_files_only=True` 時會要求快照完整，所以模型改用 `local_dir` 下載到 app 資料夾，以本機路徑載入。
- sounddevice 會列出同一支麥克風在 MME/DirectSound/WASAPI/WDM-KS 下的重複項目，UI 要過濾到單一 host API。
