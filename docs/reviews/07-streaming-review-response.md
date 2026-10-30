# 審查回應：邊講邊出字

日期：2026-10-30。

**背景**：使用者要求「一邊講一邊出字」，延遲 0.5–1 秒。設計與實測數據見 [plan.md「邊講邊出字」](../01-mvp/plan.md)。

三份獨立審查：

| 審查者 | 範圍 | 紀錄 |
|---|---|---|
| GPT/codex | 全部變更 | [06-streaming-review-codex.md](06-streaming-review-codex.md) |
| Claude 審查員 A | 執行緒安全與競態（另用實驗重現了優先權反轉） | 本文節錄 |
| Claude 審查員 B | 安裝、設定、模型政策、文件 | 本文節錄 |

修正後再請 GPT 複審：[08-streaming-recheck-codex.md](08-streaming-recheck-codex.md)。

## 處理結果

| 來源 | 嚴重度 | 問題 | 處理 | 回歸測試 |
|---|---|---|---|---|
| GPT 1、2、3／A1、A6 | major | 共用引擎時 partial 先拿走 slot，再卡在鎖上等：final 優先失效、跑到過期音訊、停止時多跑一輪 | 共用引擎改回單一 worker，每次推論前才決定跑什麼（final 優先，否則取最新 partial）。只有獨立引擎才開兩條執行緒，並移除 `asr_lock` | `test_shared_engine_never_overlaps_and_final_goes_first` |
| A2 | major | 獨立引擎下，下一句的 partial 比上一句的 final 先到，上一句的預覽消失 | `CaptionBoard` 每個未定稿段落各保留一份 partial（`partials: dict`），依段落順序顯示 | `test_next_partial_arriving_before_previous_final_keeps_both`、`test_final_clears_all_older_partials_only` |
| A3／B4 | major | 約 0.3 秒語音的第一個 partial（base 模型）鎖定整句語言，影響 final 的 prompt | 只採用音訊 ≥ 1.4 秒（含 0.4 秒 preroll）的 partial 語言判斷 | `test_short_partial_does_not_lock_sentence_language` |
| GPT 7 | major | 晚到的 partial 會把已定稿段落的語言快取寫回去 | final 完成後記錄 `max_final_utt`，比它舊的 partial 不再寫入 | `test_late_partial_after_final_does_not_revive_language_cache` |
| A4 | major | 獨立 partial 引擎執行期持續失敗時沒有提示、洗 log，也不退回 | 連續失敗 3 次就停用即時辨識（`partials_off`），定稿照常，面板顯示提示。只記第一次的 warning；統計拆出 `partial_errors` | `test_broken_partial_engine_is_disabled_but_finals_continue` |
| GPT 4／B3 | major | setup 只校正 final 模型，沒驗證 partial 模型，也沒量並行負載 | 兩個引擎都載入並檢查輸出；在 partial 持續推論時量定稿時間；partial 中位數上限 `PARTIAL_BUDGET` 0.6 秒，未通過就降級 | `test_setup_cli.py`（3 項） |
| B1／A5 | major | 舊的內顯使用者升級後缺新模型，一路退到 cpu-low，畫面沒有提示 | 退回的提示改成明確要求重新執行 `install.ps1`；README 加上升級說明 | `test_missing_models_note_tells_user_to_reinstall` |
| B2 | major | 預算收緊後，弱內顯會被推到更慢、而且沒有即時字的 CPU 等級 | lite 是鏈上最後一個 GPU 等級，放寬到 1.4 秒；intel-gpu 1.2 秒，量的是並行負載下的時間 | — |
| B5 | major | intel-gpu-lite 的定稿改成 base 沒有說明；RAM 分界原本是為 turbo 設的 | Intel RAM ≥ 7.5 GB 就選 intel-gpu（small＋base 不到 400 MB）。lite 改為「校正太慢時的退路」，文件已說明 | `test_profiles.py`（ram=8 → intel-gpu，ram=4 → lite） |
| GPT 5 | minor | partial 引擎退回共用時，使用者看不到 | `_build_partial_asr` 回傳提示，顯示在面板 | `test_partial_engine_init_failure_falls_back_to_shared_with_message` |
| GPT 6 | minor | 統計名稱不精確 | 停止時的 log 標成「最近 20 句」；spec 寫明「即時」的計算起點（partial 請求送出） | — |
| B7 | minor | `partial_model` 拼錯會直接讓啟動失敗；等級表沒有測試守住 | `is_downloaded` 移進 try；`test_all_profile_models_registered` 加檢查 partial 模型的 kind | `test_all_profile_models_registered` |
| B8 | minor | partial 模型下載失敗會中止整個 setup | 只警告，校正時判定該等級不可用並降級 | `test_partial_model_download_failure_does_not_abort` |
| GPT 7／A 測試缺口 | major | 沒有測試涵蓋獨立引擎路徑 | 新增並行、退回、停用、亂序、語言快取等測試 | `test_separate_partial_engine_runs_in_parallel_with_final` 等 |
| B6、B10、B11 | minor／nit | MODELS.md 缺紀錄；README、spec、plan 過時；`size_mb` | 已更新（含評估過但沒採用的 Moonshine、Voxtral、Confucius） | — |

