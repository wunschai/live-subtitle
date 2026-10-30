# 程式碼審查回應（第 2 輪）

日期：2026-10-30。

三份獨立審查：

| 審查者 | 範圍 | 紀錄 |
|---|---|---|
| GPT/codex | 全專案 | [02-code-review-codex.md](02-code-review-codex.md) |
| Claude 審查員 A | 核心 runtime | 本文節錄 |
| Claude 審查員 B | 安裝與整合 | 本文節錄 |

**欄位說明**：「回歸測試」欄是新增的測試或驗證方式；「—」表示沒有可自動化的測試，原因寫在處理欄。

## 處理結果

| 來源 | 嚴重度 | 問題 | 處理 | 回歸測試 |
|---|---|---|---|---|
| GPT | blocker | 重啟後 partial 被拒收：board 跨工作階段共用，但 utt_id 每次從 1 開始 | `Pipeline` 改為每次 `start()` 建立獨立的 `_Session`，包含佇列、字幕板、上下文、引擎 | `test_restart_accepts_partials_from_new_session` |
| GPT | blocker | 停止逾時仍可重啟，舊 worker 會污染新工作階段 | 舊執行緒只能碰自己的 `_Session`。逾時沒結束的執行緒列入 `_lingering`，期間拒絕重啟並提示。loader 也納入追蹤 | `test_stuck_inference_blocks_restart_until_finished` |
| GPT | blocker | Ollama 只檢查名稱，改名就能繞過 | 釘選 9 個模型的**權重層 sha256**（取自 registry manifest）。執行時讀取 `/api/show` 中 modelfile 的 `FROM …sha256-…` 比對 | `test_renamed_model_with_unknown_weights_rejected` |
| GPT | major | Windows 熱鍵註冊成功卻不觸發 | 實測確認：`WM_HOTKEY` 根本不會進 Qt 的事件迴圈。改成獨立執行緒自己跑 Win32 `GetMessageW` 迴圈，再用 Qt signal 轉回主執行緒。註冊採全有或全無 | 實機以 `PostThreadMessage` 驗證 |
| GPT | major | 翻譯在 CUDA 失敗時，執行期沒有退回 CPU | `Pipeline._build_mt` 失敗時改用 CPU int8 重試 | `test_translator_falls_back_to_cpu` |
| GPT | major | 只驗證能建構就採用，實際推論失敗不降級；收音失敗仍顯示收音中 | 候選等級必須「載入＋實際推論」都成功才採用。final 連續失敗 3 次 → FAILED。收音例外 → FAILED | `test_warmup_failure_falls_back_to_next_profile`、`test_persistent_asr_failure_goes_failed_and_clears_partial`、`test_capture_error_goes_failed` |
| GPT | major | 較新段落的 cancel 會擋掉較舊段落；final 失敗後 partial 永遠不淡出 | `cancel_partial` 不再推進 `_max_final_utt`。ASR 失敗時送出 cancel 清掉該段的 partial | 同上 |
| GPT | major | Ollama 可以連到遠端網址 | 只允許 loopback 位址，不經系統代理、不跟隨轉址 | `test_loopback_only`、`test_redirect_is_not_followed` |
| GPT | major | 固定 revision 沒有落實到本機 | 每個 revision 一個資料夾，加上 `.manifest.json`（大小與 sha256）。下載時比對 HF 公布的 LFS sha256。setup 會完整重算雜湊，損壞就重新下載 | `test_revision_change_invalidates`、`test_verify_detects_same_size_corruption` |
| GPT | major | 字幕溢出時從句子中間開始 | overlay 改為整句丟掉最舊的內容；最新一句單獨就超過行數時，才保留它的尾端 | `test_overflow_drops_whole_old_sentences_*` 加實機截圖 |
| GPT | minor | `secondary_scale` 沒有作用 | 照 plan 決策 #14 刪除這個設定 | — |
| GPT／B | minor／important | TTS 路徑含引號會失敗；桌面路徑寫死 | 路徑改用環境變數傳給 PowerShell；桌面與開始選單改用 `[Environment]::GetFolderPath` | 程式碼審閱（setup_cli.py 沒有測試） |
| GPT | minor | 延遲沒有涵蓋翻譯 | 新增 `mt_latency_s`，面板顯示「延遲 2.2 秒（譯 +0.2 秒）」 | `test_status_shows_translation_latency_and_overflows` |
| A | important | `collapse_repeats` 把 10000 截成 10 | 數字與純符號片段不做重複截斷 | `test_numbers_are_never_collapsed`（7 個案例） |
| A | minor | OpenVINO 判成日文或韓文時沒有處理 | 改以中文重跑 | — |
| A | minor | 短句的語音覆蓋率永遠低於 0.4，被當成低信心 | 覆蓋率只計算 pre-roll 之後、句尾靜音之前的核心區段 | `test_short_clean_word_has_high_voiced_ratio` |
| A | minor | 麥克風拔除後偵測不到；溢位次數沒有顯示 | 3 秒沒有收到資料時檢查 `stream.active`，串流已死就丟錯 → FAILED。`overflows` 顯示在面板（掉音 N 次） | — |
| A | minor | 沒有標點的中文多句黏成一串 | 前一句沒有句尾標點時，補一個全形空格 | `test_unpunctuated_chinese_lines_get_separator` |
| B | important | 設定檔等級名稱錯誤時，在 pythonw 下無聲退出 | 載入時把不存在的等級改回自動偵測並寫入 log。設定 `sys.excepthook` 與 `threading.excepthook`，未處理的例外寫進 log | — |
| B | important | CUDA：cuDNN 版本與 CT2 內附版本不一致；舊卡不支援 fp16；白下載數 GB | cuDNN 鎖定 9.10.x，與 CT2 4.8 內附版本一致。新增 `pick_compute_type`，依裝置實際支援的類型挑選。下載前先檢查 `cuda_ready()` | 尚未在 NVIDIA 實機驗證（見已知限制） |
| B | important | 等級存在漫遊設定檔中，會被帶到另一台電腦 | 設定改存在本機的 `%LOCALAPPDATA%`，不漫遊 | — |
| B | important | 校正只看速度，不看辨識結果 | 有預期文字時，相似度必須 ≥ 0.5；沒有時至少輸出不可為空 | — |
| B | minor | install.ps1 出錯時視窗直接關閉 | 加上 `trap`、`Start-Transcript`（install.log）與結尾的 `Read-Host` | PowerShell 5.1 parse 通過 |
| B | minor | 執行期的 `cpu_threads` 與校正時不一致 | setup 把 `cpu_threads` 寫入設定，執行期沿用 | — |
| B | minor | 換麥克風後重啟遇到舊執行緒，會放棄重啟 | 保留 pending，每 0.5 秒重試 | — |

## 未處理（已知限制）

| 項目 | 原因 |
|---|---|
| 校正沒有跑「完整 pipeline 加混合音檔」（plan 決策 #1） | 目前是 5 秒解碼時間加上辨識相似度。實機另外用中英混合音檔量到延遲中位數 2.0–2.2 秒（intel-gpu），列為下一輪工作 |
| ASR 與翻譯模型同時駐留時的顯存是否足夠（GPT） | 需要 NVIDIA 實機才能測。執行期翻譯在 CUDA 失敗時會自動退回 CPU |
| NVIDIA 等級整組流程 | 本機沒有 NVIDIA 顯示卡。程式路徑有單元測試，但沒有在實機上驗證過 |
| Ollama 權重驗證 | 本機沒有安裝 Ollama。驗證邏輯以假的 HTTP 伺服器測試；digest 取自 registry manifest |

## 協作備註

UI 修正回合原本要交給 GPT/codex 實作，但 codex 在 13:00 達到使用額度上限（14:29 恢復）。為了不中斷進度，這一輪由 Claude 依 GPT 自己在審查中提出的設計實作：overlay 的整句截斷、面板的狀態列。額度恢復後，會請 GPT 審查這些變更（見 04）。
