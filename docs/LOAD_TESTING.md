# 活動前壓力測試

以約 100 人、最多 200 人的活動為目標。需要同時確認反應速度、報到／抽獎紀錄正確，以及現場手機與網路。以下為建議驗收程序，**尚未完成正式 VPS 的持續壓測或現場驗收**。

## 現在可以直接執行的測試

在專案根目錄執行，需先依 README 安裝開發依賴：

```sh
.venv/bin/python -m scripts.server_smoke
.venv/bin/python -m scripts.bingo_server_smoke
.venv/bin/python -m scripts.raffle_server_smoke
```

這三個程式各自建立暫時 SQLite、虛構來賓及本機隨機埠的 Gunicorn，結束後停止自己的測試程序，不使用現行示範或正式資料庫。輸出摘要在 `test-results/server-results.json`、`bingo-server-results.json`、`raffle-server-results.json`。

| 程式 | 已涵蓋的情境 | 範圍限制 |
| --- | --- | --- |
| `server_smoke` | 10 個 HTTP client 同票報到，只保存一次；真實程序重啟 | 短時間正確性驗證 |
| `bingo_server_smoke` | 200 人領卡、24 個並行 client 任務、10 個同回合抽號、狀態同步及重啟 | 單輪同步的延遲統計；不是 200 人持續在線 |
| `raffle_server_smoke` | 200 位得主、10 個同時操作、獎品不超發、重抽及重啟 | 短時間正確性驗證 |

既有通過紀錄見 [TEST_RESULTS.md](TEST_RESULTS.md)。上述 smoke 程式測本機暫存服務；要測已部署的 VPS，使用下一節新增的 `vps_load_test`，網址由操作者明確指定。

## 已部署 VPS：可直接操作的指令

在自己的 Mac 專案根目錄產生負載，VPS 保持原部署運行。壓測工具使用 Python 標準函式庫及 k6，服務的 requirements 與容器不需新增套件。Mac 若尚未安裝 k6：

```sh
brew install k6
k6 version
```

