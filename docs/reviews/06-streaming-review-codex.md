# 審查：邊講邊出字（串流 partial）

## 結論（需修正）

共用引擎的互斥有做到，但「final 優先」沒有成立，且等待鎖的 partial 會變成無法被替換的舊請求。這會直接影響 NVIDIA 的延遲與停止時間。setup 也尚未驗證實際的雙模型執行方式。

以下依內嵌程式碼靜態審查，未執行測試。未提供 `captions.py`，因此無法確認 board 對晚到 partial 的拒收是否完整。

## 發現

### 1. major — 共用引擎未真正保證 final 優先

**位置：** `src/livesub/core/pipeline.py:485–527`

**問題：** final 從佇列取出後，尚未取得 `asr_lock` 就通知 partial。partial 只檢查 `final_q`，看不到已取出、正在等待執行的 final；一般 `Lock` 也沒有優先權保證。

**具體情境：** final worker 取出唯一的 final，佇列變空；partial worker 被喚醒，取出下一句的 partial，先取得引擎鎖。final 因而多等一次 partial 推論。已有 partial 正在推論時，等待中的 final 同樣不能搶占它。

NVIDIA 全部共用引擎，降低間隔至 0.3 秒不會降低單次推論時間。自適應的 `1.3 * partial_cost` 只限制請求產生頻率，不能保證 final 優先，也不能避免 partial 等待長時間 final 推論。

**建議修法：** 共用模式保留單一 ASR 排程 worker，在每次推論開始前優先取 final；只有獨立引擎模式使用兩條 worker。已開始的 partial 無法搶占，應另以實測決定模型與間隔。

### 2. major — partial 提早離開最新槽，等待鎖期間失去更新與取消能力

**位置：** `src/livesub/core/pipeline.py:454–455、513–527`

**問題：** partial 在取得引擎前就從 `partial_slot` 移到區域變數。後續新 partial 只能替換槽內資料，final 也只能清掉槽，無法淘汰這個已取出的請求。

**具體情境：** final 推論期間，partial worker 取出下一句早期音訊並等待鎖；期間已有更新音訊，甚至該句已送出 final。鎖釋放後仍會先辨識舊音訊。即使 board 最終拒收結果，也已浪費推論時間並可能延後有效字幕。

**建議修法：** 共用模式在引擎可用時才取最新 partial，並在同一排程決策中檢查 final 優先權、句子是否已結束。使用可控的假引擎測試「等待期間更新／定稿」時舊 partial 不會執行。

### 3. major — 停止後，等待鎖的 worker 仍可能開始另一輪推論

**位置：** `src/livesub/core/pipeline.py:511–527、277–304`

**問題：** stop 檢查在取得引擎鎖之前；取得鎖後直接 `transcribe()`，沒有再次檢查。

**具體情境：** final 持有鎖、partial 等待鎖時按停止。final 返回後，partial 仍開始完整推論，延長停止時間；如果超過 join 期限，就會留下 lingering worker，阻擋重新啟動。

`wake()` 的 `notify_all()` 能喚醒 condition 等待者，不能取消 `asr_lock` 等待者。

**建議修法：** 在取得引擎執行權後、呼叫推論前重查 stop，以正常取消路徑退出，不計入辨識錯誤。加入「一個推論被阻擋、另一個等待鎖、此時停止」的測試，確認釋放第一個後不會開始第二個。

### 4. major — setup 下載了 partial 模型，卻沒有校正雙模型工作負載

**位置：** `src/livesub/setup_cli.py:67–76、95–115、147–161`

**問題：** `ensure_models()` 已涵蓋 partial 模型下載與驗證，但 `probe()` 只建立 final 引擎，校正結果完全不包含獨立 partial 引擎。

**具體情境：** small 單獨推論通過預算，但 base 無法載入，或兩者同時推論時產生裝置競爭；setup 仍判定 `intel-gpu` 通過。執行期才退回共用引擎或出現超標延遲。這正是背景實測已指出會影響延遲的因素。

**建議修法：** 有 `partial_model` 時，校正應同時載入兩個引擎，測量重疊推論下的 final 與 partial 延遲，分別記錄是否可用及是否達標。測試應涵蓋 partial 載入失敗，以及單獨通過、並行超標的情況。

### 5. minor — partial 降級沒有反映在使用者狀態與有效 profile

**位置：** `src/livesub/core/pipeline.py:379–396、347–348、693–704`

**問題：** partial 載入失敗只寫 log，仍回傳宣告有 `partial_model` 的 profile，沒有把共用引擎的降級訊息帶到 UI。

**具體情境：** base 缺檔或暖機失敗，使用者仍看到原本的 Intel 等級，無從得知即時辨識已改成與 small 定稿排隊。

**建議修法：** 回傳 partial 初始化結果與提示訊息，將「即時辨識改用共用引擎」納入 status，並記錄實際 partial 模型／模式。

目前 `_build_partial_asr(p)` 使用的是**降級後的 `p`**，這點正確；降到 `intel-gpu-lite` 不會誤用原 profile 的獨立 partial 模型。

### 6. minor — 新統計不能直接代表「說話到出字」或整次工作階段 p50

**位置：** `src/livesub/core/pipeline.py:105–107、288–291、443–447、638–642`

**問題：**

- partial 計時從 `_submit()` 開始，不含等候首次 partial、音訊收集與前處理時間。
- 只有非空且 board 接受的結果被採樣，未涵蓋遭淘汰結果所造成的字幕停滯。
- deque 僅保留最近 20 筆，但停止時標示為「本次統計」。

**具體情境：** 首次 partial 必須先累積音訊，再花 0.34 秒處理；顯示的「即時 0.3 秒」不是首次開口到出字的延遲。長工作階段前段曾嚴重卡頓，最後 20 筆恢復正常，停止日誌也不會反映前段情況。

**建議修法：** 將現有指標明確命名為「最近 20 筆已接受 partial 的請求處理延遲」。若要驗證 0.5–1 秒目標，另量首次出字時間、字幕更新間隔，並以音訊時間戳追蹤新內容的延遲；整場 p50 另行累積。

### 7. major — 提供的測試變更未涵蓋此次核心並行契約

**位置：** `tests/test_ui_panel.py:224 附近`；`src/livesub/core/pipeline.py:478–556、627–653`

**問題：** 此次提供的測試 diff 只有 UI 字串，沒有驗證新增的雙 worker 行為。單次效能實測無法覆蓋指定的競態順序。

**具體情境：** 同句 final 先完成、partial 後完成時，partial 仍會送進 coordinator；final 清掉 `utt_lang` 後，晚到 partial 也會重新插入該句語言。`ctx_lock` 保護字典操作，但沒有保證句子結束後不再寫回。

**建議修法：** 使用 `Event`／barrier 控制假引擎完成順序，至少驗證：

- 共用引擎不重入、final 優先、只執行最新 partial。
- 獨立引擎確實可以重疊執行。
- final、空 final、cancel、合併取消後，晚到 partial 不會重建字幕。
- 已結束句子的語言快取不被晚到 partial 復活。
- partial 初始化失敗與 profile 降級後，引擎選擇正確。
- 停止時兩條 worker 均能退出，不再開始排隊推論。

未看到 board 實作，不能直接判定「字幕會復活」；但這是此次並行化必須有測試證明的契約。