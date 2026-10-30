以下為內嵌程式碼的靜態審查，未執行命令或測試。行號為約略位置，附函式名定位；未提供 AC 全文、`uv.lock` 與來源審查文件，因此相關符合性無法完整確認。

- **Blocker — 重啟後 partial 字幕遭拒收**  
  `core/pipeline.py:231`（`_load_and_run`）、`core/captions.py:149`（`clear`）  
  每次啟動建立新 Segmenter，`utt_id` 從 1 開始；但 `board.clear()` 沒有清除 `_max_final_utt`、`_finalized_utts`。上一場辨識 100 段後，重新開始的前 100 段 partial 都被拒收。切換麥克風也會觸發。**修正：**每個 session 建立新的 CaptionBoard，或另設完整 session reset；同步重設延遲、音量及快照快取。

- **Blocker — 停止逾時仍開放重啟，舊 worker 可污染新 session**  
  `core/pipeline.py:158,273`（`stop`、`_join`）  
  `join(timeout=8)` 後不檢查執行緒是否仍活著，直接清除追蹤並回報 IDLE；loader 更未納入追蹤。慢速推論尚未返回便重啟時，舊 `_run_asr()` 仍會修改共用 `_utt_lang`、`_partial_cost` 等狀態；舊 capture 的 flush 也可能寫入新工作的共用佇列。事件上的 session 檢查不足以保護這些操作。**修正：**讓模型、佇列、上下文屬於獨立 session；所有 worker 結束前不得宣告可重啟，或用可終止的子程序隔離推論。停止流程也應管理 loader 與局部音訊資源。

- **Blocker — 模型硬性政策僅驗名稱，Ollama 可繞過**  
  `models.py:101`（`ollama_model_allowed`）、`core/translate.py:67`  
  任意來源權重只要被本機命名成 `gemma3:custom`，甚至 `gemma3-anything`，便通過檢查。合法名稱不代表合法來源，無法落實禁止特定來源模型的硬規則。**修正：**使用經審查的精確模型與 manifest／權重 digest 清單，驗證實際載入內容及基底來源；無法驗證時停用 Ollama，退回 CT2。

- **Major — Windows 熱鍵可能註冊成功卻完全無效**  
  `ui/hotkey.py:29`（`nativeEventFilter`）、`ui/app.py:61`  
  `RegisterHotKey(None, ...)` 使用執行緒訊息，filter 卻只處理 `windows_generic_MSG`，漏掉 Qt 的 `windows_dispatcher_MSG`。因此 UI 可能顯示註冊成功，實際按鍵不觸發；沒有系統匣時甚至允許隱藏後無法叫回。**修正：**處理 dispatcher 訊息，僅攔截本 manager 已註冊的 ID；部分註冊失敗時回滾或逐鍵回報。需補真實 Windows 訊息路徑測試。

- **Major — 翻譯校正的 CPU fallback 沒有帶進執行期**  
  `setup_cli.py:111`（`probe_translation`）、`core/translate.py:154`（`build_translator`）  
  CUDA 翻譯失敗時，setup 改測 CPU 並回報成功，卻仍保存原 NVIDIA profile；正式啟動又使用 CUDA，失敗後直接沒有翻譯。ASR 與兩個翻譯模型也未在校正時同時駐留，可能漏掉顯存不足。**修正：**執行期實作 CUDA→CPU 翻譯 fallback，校正實際共同駐留配置；CPU 翻譯也失敗時，不應無條件宣告完整安裝成功。

- **Major — 實際推論失敗不會降級，收音失敗仍顯示收音中**  
  `core/pipeline.py:251,265,291,363`  
  `_build_asr()` 只驗證建構；warmup 失敗被吞掉，後續 ASR 錯誤僅更新訊息並繼續失敗。capture 發生例外則退出，但狀態仍為 RUNNING。**修正：**候選後端必須通過實際推論才採用；持續推論失敗應進行受控降級，收音失敗應清理資源並進入 FAILED。保底模型已下載，不代表目前執行路徑真的會使用它。

