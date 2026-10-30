# UI 實作需求單（給 GPT/codex）

你是 live-subtitle 專案的共同工程師（Claude 負責核心與整合，你負責 UI 層）。請依下列契約實作 PySide6 UI。

## 背景
桌面即時字幕程式：講者說中文／英文，桌面最上方或最下方出現字幕列（可中英雙語）。另有一個可隱藏的小控制面板與系統匣。使用者以繁體中文（台灣）為主，所有 UI 文字用台灣正體中文。

## 限制
- 只能用 PySide6-Essentials 的 QtCore / QtGui / QtWidgets（沒有 QtQuick、沒有其他第三方 UI 套件）。Python 3.12。
- 不要修改核心檔案；只輸出下面列出的檔案。
- 程式風格：簡潔、型別標註、中文註解（只在必要處）、不要過度抽象。
- 尺寸一律用 Qt 邏輯像素；字級以 px 設定（QFont.setPixelSize）。
- 字型：settings.font_family 為空時依序嘗試 "Microsoft JhengHei UI", "Microsoft JhengHei", "Noto Sans TC", "PingFang TC", "Segoe UI"，用 QFontDatabase.families() 挑第一個存在的。

## 要輸出的檔案
1. `src/livesub/ui/theme.py` — 面板的 QSS（深色、小巧、圓角，現代感；按鈕/下拉/滑桿/群組），以及共用常數（顏色等）。
2. `src/livesub/ui/overlay.py` — `OverlayWindow(QWidget)`
3. `src/livesub/ui/panel.py` — `ControlPanel(QWidget)`
4. `src/livesub/ui/tray.py` — `TrayIcon(QSystemTrayIcon)`（圖示用 QPainter 即時畫一個簡單的「字幕」圖示，不依賴外部圖檔）
5. `tests/test_ui_overlay.py`、`tests/test_ui_panel.py` — pytest-qt（`qtbot` fixture）測試，在 `QT_QPA_PLATFORM=offscreen` 下可跑；檔頭加 `pytestmark = pytest.mark.gui`。

## OverlayWindow 規格
- 旗標：`FramelessWindowHint | WindowStaysOnTopHint | Tool | WindowTransparentForInput | WindowDoesNotAcceptFocus`；屬性 `WA_TranslucentBackground`、`WA_ShowWithoutActivating`、`WA_TransparentForMouseEvents`。
- `apply_settings(d: DisplaySettings)`：位置 top/bottom、style outline/box、box_opacity、font_size、max_lines、width_pct、margin_px、screen、font_family 立即生效；不要每次重建原生視窗（避免閃爍），只在必要時 setGeometry/update。
- 幾何：取 `QGuiApplication.screens()[d.screen]`（索引超出→primaryScreen）的 `geometry()`；寬 = geometry.width * width_pct/100，水平置中；top 時 y = geo.top + margin，bottom 時 y = geo.bottom - margin - height。
- 高度固定：= 區塊數 × (max_lines × 行高) + 區塊間距 + 上下 padding，其中行高 = QFontMetrics(font).lineSpacing()。區塊數：模式 bilingual 時 2，否則 1（overlay 依 snapshot 的 blocks 數量決定；尚未收到 snapshot 時依 `set_block_count(n)` 或預設 1）。高度不隨文字長短改變（只隨設定改變）。
- `target_geometry() -> QRect`：回傳依目前設定計算的位置（供測試）。
- 監聽 `QGuiApplication.screenAdded/screenRemoved` 與目前螢幕的 `geometryChanged`，重新定位；選定螢幕消失時回主螢幕。
- `show_snapshot(snap: BoardSnapshot)`：每個 Block 用 QTextLayout 依可用寬度換行，**只畫最後 max_lines 個視覺行**（保留最新內容；被截掉時在第一行開頭加「…」可選）。Span.dim=True 的文字用較淡的顏色（例如 60% 不透明度的白／灰）。中文區塊在上、英文在下（照 blocks 順序）。文字置中對齊。
- 繪製：
  - `outline`：無背景；白字 + 黑色描邊（QPainterPath.addText 後先以黑筆描邊 strokeWidth ≈ font_size/9，再填白）+ 輕微陰影。
  - `box`：圓角矩形半透明黑底（alpha = box_opacity），白字（仍加細描邊以提高可讀性）。
