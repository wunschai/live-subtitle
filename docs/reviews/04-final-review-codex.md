# 最終審查（第 2 輪修正）

## 結論（需修正）

`_Session` 已隔離字幕板、佇列與推論引擎，`cancel_partial` 不再封鎖較舊段落；overlay 的 UTF-16 索引、空白／dim span，以及最新一句過長時保留尾端的邏輯，依目前 `CaptionBoard` 產生 span 的方式，沒有發現具體錯誤。

但停止與重啟仍有生命週期漏洞，重啟重試還會形成事件回授。以下問題應先修正。

本次僅依內嵌程式碼靜態審查，未執行測試；03 表中未提供實作的其他項目，無法確認。

## 發現

1. **major｜`ui/app.py:99–114`、`core/pipeline.py:210–215`：重啟重試形成無延遲的事件回授**

   **問題：** `_try_restart()` 呼叫 `start()`；若 `_lingering` 尚未結束，`start()` 強制送出 IDLE／FAILED 狀態。該 queued signal 再進入 `_on_status()`，立即呼叫 `_try_restart()`，同時每次又新增一個 500 ms timer。

   **具體情境：** 換麥克風觸發停止，但舊推論超過停止期限。原本預期每半秒重試，實際會持續處理狀態事件並累積 timer，造成 GUI 高負載、操作延遲。

   **建議修法：** 使用單一、可取消的重啟 `QTimer`；狀態事件只負責在 timer 尚未啟動時排程，不直接重試。成功、使用者停止及 shutdown 時取消 timer。

2. **major｜`core/pipeline.py:261–284、300–317`：loader 逾時後仍可能開啟音訊來源，卻無人關閉**

   **問題：** loader 的停止檢查在 `_source_factory()` 之前。stopper 逾時清理完後，loader 仍可能才設定 `s.source` 並執行 `source.start()`；接著第 317 行直接返回，錯誤假設 stopper 還會負責關閉來源。

   **具體情境：** `_source_factory()` 卡住超過 15 秒。stopper 當時看到 `s.source is None`，記錄 lingering 並結束。factory 恢復後開啟麥克風，loader 隨即退出，但沒有呼叫 `source.stop()`。下一次啟動時 lingering 已清空，舊麥克風串流卻可能仍存在。

   **建議修法：** 明確定義來源的清理責任。loader 在尚未將來源交付執行階段前，若取消或失敗，必須自行於 `finally` 清理；交付與停止判斷需同步，避免漏關或並行關閉。

3. **major｜`core/pipeline.py:305–313`：啟動失敗直接發布 FAILED，繞過 lingering 追蹤**

   **問題：** 啟動例外路徑先設為 FAILED，之後才呼叫 `source.stop()`，沒有經過 `_end_session()`／`_join()`。`start()` 只檢查 `_lingering`，不檢查這條仍活著的 loader。

   **具體情境：** `source.start()` 失敗，而後續 `source.stop()` 清理緩慢或卡住。介面已允許使用者重新開始，新 session 會與尚未完成清理的舊 loader 同時操作音訊裝置。

   **建議修法：** 啟動失敗也走統一停止流程：清理期間保持 STOPPING，結束或逾時登記 lingering 後再發布 FAILED。加入「啟動失敗且清理阻塞」的重啟測試。

4. **major｜`ui/hotkey.py:36–46、53–64、78–88`：註冊逾時可能遺失仍存活的熱鍵執行緒**

   **問題：** `unregister()` 一開始就清除 `_thread`。若 worker 尚未設定 `_tid`，完全不送退出訊息，也不等待；即使有 tid，送出訊息與 join 逾時後也一律清除追蹤。worker 沒有獨立的取消旗標，稍後仍可完成註冊並進入訊息迴圈。

   **具體情境：** `register()` 等待逾時時 worker 尚未執行到第 53 行；清理返回後 worker 才開始註冊。管理器已遺失執行緒參照，後續 `unregister()` 無法停止它，重新註冊還可能與它衝突。

   **建議修法：** 每次註冊使用獨立的停止事件與就緒事件；取消後，worker 即使晚啟動也必須自行撤銷。確認執行緒退出前保留 `_thread`，檢查退出訊息送出結果，並在 worker 的 `finally` 同步清理狀態。

5. **major｜`core/pipeline.py:249–259`、`ui/app.py:163–171`：已在 STOPPING 時，shutdown 的 `wait=True` 無效**

   **問題：** `_end_session()` 在 STOPPING 狀態直接返回，沒有等待已存在的 stopper。因此 `_shutdown()` 的 `stop(wait=True)` 不保證等待目前的清理工作。

   **具體情境：** 使用者先按停止，接著立即結束程式。shutdown 直接通過，daemon stopper 可能還沒執行到 `source.stop()`，程式便退出。

   **建議修法：** 保存 stopper 或 session 完成事件；`wait=True` 即使遇到 STOPPING，也應在鎖外等待既有清理工作。shutdown 同時清除 pending restart 並停止重啟 timer。

6. **major｜`core/pipeline.py:353–363、505–518`：CUDA 翻譯推論失敗仍不會退回 CPU**

   **問題：** CPU fallback 只包住翻譯器建構。`_mt_loop()` 中的實際 `translate()` 失敗只會回傳 `None`，之後仍繼續使用同一個翻譯器。03 所稱「執行期翻譯在 CUDA 失敗時會自動退回 CPU」尚未落實。

   **具體情境：** CUDA 翻譯器建構成功，但第一次或較長文字推論時發生顯存不足。之後每句翻譯都可能持續失敗，無 CPU 重試。

   **建議修法：** 在 MT worker 中處理符合降級條件的裝置／推論錯誤，切換一次 CPU int8 翻譯器並重試當筆；加入「建構成功、推論失敗」的回歸測試。

7. **major｜`core/pipeline.py:444–456`、`core/captions.py:96–115`：partial 失敗被當成整段終止，且計入 final 失敗門檻**

   **問題：** 所有推論例外都增加 `s.asr_errors` 並送 `_Cancel`。但 `_Cancel` 會永久拒收該 utt 後續 partial；此外，目前並非 03 宣稱的「final 連續失敗三次」。

   **具體情境：** 同一句第一次 partial 暫時失敗，後續成功的 partial 全被拒收，只能等 final。若同一句有三次 partial 失敗，即使 final 路徑原本可用，也會提前讓整個 pipeline 失敗。

   **建議修法：** 將 final 的連續失敗計數與 partial 錯誤分開。partial 暫時失敗若需要清除畫面，使用不封鎖 utt 的清除操作；只有確定該段終止、final 失敗或被丟棄時才送永久 cancel。