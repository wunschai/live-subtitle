# 最終審查回應

日期：2026-10-30。

審查紀錄：[04-final-review-codex.md](04-final-review-codex.md)（GPT/codex，範圍為 `de01ed6..c634645`）。

GPT 對 overlay 的整句截斷（UTF-16 索引、空白與 dim span、最新一句過長）沒有提出問題。另外提出 7 項 major，全部修正並補上回歸測試，如下表。

## 處理結果

| # | 問題 | 處理 | 回歸測試 |
|---|---|---|---|
| 1 | 重啟重試與狀態事件互相觸發，形成無延遲的回授 | `LiveSubApp` 改用單一、可取消的 `_restart_timer`。狀態事件只在 timer 未啟動時重試；使用者停止與 shutdown 時取消 | `test_ui_app.py::test_restart_retry_is_paced_not_a_feedback_loop` |
| 2 | loader 在停止逾時後才開好音訊來源，之後無人關閉 | 音訊來源在交付給 session 前都歸 loader 管。交付與 stop 旗標在同一把鎖內判斷；沒交付就由 loader 在 `finally` 關閉 | `test_loader_closes_source_opened_after_stop_timeout` |
| 3 | 啟動失敗直接發布 FAILED，繞過 lingering 追蹤 | 啟動失敗改走 `_fail` → `_end_session`。清理期間顯示 STOPPING；loader 逾時就列入 lingering | `test_start_failure_goes_through_stopping_and_closes_source` |
| 4 | 熱鍵註冊逾時會遺失仍在執行的 worker | 每次註冊都有自己的 `_Worker`，含取消與就緒旗標。訊息佇列建立後才公布 tid。worker 晚啟動時，看到取消旗標會自行撤銷；執行緒退出前保留參照 | `test_ui_hotkey.py`（2 項，Windows 實機） |
| 5 | 已在 STOPPING 時，`stop(wait=True)` 沒有等待 | 保存目前的 stopper；`wait=True` 會 join 既有的清理工作。stopper 在鎖內啟動 | `test_stop_wait_while_already_stopping_waits_for_cleanup` |
| 6 | CUDA 翻譯推論失敗（非建構失敗）不會退回 CPU | `_mt_loop` 推論失敗時，改建 CPU int8 翻譯器並重試該句。只切換一次，並在面板顯示提示 | `test_translation_inference_failure_switches_to_cpu` |
| 7 | partial 失敗會封鎖該段落，並計入 final 的失敗門檻 | partial 失敗只記入 log 與統計，不送 cancel，也不計入 `MAX_ASR_ERRORS`。只有 final 失敗才計數與 cancel | `test_partial_failures_do_not_block_segment_or_fail_pipeline` |

## 驗證

- 一般測試 246 passed，slow 4 passed（真模型）。
- 用中英混合音檔實跑（intel-gpu，turbo int8）：每句都辨識正確，延遲約 2.0 秒。