- 淡入淡出：snap.visible=False 時 300ms 淡出（windowOpacity 動畫），之後收到 visible=True 且非空的 snapshot 時淡入。snapshot 為空（snap.empty）時不畫任何東西（box 模式也不畫底框）。
- 避免在 paintEvent 裡做重運算：layout 結果在 show_snapshot / apply_settings 時快取。

## ControlPanel 規格
- 精簡（約 320×260 邏輯像素），標題「即時字幕」，視窗 `Qt.Tool` 以外的一般小視窗，可置頂（`WindowStaysOnTopHint`）。
- 主區：
  - 大按鈕「開始」/「停止」（依狀態切換文字；LOADING/STOPPING 時停用）。
  - 狀態列：`STATE_LABELS[state]` + `profile_label` + 延遲（例如「延遲 1.2 秒」）；`segmented=True` 時加註「分段字幕」；`message` 非空時以警告色顯示。
  - 顯示模式（下拉或分段按鈕）：原文 / 中文 / 英文 / 中英雙語 → mode = original / zh / en / bilingual
  - 位置：上方 / 下方
  - 字級：滑桿 16–96 + 數字顯示
  - 「隱藏面板」按鈕（`can_hide=False` 時停用並顯示 tooltip「系統匣與熱鍵都不可用，無法隱藏」）
- 「進階」可展開區（QToolButton 箭頭或可勾選的 QGroupBox；預設收合，展開時視窗長高）：
  - 麥克風下拉（`set_devices(devices, current)` 填入；第一項「系統預設」對應 device=""）+ 音量表（QProgressBar 0–100，取 status.level）
  - 來源語言：自動 / 中文 / 英文 → source_language = auto / zh / en
  - 樣式：直接顯示（描邊）/ 半透明底框 → style = outline / box
  - 底框透明度滑桿 0–100%（style=outline 時停用）
  - 行數 1–3（QSpinBox）
  - 寬度 30–100%
  - 螢幕下拉（列出 QGuiApplication.screens() 的名稱與解析度）
- 每次控制項改變 → 更新內部 Settings 的對應欄位 → `settingsChanged.emit(copy.deepcopy(settings))`。程式化設定控制項（初始化、set_status）時不可觸發 settingsChanged（用 QSignalBlocker）。
- `set_hotkey_ok(False)`：在狀態列下方顯示一行小字「全域熱鍵註冊失敗，請用系統匣叫回面板」。正常時顯示「Ctrl+Alt+H 顯示/隱藏面板 · Ctrl+Alt+S 開始/停止」。
- closeEvent：若 can_hide 為 True → 改成隱藏並 emit hideRequested（不要真的關閉）；否則接受關閉並 emit `quitRequested`（請額外宣告這個 Signal）。

## TrayIcon 規格
- 選單：「顯示/隱藏面板」→ toggleRequested、「開始」/「停止」→ startStopRequested（文字隨 set_running 切換）、分隔線、「結束」→ quitRequested。
- 左鍵單擊（activated reason Trigger）→ toggleRequested。
- tooltip「即時字幕」。

## 測試要點（pytest-qt，offscreen）
- overlay：apply_settings 改 position/width_pct/margin/font_size/max_lines 後 target_geometry 正確（寬度比例、置中、top/bottom、高度隨 max_lines 與 bilingual 增加、高度不隨文字長短改變）；show_snapshot 給極長文字不會 crash；screen 索引超出時回主螢幕；visible=False 會啟動淡出。
- panel：改每個控制項都會 emit 正確的 Settings；set_status 不會 emit settingsChanged；開始/停止按鈕依狀態切換與停用；can_hide=False 時隱藏鈕停用。
- 測試可用 `from livesub.config import Settings, DisplaySettings`、`from livesub.core.captions import BoardSnapshot, Block, Span`、`from livesub.core.status import PipelineStatus, PipelineState, STATE_LABELS`。

## 輸出格式（重要）
你的沙箱無法寫檔，請把每個檔案完整內容用以下格式輸出（不要用 markdown code fence 包住檔案內容）：

=====FILE: src/livesub/ui/overlay.py
<完整檔案內容>
=====END

所有檔案輸出完後，最後附一小段「給 Claude 的整合說明」（Signal 連接方式、已知限制）。

## 核心契約原始碼（唯讀參考）