- **Major — 舊句取消可讓更新的 partial 永久不淡出**  
  `core/captions.py:93,104`（`set_partial`、`cancel_partial`）  
  ASR 正在處理第 1 段時，capture 可能已送出第 2 段的 cancel。coordinator 先把 `_max_final_utt` 提升至 2，稍後第 1 段 partial 被拒收。另一種順序下，已顯示的第 1 段 partial 不會被第 2 段 cancel 清除；若它的 final 推論再失敗，`partial is not None` 會一直阻止淡出。**修正：**取消與完成狀態按 utterance 分別追蹤，避免較新 cancel 把較舊有效結果一律封鎖；final 失敗也必須送出清除事件。

- **Major —「離線、本機」限制未涵蓋 Ollama**  
  `core/translate.py:69,96`、`config.py:67`  
  `ollama_url` 接受任意遠端網址，辨識文字及前文會送往該站；`HF_HUB_OFFLINE` 對此無效，urllib 預設亦可跟隨重新導向。**修正：**依本機契約限制 loopback 位址、禁止離開本機的重新導向與代理路徑。若允許遠端服務，必須明確列為另一種連線模式。

- **Major — 固定 revision 沒有落實到本機模型驗證**  
  `models.py:122–160`（`local_dir`、`is_downloaded`、`resolve`）  
  模型目錄只含 repo，不含 revision；必要檔案非空就視為完成。更新 registry 的 commit 後，舊權重仍直接使用；損壞但非空的模型也無法透過重跑 setup 修復。**修正：**使用 revision 專屬目錄及完成 manifest，記錄並驗證檔案雜湊；`resolve()` 同樣檢查政策及版本。

- **Major — 字幕溢位裁切破壞句首；修正屬於我的 overlay**  
  `ui/overlay.py:239`（`kept = lines[-d.max_lines:]`）  
  目前直接取排版尾端行，必然可能從舊句中間開始。**設計：**依既有交錯結構，把內容 span 與 joiner 分開辨識，保留空字串中文 joiner 的結構資訊；逐次移除最舊內容 span 及其後 joiner，重新排版剩餘完整後綴，直到行數符合上限。只有最後一個內容 span 單獨仍超長時，才取它的尾端行。保留 dim 與 UTF-16 索引，寬度、字級及翻譯回填後重新計算；不修改 Board 歷史。補測中英文 joiner、partial、emoji、換行及最新句單獨超長。注意目前 span 實際代表一次 ASR final，未保證是一個語法句子。

- **Minor — 第二語言比例設定完全未使用**  
  `ui/overlay.py:129,185`（`target_geometry`、`_cache_paths`）  
  `secondary_scale=0.8` 有設定卻沒有任何排版效果。**修正：**第二區使用獨立字型及 metrics，高度按各區實際行高加總；整句裁切也使用各區自己的 metrics。這也是我的 UI 應修正之處。

- **Minor — Windows 安裝有路徑與桌面重新導向缺口**  
  `setup_cli.py:48,157`（`bench_audio`、`create_shortcuts`）  
  TTS 的 PowerShell 路徑未跳脫單引號，含 `'` 的使用者路徑會合成失敗，靜默改用非語音訊號校正；桌面固定為 `%USERPROFILE%\Desktop`，OneDrive／重新導向桌面可能拿不到捷徑。**修正：**正確跳脫或透過參數傳路徑；透過 Windows Known Folder 取得桌面，檢查子程序結果並呈現失敗。

- **Minor／待確認 — 效能數字不足以驗證翻譯延遲 AC**  
  `core/pipeline.py:491`、`setup_cli.py:93`  
  UI 延遲只量到原文 final，不包含翻譯；校正取兩次解碼最快值，且未包含同時執行的翻譯負載。因此所述 p50 約 2 秒不能直接代表雙語完成時間。**修正：**分別量測原文、譯文的句尾延遲與較高百分位，再依 AC 定義判定；AC 原文未提供，此處不直接判定違規。