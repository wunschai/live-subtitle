# live-subtitle 即時字幕

演講或小型工作坊用的桌面即時字幕。講者用中文或英文（可以中英夾雜）說話，螢幕最上方或最下方會即時出現字幕，可以只顯示原文、只顯示中文、只顯示英文，或中英雙語同時顯示。

- **完全離線**：模型在安裝時下載，之後不需要網路。
- **自動配合硬體**：安裝時偵測 NVIDIA 顯示卡、Intel 內顯或純 CPU，自動裝上對應的加速套件與模型。
- **台灣正體**：辨識與翻譯結果一律轉成台灣正體與台灣用語。
- **模型來源審查**：不使用中國政府或中國資本來源的模型（見 [docs/MODELS.md](docs/MODELS.md)）。

## 安裝（Windows）

1. 下載或 clone 這個 repo。
2. 在資料夾裡對 `install.ps1` 按右鍵，選「以 PowerShell 執行」；也可以在 PowerShell 裡執行：
   ```powershell
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```
3. 安裝程式會依序完成以下步驟，第一次約需 5–15 分鐘，依網速而定：
   1. 安裝 uv（Python 與套件管理工具）
   2. 偵測硬體並安裝對應的加速套件
   3. 下載模型，實際跑一次辨識測速，太慢就自動降一級
   4. 在桌面與開始選單建立「即時字幕」捷徑

不需要系統管理員權限。可以加這些參數：

| 參數 | 用途 |
|---|---|
| `-Tier cpu-high` | 強制使用某個等級 |
| `-SkipBench` | 不因速度降級 |
| `-NoShortcut` | 不建立捷徑 |

macOS / Linux 執行 `sh install.sh`，目前以 CPU 模式為主。

## 使用

開啟「即時字幕」後會出現一個小面板：

| 控制項 | 說明 |
|---|---|
| 開始／停止 | 開始收音、出字幕 |
| 顯示模式 | 原文／中文／英文／中英雙語。雙語時中文在上、英文在下 |
| 位置 | 字幕在螢幕最上方或最下方 |
| 字級 | 16–96 px |
| 進階 | 麥克風（附音量表）、來源語言（自動／中文／英文）、樣式（直接顯示的描邊字，或半透明底框，底框透明度可調）、每區行數、字幕寬度、顯示在哪個螢幕 |
| 隱藏面板 | 面板藏起來後，從系統匣圖示叫回，或按 **Ctrl+Alt+H** |

**Ctrl+Alt+S** 可以隨時開始或停止。

所有設定會自動保存，下次開啟時還原。字幕列不會擋住滑鼠點擊，也不會搶走簡報軟體的焦點。

### 小提醒

- **收音品質比模型更重要。** 從場地混音器接線進電腦，或用領夾麥，效果遠勝筆電內建麥克風。
- **講者中英夾雜時**，來源語言保持「自動」即可；全場只講中文時，選「中文」會更穩定。
- **翻譯只處理已經確認的句子。** 句子還在說的時候，原文以淡色顯示；譯文出來後會補上。

### 測試或示範（不用麥克風）

```powershell
.\.venv\Scripts\python.exe -m livesub --source 某個音檔.wav
```

## 硬體等級

| 等級 | 條件 | 辨識模型 | 延遲目標 |
|---|---|---|---|
| nvidia-high | NVIDIA VRAM ≥ 8 GB | Breeze-ASR-25（MediaTek，繁中與中英夾雜強化） | ≤ 1.5 秒 |
| nvidia-mid | NVIDIA 4–8 GB | Whisper large-v3-turbo | ≤ 1.5 秒 |
| nvidia-low | NVIDIA < 4 GB | Whisper small | ≤ 1.5 秒 |
| intel-gpu | Intel Arc / Iris Xe 內顯，RAM ≥ 16 GB | Whisper large-v3-turbo（OpenVINO） | ≤ 2.5 秒 |
| intel-gpu-lite | Intel 內顯，RAM < 16 GB | Whisper small（OpenVINO） | ≤ 2.0 秒 |
| cpu-high | 6 核以上 | Whisper small | ≤ 4 秒（分段字幕） |
| cpu-low | 其他 | Whisper base | ≤ 4 秒（分段字幕） |

延遲是從講者停頓到字幕出現的時間，實際數值會顯示在面板上。翻譯所有等級都使用 [quickmt](https://huggingface.co/quickmt)，每句約 0.05–0.1 秒。

**實測**：在 Intel Core Ultra 7 155H 加 Arc 內顯上，自動選擇 `intel-gpu`，延遲中位數約 2.0 秒。

如果加速裝置初始化失敗（例如驅動問題），程式會自動退回下一個等級，最後一定能用 CPU 保底模型。

**進階**：選用 Ollama 的本機 LLM 翻譯（Gemma、Phi、Breeze-7B 等允許清單內的模型）時，在設定檔 `[runtime]` 加上 `translator = "ollama"` 與 `ollama_model = "gemma3:4b"`。失敗時會自動退回 quickmt。

## 開發

```powershell
uv sync --extra intel        # 或 --extra cuda，或不加
uv run pytest                # 單元、UI、monkey 測試
uv run pytest -m slow        # 真模型整合測試（需先跑過 livesub-setup）
```

| 文件 | 內容 |
|---|---|
| [docs/01-mvp/research.md](docs/01-mvp/research.md) | 參考專案與本機實測 |
| [docs/01-mvp/plan.md](docs/01-mvp/plan.md) | 架構與審查後決策 |
| [docs/01-mvp/spec.md](docs/01-mvp/spec.md) | 驗收條件與介面契約 |
| [docs/reviews/](docs/reviews/) | 規劃與程式碼審查紀錄 |

本專案由 Claude（核心、整合）與 GPT/codex（UI 層、獨立審查）協作開發。

## 授權

程式碼採 MIT 授權。模型與第三方套件各有授權，詳見 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
