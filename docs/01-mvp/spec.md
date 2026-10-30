# 01-mvp 規格書

> 以 `plan.md` 為準（含「審查後決策」）。本文件定義 User Story、驗收條件（AC）、介面契約與 ADR。

## User Stories

- **US1 字幕**：身為講者，我開始說話後，桌面上方或下方就出現我說的話的字幕，中文顯示為台灣正體。
- **US2 翻譯**：身為台下聽眾，我能看到英文或中文譯文，或中英兩行同時顯示。
- **US3 控制**：身為操作者，我用一個小面板開始或停止、切換模式、位置、樣式和字級，並且可以把面板藏起來。
- **US4 安裝**：身為 IT 人員，我在任何 Windows 電腦上執行 `install.ps1`，程式會自己判斷硬體、裝好對應的套件和模型，之後不需要網路也能用。
- **US5 合規**：身為管理者，我能確認程式只會載入經過審查、非中國來源的模型。

## 驗收條件

### AC-ASR 語音轉字幕

| # | 驗收條件 |
|---|---|
| 1 | 說一句中文並停頓 ≥ 0.5 s 後，該句會以 final 字幕出現。有 partial 的等級，說話中就以淡色顯示目前辨識到的文字。字幕為正體字，且中文標點為全形。 |
| 2 | 說英文時顯示英文原文。中英夾雜（如「這個 API 很快」）時，英文詞保留原樣。 |
| 3 | 連續說話超過 `max_segment_s` 也會分段出字幕，不會一直等到停頓才出現。 |
| 4 | 靜音或環境雜音不產生字幕。強幻覺句（字幕由Amara.org社區提供、訂閱…）永遠不出現。講者清楚說出的「謝謝大家」會保留。 |
| 5 | 有 partial 的等級，說話中每隔約 `partial_interval_s` 更新一次淡色的暫定文字。final 出現後該行固定，晚到的舊 partial 不會覆蓋它。 |

### AC-MT 翻譯與顯示模式

| # | 驗收條件 |
|---|---|
| 1 | 四種模式：`original`（原文）、`zh`（中文）、`en`（英文）、`bilingual`（中文行在上、英文行在下）。 |
| 2 | 中文句子翻成英文；英文句子翻成中文，且譯文為台灣正體。 |
| 3 | 在 `zh`/`en` 模式下，譯文還沒出來時先以淡色顯示原文，譯文到了再替換；在 `bilingual` 模式下，待翻譯的那一行顯示「…」。 |
| 4 | 翻譯失敗時以淡色原文暫代，不會讓字幕留白。翻譯結果不會亂序，也不會回填到已經淘汰的行。 |
| 5 | 切換模式時立即重繪，不需要重新開始。 |

### AC-UI 字幕列與面板

| # | 驗收條件 |
|---|---|
| 1 | 字幕列無邊框、永遠置頂、不出現在工作列、點擊會穿透、不搶焦點；位置在所選螢幕的最上方或最下方，水平置中，寬度為 `width_pct`%。 |
| 2 | 樣式 `outline` 為無背景的白字加黑邊；`box` 為半透明黑底，透明度 0–100% 可調。 |
| 3 | 字級 16–96 px 可調。每個語言區塊最多 `max_lines`（1–3）個視覺行，超出時保留最新的內容。字幕列高度固定，不隨文字跳動。 |
| 4 | 精簡面板包含：開始/停止、狀態（等級、狀態、即時與定稿延遲）、模式、位置、字級、隱藏。「進階」展開後有：麥克風（附音量表）、來源語言、樣式、透明度、行數、寬度、螢幕。 |
| 5 | 隱藏面板後，可以從系統匣或 `Ctrl+Alt+H` 叫回。`Ctrl+Alt+S` 切換開始/停止。熱鍵註冊失敗時面板會顯示提示；系統匣也不可用時停用隱藏按鈕。 |
| 6 | 所有設定改動後 0.5 s 內寫進 config，重新啟動後還原。 |
| 7 | 最近一次活動之後 `clear_after_s` 秒沒有新內容，字幕淡出；有新內容時再淡入。 |
| 8 | 隨機操作所有控制項（10 個種子，每個 300 步）都不會產生例外，overlay 的幾何數值永遠有限且為正。 |

### AC-HW 安裝與等級

| # | 驗收條件 |
|---|---|
| 1 | `select_profile` 對 plan 等級表中每一列的條件都回傳正確的等級（表格驅動測試）。 |
| 2 | `install.ps1` 在沒有 uv 的電腦上會先安裝 uv。extra 是 `none` 時不帶 extra 參數。整個流程不需要系統管理員權限。 |
| 3 | `livesub-setup` 會下載該等級的模型與 CPU 保底模型，實際載入並跑一次推論和校正（速度＋辨識相似度）；不達標就沿 fallback 鏈降級。選定的等級與 `cpu_threads` 寫入 config 的 `[runtime]`，完整探測紀錄寫入同資料夾的 `setup.json`。 |
| 4 | 執行期 ASR 初始化失敗時，沿 fallback 鏈自動退到第一個模型已下載的等級，並在面板上顯示警告。 |
| 5 | 設定 `HF_HUB_OFFLINE=1` 時，啟動和推論都正常（模型已在本機）。 |