安裝方式見 [k6 官方說明](https://grafana.com/docs/k6/latest/set-up/install-k6/)。若使用獨立下載的執行檔，以下指令另加 `--k6-bin /完整路徑/k6`。不必在 VPS 上安裝 k6。

### 先測不改變活動紀錄的公開 API

將下例網址替換成自己的 HTTPS **測試站**：

```sh
.venv/bin/python -m scripts.vps_load_test \
  --url https://test-checkin.example.org \
  --mode public --users 10 --duration 30s

.venv/bin/python -m scripts.vps_load_test \
  --url https://test-checkin.example.org \
  --mode public --users 120 --duration 60s
```

`public` 模式將虛擬使用者分配到賓果／抽獎的公開狀態 API，每人每兩秒查詢一次。120 人約為每秒 60 次查詢；實際速率以報告為準。此模式是公開 API 負載基準，沒有驗證個人票券、報到、領卡或手機渲染，不能用其結果宣稱完整 120 人活動已驗收。

### 再測 120 張獨立票券與賓果卡

1. 在測試站匯入 `examples/attendees-120.csv`。120 位假來賓採 `TEST120-001`～`TEST120-120`；可和原 20 位示範來賓共存。
2. 到管理總覽匯出「票券連結」CSV，存成這台電腦的 `examples/tickets.csv`。這份是帶 `ticket_url` 的匯出清單，原本只有姓名／桌次的名冊 CSV 不適用。此路徑已加入 `.gitignore`；亦可存於已忽略的 `exports/`。
3. 在「賓果主持台」按「開放領卡」。第一次壓測先保持同一局；測試期間若切換新局、撤回報到或補發票券，工具會因身分／局次變更停止。
4. 建立或使用自己的測試工作人員帳號。在本機終端機執行：

```sh
# 先確認 10 位正常；只有尚未報到者需要輸入帳號與密碼。
.venv/bin/python -m scripts.vps_load_test \
  --url https://test-checkin.example.org \
  --mode bingo --tickets examples/tickets.csv \
  --users 10 --duration 30s --check-in

# 120 位持續同步，每 10 次同步（約 20 秒）切換一次格子記號。
.venv/bin/python -m scripts.vps_load_test \
  --url https://test-checkin.example.org \
  --mode bingo --tickets examples/tickets.csv \
  --users 120 --duration 5m --check-in --mark-every 10

# 前一輪通過後延長至 30 分鐘。
.venv/bin/python -m scripts.vps_load_test \
  --url https://test-checkin.example.org \
  --mode bingo --tickets examples/tickets.csv \
  --users 120 --duration 30m --mark-every 10
```

`--check-in` 會在預檢後透過正常登入、CSRF 與工作人員 POST，協助所選 TEST 假來賓報到。帳號可互動輸入，或另加 `--staff-user 測試工作人員帳號`；密碼一律遮罩輸入，不放命令列或報告。所有所選來賓的編號及姓名會先由工作人員查票核對，再開始報到。再次執行時保留原報到、原卡；已全數報到時不用再次登入。

自動報到是壓測前的預備動作，延遲不計入持續輪詢的效能統計，不能當作「4 位工作人員同時報到」的驗收。持續壓測涵蓋每人獨立 cookie／CSRF、本人領卡、同卡身分驗證、每兩秒同步及可選的手動記號。主持人可從正常介面抽號、抽獎與查看投影，同時觀察這些操作的體感；自動化工具不替主持人執行抽選。

120 人每兩秒同步約為每秒 60 次查詢，200 人約每秒 100 次；領卡及勾選另計。`--users` 最大 200，但票券清單至少要有同樣多的 TEST 假來賓。工具只選取 `TEST-xxx`、`TEST120-xxx`、`TEST200-xxx` 等測試編號，略過其他來賓，並拒絕票券來源不符、重複憑證、非 HTTPS 網址及錯誤身分。未提供票券時不會用匿名狀態冒充已領卡玩家。

### 查看結果與主機負載

每輪摘要寫到本機 `test-results/vps-load-日期時間-隨機值/summary.json`，終端機同時顯示結果。摘要包含目標、模式、人數、實際時長、HTTP 速率、p95／p99、429／5xx／網路錯誤、成功領卡人數及保存勾選次數；沒有票券、姓名、cookie 或密碼。查看：

```sh
.venv/bin/python -m json.tool test-results/vps-load-本次資料夾/summary.json
```

`passed: true` 需達到設定時長、必要玩家全部成功領卡、回應資料驗證及延遲／錯誤門檻全部通過。錯誤率超標時會提早停止；被中斷、沒有樣本或只是回傳 HTTP 200 的錯誤資料都不會算通過。初步門檻為狀態查詢 p95 < 1 秒、p99 < 2 秒、非預期 HTTP 與資料錯誤為 0，並非目前 VPS 的效能保證。

在 VPS 另一個 SSH 終端機觀察：

```sh
docker stats
docker compose logs --tail 100 app
df -h
```

核對 CPU、記憶體、磁碟及資料庫鎖定／逾時；報告的請求速率也要接近預期。若回應變慢導致實際流量下降，不能僅憑錯誤率為 0 就宣稱達到目標流量。測試前後另用後台核對人數、卡片及得獎紀錄，並完成實體手機與投影彩排。

票券只在必要的 HTTPS POST 本文與私人暫存檔中使用；暫存檔權限 600，退出時刪除。工具不採用原有 k6 的 HTTP-debug、雲端輸出或跳過憑證設定，請勿自行開啟包含 HTTP 本文的除錯。快速重跑可能遇到每分鐘票券查詢／領卡額度，應等待限流窗結束後核對重試。

開發者可在本機獨立資料庫驗證此工具，會啟動自己的 Gunicorn，**不連線 VPS**。本次實際通過環境為 Python 3.14.7／Gunicorn 26.2.0；舊版 Python 3.9.6／Gunicorn 23 的本機 worker 啟動異常另記於 [TEST_RESULTS.md](TEST_RESULTS.md)，不要將沒有完成的測試視為通過：

```sh
.venv/bin/python -m pytest -q tests/test_vps_load_test.py
.venv/bin/python -m scripts.vps_load_smoke --k6-bin /完整路徑/k6
```

`--allow-local-http` 只供這類 localhost／loopback 回歸；VPS 持續使用正常 HTTPS 憑證驗證。

## 部署測試環境

1. 準備獨立測試名冊、200 位虛構來賓、工作人員帳號、資料庫及資料卷，不向正式名冊報到或抽獎。
2. 在預定的 Oracle ARM VPS、磁碟及正式部署架構上測試：HTTPS → 反向代理 → Gunicorn → SQLite。沿用目前 `gunicorn.conf.py` 的 2 workers × 4 threads 作為第一輪基準，勿以 Flask 開發伺服器的數字驗收正式容量。
3. 使用另一台電腦產生負載，經測試網域連線；同時記錄壓測電腦及 VPS 的 CPU／記憶體，避免產生流量的電腦先成為瓶頸。
4. 使用獨立部署時依 [DEPLOYMENT.md](DEPLOYMENT.md) 規劃網域、代理入口與資料卷。原 Compose 的專案名與 80／443 不能直接重複使用來建立第二套服務。

測試程式必須使用正常登入／CSRF 流程。每位虛擬來賓有自己的 cookie、票券及賓果卡；每位工作人員只在情境開始時登入，再重用該登入。不要在每次同步時重新登入，也不要關閉 CSRF 或全部限流來換取漂亮數字。

## 建議負載情境

持續負載可用 k6 編寫情境，依序從少量連線、正常負載、尖峰到長時間運行；測試形狀的用途見 [k6 官方說明](https://grafana.com/docs/k6/latest/testing-guides/test-types/)。下表是本專案建議的測試量，不是已測得的容量。

| 情境 | 建議流量與時間 | 同時驗證 |
| --- | --- | --- |
| 基準 | 10 位來賓，5 分鐘 | 腳本確實讀到自己的資料；沒有把 403 錯誤頁當成功 |
| 進場報到 | 4 位工作人員，200 位不同來賓在 10 分鐘內進場；另做短時間集中查票／報到 | 姓名搜尋、查票、確認報到與票券更新；最後只有 200 筆有效報到 |
| 賓果正常使用 | 先 50 人、再 100 人，每個階段 10 分鐘 | 每人每兩秒讀一次自己的遊戲狀態，混入勾選及主持抽號 |
| 賓果滿載 | 200 人，每兩秒同步一次，持續 30 分鐘 | 約每秒 100 次狀態查詢，另加主持／投影／勾選；包含等待、抽號及結束後同步 |
| 短時間尖峰 | 200 人在 10～30 秒內集中開票；再做集中領卡、刷新及重連 | 記錄排隊、超時、限流；恢復後原卡與報到數不變 |
| 抽獎混合流量 | 200 人資料下，主持台及 1～2 台投影持續在線並抽獎、領獎 | 與仍開著的賓果頁同時存在；獎品數、得主及重抽關聯正確 |
| 長時間運行 | 預期正常人數維持 2 小時，或至少涵蓋預計遊戲時間 | 記憶體、延遲及 WAL／磁碟用量沒有持續惡化 |

「200 人」是名冊及玩家數；「每秒 100 次」是 200 個前景賓果頁各每兩秒同步一次的估算，未包含其他操作。單一伺服器開著 200 筆資料，和真的有 200 個持續請求的 client，是不同驗證。

完整網路請求要包含正常 cookie、有效票券和伺服器發給該 client 的 CSRF，並確認回傳欄位與預期狀態。只反覆打 `/healthz`，或只開首頁，無法代表報到、QR 產生或遊戲負載。HTTP 負載測試也不會驗證攝影機、JavaScript 動畫或手機渲染；這些另以瀏覽器測試及實機彩排確認。

## 本專案需要特別觀察的項目

- **共用 Wi-Fi 的限流**：公開票券查詢與賓果領卡／恢復原卡各有每 IP 每分鐘 300 次的額度。200 人各開一次票、再重整一次，若落在同一計數時間窗，可能超過票券額度。需使用相同出口 IP 測集中開票與重整，單獨統計 429，依結果調整策略後重測；不能將此誤判為 CPU 不足。完整限制見 README。
- **輪詢與開獎同時存在**：持續讀取狀態時，插入真正的報到、勾選、抽號、領獎、匯出與一致性備份操作，確認查詢不會拖垮寫入。不要用重複登入取代真實讀寫比例。
- **資料庫等待**：觀察 `database is locked`、SQLITE_BUSY、503 與逾時。SQLite WAL 可同時讀寫，但一次仍只有一個寫入者，不能假設增加 worker 就能解決寫入競爭。[SQLite WAL 說明](https://www.sqlite.org/wal.html)
- **業務結果**：重複報到不能增加人數；同一操作重試不能多抽；重抽保留原紀錄；獎品不能超發。預期的競爭回應（例如過期回合的 409）與意外 5xx、逾時分開統計。
- **資料保護**：報告只記錄路徑、狀態碼、延遲與數量；不要開啟包含票券、cookie、密碼或 POST 本文的除錯紀錄。

## 建議通過標準

以下為活動體感訂出的初步目標，需在目標主機實測，不能當作目前的效能保證。

| 指標 | 初步目標 |
| --- | --- |
| 查票／搜尋／確認報到的 API 延遲 | p95 < 1 秒，p99 < 2 秒；按操作分開統計 |
| 持續遊戲狀態 API 延遲 | p95 < 1 秒，沒有越跑越慢 |
| 正常網路下投影同步 | 以兩秒輪詢為基礎，約 3～5 秒內看到更新；另外核對設計中的揭曉動畫 |
| 正常流量的非預期 5xx／逾時／驗證失敗 | 0；每個 429 都需確認是否會阻擋實際活動操作 |
| 報到及獎品結果 | 100% 正確，無重複計數或超發 |
| 資源與恢復 | CPU 不長時間滿載、記憶體不持續成長、磁碟充足；解除尖峰後延遲回復 |

p95 < 1 秒表示 95% 的該類請求在一秒內完成。需同時看 p99、最大值與逾時筆數，不能只看平均值或「總請求成功率」。API 延遲也不包含人員核對或鏡頭辨識耗時。

每次保存程式版本、VPS 規格、worker／thread 設定、情境、人數、實際請求速率、測試時長、各 API 延遲／錯誤、資源曲線與測試後的資料總數。若未達標，修改一項設定後再跑相同情境比較。

## 現場彩排與開場前

找 5～10 支不同手機，以現場 Wi-Fi 及行動網路各跑一次：開票、實際鏡頭掃碼、確認報到、重複票、領卡、勾選、投影與領獎；再切換網路並安全重試。英語／繁中、iPhone Safari／Android Chrome 都要有人操作。

這類小規模彩排不能證明場館 Wi-Fi 可容納 200 台設備。如果預期大家共用 Wi-Fi，另向場館確認 AP 與對外頻寬容量。保留行動網路備援、充電及紙本核對流程，完整項目見 [ACCEPTANCE.md](ACCEPTANCE.md)。

正式活動前完成一致性備份，確認磁碟與健康狀態；停止壓測流量，正式系統只匯入正式名冊。測試人員、票券、報到及抽獎紀錄留在測試資料庫，不直接搬入正式活動。
