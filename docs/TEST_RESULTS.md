# 本機驗證結果

驗證日期：2026-10-19（Asia/Taipei）。測試均使用隔離的暫時 SQLite；示範資料庫另外建立，不混入正式資料。

## 自動化後端

`python -m pytest -q`：**66 項通過，0 項失敗**。

- macOS ARM64／Python 3.9.6／Flask 3.1.3／Pillow 11.3.0：通過，最近一次耗時 24.11 秒。
- macOS ARM64／Python 3.14.7／Flask 3.1.3／Pillow 13.0.0：通過，最近一次耗時 9.98 秒。
- 依賴一致性 `pip check`：兩個環境均無衝突。

| 驗證範圍 | 結果 |
| --- | --- |
| CSV 同編號重匯更新、不重複新增、保留票券／報到 | 通過 |
| UTF-8 BOM、缺欄、重複編號、錯誤編碼、格式錯誤 | 通過；不部分寫入 |
| 預覽使用者綁定、過時名冊／重複確認拒絕 | 通過 |
| 200 人上限，第 201 人被拒絕 | 通過 |
| 未登入／只有票券者無法報到，GET 無法報到 | 通過 |
| 無效／已補發舊票無法查詢或報到 | 通過 |
| 重複報到保留原時間、原操作者 | 通過 |
| 10 個獨立連線並行報到，只有一筆首次寫入 | 通過 |
| 同一操作安全重試，撤回後不因原操作重試而復活 | 通過 |
| 工作人員不能管理、補發、撤回、匯出或操作帳號 | 通過 |
| 參加者只有憑證對應票券，不能用編號查他票／名冊 | 通過 |
| QR PNG 以獨立 ZXing 解碼器解出正確片段網址 | 通過 |
| CSRF、停用／重設後撤銷登入、登入與票券限流 | 通過 |
| Secure／HttpOnly／SameSite、no-store、noindex、CSP | 通過 |
| 正式 HTTPS／金鑰要求及網域驗證 | 通過 |
| 管理確認、稽核、完整歷史、各桌統計、CSV 公式處理 | 通過 |
| 應用程式重建後名冊、票券、報到仍在 | 通過 |
| 線上 Backup API、integrity_check、停機還原 | 通過 |
| DB／備份／環境檔沒有公開下載路由 | 通過 |

## 真實 HTTP 與程序重啟

`python -m scripts.server_smoke` 使用 Gunicorn 26.2.0，2 workers × 4 threads，本機隨機埠與暫時 DB。

- 10 個分別登入的 HTTP client 同時 POST 同票：1 個 201，其餘 200 已報到，資料庫只有 1 筆。
- 實際終止與重新啟動 Gunicorn，原憑證仍有效、原時間及狀態保留。
- 檢查實際 Gunicorn 存取與錯誤紀錄，未包含完整 token。
- 結果：全部通過。可機讀結果見 `test-results/server-results.json`。

## 手機尺寸瀏覽器流程

`python -m scripts.browser_smoke` 啟動真實 HTTP server，使用 Chromium 148、WebKit 26.4；分別套用 Pixel 7 與 iPhone 13 的尺寸／行動裝置設定。

兩引擎均通過：

- 登入、搜尋、核對後人工確認，核對階段尚未報到。
- 重複票顯示原時間與操作者，確認按鈕隱藏。
- 相機拒絕授權的中文提示與人工備援：於掃碼 API 邊界注入權限拒絕，並確認模擬確實被呼叫；未驗證實機授權對話框。
- 請求送出前中斷：沒有成功畫面，復線重試成功。
- 伺服器寫入後回應遺失：維持「尚未確認」，重試顯示已報到，仍只一筆。
- 匿名票券、失效票提示、token 未出現在 HTTP URL、手機頁面無整體橫向溢出。
- 管理總覽／個人票券／確認畫面截圖檢視，無 JavaScript 頁面錯誤。

另以 Chromium 虛擬攝影機讀入合成 QR 影片，停用原生 `BarcodeDetector`，**成功由隨專案提供的軟體解碼器辨識**，核對後 POST 才完成報到。未使用實體鏡頭。

截圖與 JSON 位於 `test-results/`，只含暫時測試資料。主要檔案：`webkit-ticket.png`、`webkit-admin.png`、`chromium-camera-decoder.png`、`*-network-unconfirmed.png`、`browser-results.json`。

## 示範環境

已建立 `instance/demo.sqlite3`，20 位虛構來賓，2 個隨機密碼帳號。實際驗證管理員登入、個人管理頁、QR PNG 下載／解碼、20 人票券連結匯出及匿名讀取票券，均通過。讀取與下載後出席紀錄仍為 0，保留乾淨的報到展示起點。

本機示範服務監聽 `127.0.0.1:5001`。若程序已停止，以 `sh scripts/run_demo.sh` 重新啟動；帳密請讀私人 `instance/demo-credentials.txt`。

## 2026-10-20：電腦 Safari 本機相機與內網票券

內網模式已可設定 `DEMO_HOST=0.0.0.0` 和 `DEMO_PUBLIC_BASE_URL`。實際 WebKit 比對：內網 HTTP 沒有安全環境／相機 API，`http://localhost:5001` 有；電腦從 localhost 操作、手機從內網出示票券可共用同一名冊。

`python -m scripts.local_camera_smoke` 在修正前重現「localhost 報到頁拒絕本活動的內網票券網址」，修正後 WebKit 與 Chromium 均通過：相機 API 可用、接受系統設定的票券來源、拒絕其他網站來源、讀票不會自動報到。使用暫時 DB，未開啟實體鏡頭；結果保存在 `test-results/local-camera-results.json`。後端 66 項測試再次全數通過（Python 3.14.7，9.51 秒）。

修正僅接受目前來源與伺服器 `PUBLIC_BASE_URL` 的來源，票券 token、登入、權限、CSRF 與確認報到流程仍由原本伺服器檢查。實體 Safari 相機授權與鏡頭掃描仍由使用者在本機驗收。

## 部署範本與尚未完成的驗收

Compose YAML 已解析並檢查 ARM64、應用程式不公開 port、持久化 volume 與唯讀根檔案系統設定。另以獨立 Compose 5.6.0 執行 `config --quiet` 通過；以 Caddy 2.11.7 執行 `adapt --validate` 通過，未啟動公開代理、未申請憑證。這些是本機設定驗證，未啟動 Docker Engine 或容器。

**尚未實際執行**：

- iPhone Safari／Android Chrome 實體手機、真正後鏡頭、低光／反光與權限恢復。
- LINE／Email 的實際傳送與內建瀏覽器、真實手機網路切換。
- Docker Engine build／容器運行／資料卷重建驗證（本機未安裝 Docker Engine）。
- Oracle ARM Linux、正式 DNS、防火牆、HTTPS 憑證簽發與 VPS 重啟。
- 現場 Wi-Fi 尖峰負載、長時間連續掃描與外部備份還原演練。

請逐項完成 [ACCEPTANCE.md](ACCEPTANCE.md)。瀏覽器模擬、單機並行測試及設定語法檢查不能代表以上實機／部署項目已驗收。