## 修正中另外發現的問題

**校正的相似度算法**：測試音只取前 5 秒，句子會被截斷，原本卻拿整句比對，small 只得 0.46，低於門檻 0.5，會被誤判降級；以前 turbo 剛好過關。改成「輸出的字有多少比例對得上預期文字」，這才是這道檢查原本的用意：抓出 GPU 出錯時的亂碼。

回歸測試：`test_similarity_tolerates_truncated_bench_audio`。

## GPT 複審（08）後的處理

GPT 確認共用引擎排程、跨句預覽、短音訊語言判斷、partial 引擎停用、升級提示都已修正。新發現的處理：

| 嚴重度 | 問題 | 處理 | 回歸測試 |
|---|---|---|---|
| major | 校正時 partial 背景執行緒出錯或卡住，該等級仍會通過 | 背景例外改為傳回主執行緒；沒有樣本、出錯、超過 `PARTIAL_JOIN_S`（10 秒）沒結束，都判定失敗。並且等 partial 開始推論後才量定稿 | `test_probe_fails_when_partial_breaks_under_load`、`test_probe_fails_when_partial_hangs` |
| minor | 取消、丟棄或 final 失敗的段落，語言快取沒清，晚到的 partial 還會寫回 | 新增 `_end_utts`：清掉快取並記入 `ended_utts`，不推進 `max_final_utt`。segmenter 取消、積欠合併時丟掉的段落、final 失敗都會走這裡 | `test_cancelled_or_failed_segments_do_not_keep_language_cache` |
| minor | 新的相似度算法會讓「的」這種極短輸出拿滿分 | 至少要對上 `MIN_MATCHED_CHARS`（6）個字 | `test_similarity_tolerates_truncated_bench_audio`（加入「的」「大家好」） |
| 06-5 部分 | 狀態沒有記錄實際使用的 partial 模型 | `asr_model` 顯示為「small＋即時 base」 | `test_status_names_partial_model_when_separate` |

**複審中標為「部分」、這次沒有處理的項目**：

- **lite 的退路**：intel-gpu-lite 超過預算時仍會退到 CPU，沒有先比較兩者的實測速度。lite 的預算已放寬到 1.4 秒，實際上很少會發生。
- **受控順序測試**：沒有補上用 Event 控制完成順序的競態測試（最新 slot 替換、停止、合併），目前的並行測試是時間驅動的。

## 驗證

- 一般測試 272 passed，slow 4 passed。
- 真實校正（Core Ultra 7 155H + Arc）：
  - intel-gpu：定稿 0.76 秒（上限 1.2），即時 0.29 秒（上限 0.6），相似度 0.92／0.94。
  - intel-gpu-lite：定稿 0.27 秒。
- 中英混合音檔：即時 p50 0.38 秒，定稿 p50 1.25 秒，6 句全對。
- 麥克風實測（修正前的版本）：即時 p50 0.34 秒，定稿 p50 1.25 秒。

## 已知限制

| 項目 | 說明 |
|---|---|
| NVIDIA 等級 | 仍共用引擎（單一 worker、final 優先），還沒在實機上驗證。使用者預計之後在 RTX 3060 上測，屆時再評估是否也改用獨立的 partial 引擎 |
| small 的同音錯字 | 麥克風實測中有「茶敘→查訓」之類的錯誤；NVIDIA 等級的 Breeze 模型準確度較高 |
| 「即時」的計算起點 | 從 partial 請求送出開始算，不含開口到第一個 partial 之間約 0.4 秒的音訊累積 |