### AC-LAT 延遲目標（p50）

- **即時**：partial 請求送出 → 淡色字出現。
- **定稿**：句尾停頓開始 → final 原文出現（含 0.5 s 句尾等待）。

| 等級 | 即時 | 定稿 |
|---|---|---|
| nvidia-* | ≤ 1.0 s | ≤ 1.5 s |
| intel-gpu | ≤ 1.0 s | ≤ 1.5 s |
| intel-gpu-lite | ≤ 1.0 s | ≤ 2.0 s |
| cpu-high、cpu-low | —（UI 標示為「分段字幕」） | ≤ 4.0 s |

實測（2026-10-30，Core Ultra 7 155H + Arc，intel-gpu，麥克風）：即時 0.34 s，定稿 1.25 s。

譯文在原文 final 之後 ≤ 1.0 s 內出現（CT2 翻譯器）。

### AC-POL 模型合規

| # | 驗收條件 |
|---|---|
| 1 | registry 中每個模型都有固定的 40 碼 revision、必要檔案清單、來源、基底與轉檔者；`check_policy()` 回傳空清單。 |
| 2 | 名稱命中禁用名單的模型無法下載或載入，Ollama 模型不在 allowlist 時拒絕使用。 |
| 3 | `docs/MODELS.md` 和 `THIRD_PARTY_NOTICES.md` 列出所有模型與套件的授權，其中包含 quickmt 的 CC-BY 標示。 |

## 介面契約

### 核心 → UI（`livesub.core.captions`）

```python
@dataclass(frozen=True)
class Span:  text: str; dim: bool = False      # dim = partial / 等譯文
@dataclass(frozen=True)
class Block: lang: str; spans: tuple[Span, ...] # lang: "zh" | "en" | "orig"
@dataclass(frozen=True)
class BoardSnapshot: blocks: tuple[Block, ...]; visible: bool; revision: int
```

- 單語模式只有一個 Block，雙語模式有兩個（zh、en 依序）。
- Block 內的 spans 是由多句組成的連續文字（舊 → 新）。overlay 自行換行，並只保留最後 `max_lines` 個視覺行。

### Pipeline 狀態（`livesub.core.status`）

```python
class PipelineState(str, Enum): IDLE, LOADING, RUNNING, STOPPING, FAILED
@dataclass(frozen=True)
class PipelineStatus:
    state: PipelineState; profile: str; profile_label: str; asr_model: str
    latency_s: float | None      # 最近 final 的延遲（句尾 → 出字）
    mt_latency_s: float | None   # 原文 → 譯文
    partial_latency_s: float | None  # partial 請求送出 → 畫面出字
    overflows: int               # 掉音次數
    level: float                 # 0–1 麥克風音量
    segmented: bool              # True = 分段字幕模式（CPU）
    message: str                 # 給使用者看的提示／警告（可空）
```

### UI 元件（`livesub.ui`，PySide6）

```python
class OverlayWindow(QWidget):
    def __init__(self, settings: DisplaySettings): ...
    def apply_settings(self, settings: DisplaySettings) -> None   # 位置/樣式/字級/行數/寬度/螢幕，立即生效
    def show_snapshot(self, snap: BoardSnapshot) -> None          # 重繪；visible=False 時淡出
    def target_geometry(self) -> QRect                            # 供測試：目前應在的位置（邏輯像素）

class ControlPanel(QWidget):
    settingsChanged = Signal(object)    # 發出新的 Settings（深拷貝）
    startRequested = Signal(); stopRequested = Signal(); hideRequested = Signal()
    def __init__(self, settings: Settings, can_hide: bool = True): ...
    def set_devices(self, devices: list[str], current: str) -> None
    def set_status(self, status: PipelineStatus) -> None
    def set_hotkey_ok(self, ok: bool) -> None

class TrayIcon(QSystemTrayIcon):
    toggleRequested = Signal(); startStopRequested = Signal(); quitRequested = Signal()
    def set_running(self, running: bool) -> None
```

## ADR

- **ADR-1 GUI 用 PySide6**（LGPL）。它支援透明、置頂、點擊穿透和系統匣。Tkinter 的透明度支援差；Electron 太重。
- **ADR-2 ASR 用 faster-whisper（CT2）加 OpenVINO GenAI 雙後端**。CT2 不支援 Intel GPU；實測 OpenVINO 在 Arc 內顯上比 CPU 快 3–8 倍。
- **ADR-3 翻譯預設用 quickmt CT2**。在中英兩個方向上，它的品質和速度都勝過 OPUS-MT；授權 CC-BY 只要求標示出處。LLM（Ollama）作為選用。
- **ADR-4 VAD 加 final 為主的串流策略**，partial 為輔且間隔自適應。LocalAgreement 留到之後再做。原因：在 CPU 和內顯上，每次重解的成本接近固定，重解太頻繁做不到即時。
- **ADR-5 核心不依賴 Qt**，由單一 coordinator 執行緒擁有狀態，UI 只接收快照。
- **ADR-6 模型 allowlist**：固定 revision、必要檔案、來源證據；禁用名單只是第二道防線。
