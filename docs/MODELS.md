# 模型來源審查紀錄

**規則**：不使用中國政府或中國資本來源的模型。

**判準**：以下任一方的公司或組織若是中國政府機構、國有企業，或中國資本控股的企業，該模型一律不採用：

- 原始權重的開發者
- 基底模型的開發者
- 轉檔或上傳者

證據不完整的模型也不採用。

`src/livesub/models.py` 的 `REGISTRY` 是唯一的允許清單（allowlist）。每個模型都固定在 40 碼的 commit hash，並列出必要檔案；下載後會逐一驗證。`FORBIDDEN_SOURCES` 名單只是第二道防線，由測試 `tests/test_models_policy.py` 強制檢查。

最後審查日期：2026-10-30。

## 允許清單

| key | Hugging Face repo（revision） | 原始開發者 | 基底模型 | 轉檔／上傳者 | 授權 |
|---|---|---|---|---|---|
| whisper-tiny / base / small | `Systran/faster-whisper-*` | OpenAI（美國） | openai/whisper-* | SYSTRAN（法國） | MIT |
| whisper-large-v3-turbo | `dropbox-dash/faster-whisper-large-v3-turbo`（原 `mobiuslabsgmbh/…`，`0a363e9`） | OpenAI | openai/whisper-large-v3-turbo | Mobius Labs（德國，現屬 Dropbox） | MIT |
| breeze-asr-25 | `phate334/Breeze-ASR-25-ct2`（`ac4ec98`） | MediaTek Research（台灣） | openai/whisper-large-v2 | phate334（GitHub 標示台灣） | Apache-2.0 |
| ov-whisper-small | `OpenVINO/whisper-small-int8-ov`（`5b83171`） | OpenAI | openai/whisper-small | Intel OpenVINO（美國） | Apache-2.0 |
| ov-whisper-large-v3-turbo | `OpenVINO/whisper-large-v3-turbo-int8-ov`（`b568445`） | OpenAI | openai/whisper-large-v3-turbo | Intel OpenVINO（美國） | Apache-2.0 |
| mt-zh-en / mt-en-zh | `quickmt/quickmt-zh-en`（`c27cc80`）、`quickmt/quickmt-en-zh`（`f48b69e`） | quickmt（Mark Franey，加拿大） | 自行訓練 | quickmt | CC-BY-4.0 |
| VAD | faster-whisper 套件內附 `silero_vad_v6.onnx` | Silero | — | — | MIT |

### 備註

- **Breeze-ASR-25 的 CT2 轉檔**：上傳者是個人帳號。若要完全不依賴第三方轉檔，可以從 MediaTek 官方 repo 自行轉換：
  ```
  ct2-transformers-converter --model MediaTek-Research/Breeze-ASR-25 --output_dir breeze25-ct2 \
      --copy_files tokenizer.json preprocessor_config.json --quantization float16
  ```
  轉換需要 torch 與 transformers。
- **quickmt 的 CC-BY-4.0** 要求標示出處，已列在 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

## 選用：Ollama LLM 翻譯

Ollama 的模型由使用者自行 `ollama pull`，tag 無法鎖定 digest。程式只接受下列前綴：

`gemma3`, `gemma3n`, `gemma4`（Google）、`llama3.1`, `llama3.2`（Meta）、`phi4`, `phi4-mini`（Microsoft）、`mistral`, `ministral`（Mistral AI）、`ycchen/breeze-7b-instruct`, `jcai/breeze-7b-instruct`（MediaTek Breeze-7B，基於 Mistral-7B）

## 已評估並排除

| 模型 | 排除原因 |
|---|---|
| Qwen 系列、Qwen3-ASR、TEA-ASR（Qwen 衍生） | Alibaba |
| FunASR、SenseVoice、Paraformer | Alibaba DAMO / FunAudioLLM |
| DeepSeek、GLM / ChatGLM、Yi、InternLM / InternVL、Baichuan、MiniCPM | 中國資本或中國政府相關機構 |
| Llama-Breeze2 | 使用 InternVL 架構與模板（上海人工智慧實驗室），有疑慮所以不採用 |
| NLLB-200 | 不是來源問題：授權為 CC-BY-NC（非商用），所以不採用 |
| OPUS-MT zh↔en | 不是來源問題：實測品質與速度都不如 quickmt，所以不採用 |
