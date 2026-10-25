# qrcode-checkin · 晚宴 QR Code 電子報到

獨立的 Python／Flask／SQLite 專案，預設活動名稱「導生晚宴」，支援最多 200 人。介面為繁體中文、黑金風格，所有畫面與 CSV 報表時間均為 Asia/Taipei。提供個人票券、工作人員掃碼／搜尋、管理後台，以及整合的晚宴賓果：完成報到後，使用原電子票券領卡。

本專案不讀寫其他專案。名冊與帳號保存在私有 `instance/`；正式 Docker 版存於獨立資料卷。

目前採 **一份部署對應一場活動**，透過事前設定調整活動資訊與初始管理員。多活動清單、活動資料隔離及類型／主題設定已記錄於 [後續規劃](docs/FUTURE_MULTI_EVENT.md)，待目前活動結束後再評估；現行範圍見 [架構決定](docs/adr/0001-single-event-deployment.md)。

## 1. 本機啟動

建議 Python 3.12 以上。既有 macOS Python 3.9 可用於本機展示；正式部署固定使用 Docker Python 3.12，不以舊 Python 上線。

在這個專案目錄執行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

### 快速試用（20 位虛構來賓）

```sh
sh scripts/run_demo.sh
```

開啟 <http://127.0.0.1:5001>。第一次啟動會產生 `instance/demo.sqlite3` 和隨機示範帳密，存於 `instance/demo-credentials.txt`（權限 600，不納入版本控制）。可在本機讀取此檔：

```sh
cat instance/demo-credentials.txt
```

示範管理員為 `demo-admin`，工作人員為 `demo-staff`；兩者密碼各自隨機產生。範例包含兩位同名者、一位未安排桌次者。重啟不重設、不覆寫資料；展示操作也會保留。

示範腳本強制使用 demo 資料庫、獨立 session cookie 名稱與簽章金鑰，不受正式 `.env` 的資料庫設定影響。需要恢復初始示範時，先停止示範服務，將 `instance/demo.sqlite3`、同名 `-wal`／`-shm`（如有）、`demo-credentials.txt` 移往私人備份資料夾，再執行腳本。勿操作正式資料庫。

### 手機透過內網測試

讓手機和電腦連上同一個 Wi-Fi，先停止原示範程序，再以以下指令啟動。將範例 IP 換成電腦當時的內網 IP（macOS 可用 `ipconfig getifaddr en0` 查詢）：

```sh
DEMO_HOST=0.0.0.0 DEMO_PUBLIC_BASE_URL=http://192.168.1.100:5001 sh scripts/run_demo.sh
```

手機開啟 `http://電腦內網IP:5001/login`，沿用同一份示範帳密。票券和 QR 也會使用這個內網網址，示範資料不會重設。切換 Wi-Fi／IP 後須重新設定網址並重啟；同一輪測試請使用同一個內網網址登入、開票及掃碼。

內網 HTTP 可測試登入、票券、後台、搜尋與人工報到；**手機網頁相機需要有效 HTTPS，無法用這個 HTTP 網址開啟**。真正的手機鏡頭掃碼仍需另行設定受手機信任的 HTTPS。

若使用伺服器這台 Mac 的 Safari 測試鏡頭，請在電腦開啟 `http://localhost:5001/staff`，手機仍用內網網址出示票券。報到頁同時接受目前來源與 `PUBLIC_BASE_URL` 設定的票券來源，其他網站的 QR 仍會被拒絕。Safari 提示時允許相機；若曾拒絕，可到 Safari → 設定 → 網站 → 相機，將 localhost 改成「詢問」或「允許」。[Apple 設定說明](https://support.apple.com/zh-tw/guide/safari/ibrwe2159f50/mac)

### 自己的活動與正式名冊（本機）

```sh
.venv/bin/python -m checkin.setup_event --target local
FLASK_SKIP_DOTENV=1 .venv/bin/python -m flask --app wsgi run --host 127.0.0.1 --port 5000
```

事前設定會詢問活動名稱、時間、地點、主辦單位、聯絡方式、票券提醒、公開網址及初始管理員帳密，產生權限 600 的 `.env`。密碼遮罩輸入並只存雜湊，session 金鑰隨機產生；服務首次啟動時在空白帳號資料庫建立管理員。首頁與票券會顯示填寫的活動資訊，空白項目隱藏。詳細流程見 [事前設定文件](docs/PRE_EVENT_SETUP.md)。

設定工具不覆寫既有 `.env`；可用 `--output /私人路徑/event.env` 草擬，再於新部署核對使用。既有帳號、名冊、票券及報到不會被初始化流程覆寫，示範資料保持獨立。已有正式部署可繼續使用 `.env.example` 手動設定與 `create-admin`，不必重新初始化。

管理員帳號需 3–40 位英數或 `_ . -`，密碼需 12–128 字。忘記密碼可在主機執行：

```sh
.venv/bin/python -m flask --app wsgi reset-password your-admin-name
```

本機資料庫為 `instance/checkin.sqlite3`，與 demo 完全分開。預設開發金鑰自動保存在 `instance/.session-key`，重啟不會登出；正式環境必須設定自己的隨機 `SECRET_KEY`。

| 介面 | 本機正式名冊 | 示範 |
| --- | --- | --- |
| 首頁 | `http://127.0.0.1:5000/` | `http://127.0.0.1:5001/` |
| 員工／管理員登入 | `/login` | `/login` |
| 工作人員掃碼與人工搜尋 | `/staff` | `/staff` |
| 管理總覽、名單、報表 | `/admin` | `/admin` |
| CSV 上傳預覽 | `/admin/import` | `/admin/import` |
| 工作人員帳號管理 | `/admin/users` | `/admin/users` |
| 個人票券 | `/ticket#隨機憑證` | 由管理後台開啟或複製 |
| 玩家賓果卡 | `/bingo` | 從已報到票券按「領取／開啟賓果卡」 |
| 賓果主持台 | `/bingo/host` | 管理員登入後使用 |
| 公開賓果投影 | `/bingo/display` | 唯讀、使用卡號呈現得主 |

手機的相機需要有效 HTTPS；電腦的 localhost／127.0.0.1 可用於本機測試。手機連到區網 HTTP 位址時，請以人工搜尋測試流程，正式掃碼驗收須用 HTTPS。`PUBLIC_BASE_URL` 必須是參加者和工作人員都能連到的同一網域根網址，不能含子路徑或查詢。正式發票前設定好網址；換網域後需重新匯出並發送連結／QR。

## 2. 匯入、管理名冊

可直接使用 [20 位虛構來賓範例 CSV](examples/attendees.csv) 測試匯入。檔案為 UTF-8，包含 4 桌、兩位同名來賓，以及一位尚未安排桌次的來賓；編號與示範資料庫一致，重匯會依編號更新。

1. 管理員登入，開啟「匯入名冊」，上傳 UTF-8 CSV（可帶 BOM，1 MB 以下）。範例見 [examples/attendees.csv](examples/attendees.csv)。
2. 檢查預覽、新增／更新人數與錯誤。任何錯誤都不會部分匯入，修正後重新上傳。
3. 按「確認匯入」才寫入。預覽只屬於上傳的管理員，15 分鐘到期；名冊在預覽後被編輯時，需重新預覽。

```csv
attendee_id,name,affiliation,table_no
G001,王小明,資訊工程學系,01
G002,王小明,電機工程學系,02
G003,陳小華,,
```

`attendee_id`、`name` 必填；其餘兩欄可省略或留白。欄名固定、不能重複或包含額外欄。上限分別為 64／100／120／30 字，不接受控制字元。編號以去除前後空白後的字串精確比對，大小寫不同視為不同編號；前導零會保留。

重匯以編號更新姓名、單位、桌次，不新增同一編號，不更換票券、不清除出席紀錄。**省略或空白的選填欄會將該名冊欄位清空**，請先核對預覽。檔案內重複編號會阻止整批匯入；不同編號可同名。

後台可搜尋姓名、編號、單位，篩選已／未報到，在「管理票券」編輯資料，或「新增臨時來賓」。總名冊含臨時來賓不得超過 200 人；不提供刪除來賓以保護歷史。

## 3. 發送個人票券

管理員在個人管理頁可複製連結、開啟票券或下載個人 QR PNG；管理總覽可匯出全員票券連結 CSV。主辦方自行用 LINE 或 Email **逐一發給本人**，本版不自動寄信。

票券憑證來自 `secrets.token_urlsafe(32)`，含 32 bytes 隨機資料（43 個 URL-safe 字元），不是個資或流水號。網址為 `/ticket#憑證`，憑證不在 HTTP 路徑／查詢／Referrer 中；前端以 POST 本文讀取本人的票券。票券顯示活動名稱、姓名、單位、桌次、QR、報到狀態及時間，開頁不會報到。

這是**持有連結即可讀取的個人票券**，並無參加者身分登入。無憑證者不能用姓名或編號取得票券，也沒有公開名冊 API；知道別人完整連結者仍可讀到那張票。請勿公開連結清單，現場務必核對本人，遺失或誤傳時由管理員補發。

補發需填原因並勾選確認，舊票立即失效，原來賓、報到狀態與歷史保留。相同管理表單重送會被拒絕，避免意外連續補發。補發後請重新傳送新連結。

參加者介面提供 **繁體中文／English**，涵蓋首頁、票券、賓果玩家頁、賓果與抽獎投影，以及狀態／錯誤提示。首次依瀏覽器語言選擇，頁首可手動切換並記住選擇；管理、主持及工作人員頁維持繁體中文。切換語言會保留票券及原賓果卡，不會完成報到。

可直接開啟 `/bingo?lang=en`、`/bingo/display?lang=en`、`/raffle/display?lang=en`。需要預先指定英文票券時，使用 `/ticket?lang=en#原憑證`，請保留完整 `#` 片段。活動名稱、地點、提醒、姓名及獎品等自訂內容照原文顯示，主辦方可事先輸入中英並列文字；不會自動翻譯個資。完整方式見 [雙語操作說明](docs/LANGUAGES.md)。

## 4. 工作人員現場操作

1. 管理員到「工作人員」為每位人員建立獨立帳號，個別交付密碼；可重設、停用、重新啟用。
2. 工作人員以 Safari／Chrome 登入 `/staff`，按「開啟後鏡頭」並允許相機。
3. 對準參加者手機上的 QR，辨識後暫停，顯示姓名、編號、單位、桌次與狀態。
4. 核對本人後按「確認報到」。只有伺服器成功寫入才顯示成功；再按「繼續下一位」。

重複票會顯示「已報到」、原報到時間及原操作者，不覆寫、不重複計數。不能使用相機時，可貼上本活動完整票券連結，或搜尋姓名／編號，選取正確者後確認人工報到。

網路斷線或 12 秒未收到回應時，顯示「尚未確認報到結果」，可按同一按鈕安全重試。即使伺服器已寫入、回應中途遺失，也只保留一次報到；重試會讀取原紀錄。不支援離線核銷或同步。

掃碼使用隨專案提供的 html5-qrcode 2.3.8 軟體解碼器，不依賴瀏覽器限定的原生 `BarcodeDetector`。相機實機相容性仍須按照 [實機驗收清單](docs/ACCEPTANCE.md) 完成，LINE 內建瀏覽器有問題時請改用系統瀏覽器。上游列出的平台支援不等於本專案已完成實機驗收。[上游平台說明](https://scanapp.org/html5-qrcode-docs/docs/supported_frameworks)

## 5. 管理、報表與稽核

- 總覽顯示總人數、已報到、未報到。「現場桌次概況」以卡片顯示各桌已報到／名冊人數及進度條，到齊桌次以綠色標示；底部顯示全場到場人數與比例，按「重新整理」取得最新資料。未安排桌次者另外顯示，也計入全場總數。分母依已匯入名冊的 `table_no` 分組計算各桌實際人數；更新 CSV 分組後，請重新匯入並確認，概況即以更新後名冊統計。
- 可匯出票券連結、目前出席報表、各桌總數／已報到／未報到統計、完整歷次報到／撤回歷史、稽核紀錄。所有匯出只有管理員能操作，CSV 以 UTF-8 BOM 輸出，時間含台北時區。
- 個人管理頁撤回報到需填原因、勾選確認；報到者、原時間、撤回者、原因與時間保留。可在重新核對後再報到，會另建歷史紀錄。
- 停用／重設工作人員密碼立即使既有登入失效。歷史操作者名稱以快照保存，帳號不刪除。
- CSV 儲存格以 `= + - @` 等開頭會加單引號，降低試算表公式風險；報表是供閱讀與統計，不是無損名冊備份，完整備份請使用下一節。

稽核記錄包括名冊匯入／編輯、新增來賓、報到、撤回、補發、帳號異動與匯出；不記錄票券 token 或密碼。資料庫含可發送的有效票券憑證及密碼雜湊，仍屬敏感檔案。

### 晚宴賓果

賓果整合自 [fk8663212/bingobingo](https://github.com/fk8663212/bingobingo)，保留原遊戲規則、玩家／主持／投影介面與開獎動畫。管理員在導覽列開啟「賓果主持台」，按「開放領卡」；已報到者在自己的電子票券按「領取／開啟賓果卡」。同一人每局一張卡，重開、換裝置或補發票券可恢復原卡。工作人員不具主持權限。

每局 1～49、中央免費格，獎品份數可由管理員設定為 1～200 份，每人每局最多中獎一次。第一局預設 10 份，可在首次開獎前從主持台儲存調整；建立下一局時可重新選擇。首次開獎後鎖定份數；開獎與同回合超額抽選由伺服器交易保存。下一局需輸入「開啟下一局」確認，歷史與領獎紀錄保留。公開投影用卡號顯示得主，管理員能核對姓名與桌次。完整操作、報到撤回與補發行為、來源版本見 [賓果文件](docs/BINGO.md)。

### 一般抽獎、獎品與加碼

管理員在導覽列開啟「一般抽獎」（`/raffle/host`），設定本輪規則、新增獎品名稱及份數，再選擇獎品抽出得主。只抽已報到來賓，預設本輪不重複中獎、排除先前一般獎得主，賓果得主仍可參加；三項資格開關在首次抽獎後鎖定。可隨時新增獎品、增加份數，或另開加碼輪次讓先前得主重新參加。

同一批人選不重複，並行抽選不超發；逾時保留操作識別碼，可安全重試。主持台可標記領獎、填原因撤銷並重抽，原紀錄與替補關聯保留。提供全部輪次 CSV 報表與完整 JSON 匯出。公開投影為 `/raffle/display`，以卡號公告，來賓可在原票券查看自己的抽獎號碼。流程、資格開關、重抽及網路恢復方式見 [一般抽獎文件](docs/RAFFLE.md)。

## 6. SQLite 一致性備份與還原

本系統使用 WAL、15 秒忙碌等待、明確 `BEGIN IMMEDIATE` 寫入交易，以及「同一人只能有一筆有效報到」的資料庫唯一索引。不要只複製運行中的 `.sqlite3` 而遺漏 WAL。

備份指令使用 SQLite Online Backup API，可在線上執行，完成後檢查 `PRAGMA integrity_check`，輸出檔不得已存在、不能位於公開 static 目錄，權限為 600。[SQLite 備份說明](https://www.sqlite.org/backup.html)

```sh
# 備份目前環境；demo 請額外指定 CHECKIN_ENV=demo DATABASE_PATH=完整的demo資料庫路徑。
.venv/bin/python -m flask --app wsgi backup-db backups/before-event.sqlite3
```

還原前停止**所有** Web／worker 程序，保存 `.env` 與金鑰，再執行：

```sh
.venv/bin/python -m flask --app wsgi restore-db backups/before-event.sqlite3 --confirm-stopped
```

指令驗證來源完整性、關聯與必要資料表，先保留目前資料庫為 `*.before-restore-隨機值`，再還原並使舊登入失效。還原會將名冊、票券與出席狀態回到備份時間，之後的報到不會自動合併。`--confirm-stopped` 是操作者的停機確認，不會替你停止服務。

備份／金鑰請存私人儲存空間，定期複製到主機外；保留天數由主辦方決定。活動前、匯入後、開場前及活動結束各備份一次，演練時還原到隔離位置，核對名冊與報到數。勿將匯出、DB 或備份放進 `checkin/static/`。

同一份備份也包含賓果及一般抽獎的獎品、得主與領獎紀錄；還原後請先核對備份時間之後實際已發出的獎品，再繼續抽獎。

## 7. Oracle ARM Linux VPS 部署範本

本次只提供範本與文件，未操作 VPS，未修改任何既有網站／服務。請見 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。包含 ARM64 Dockerfile、持久化 Compose、獨立 Caddy 自動 HTTPS、非 root 應用程式與 healthcheck。

## 8. 測試

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q

# 真正啟動本機 HTTP server 與瀏覽器；使用暫時資料庫，不碰 demo/正式資料。
.venv/bin/python -m playwright install chromium webkit
.venv/bin/python -m scripts.browser_smoke

# 雙 Gunicorn worker 的真實 HTTP 並行請求、程序停止／重啟、存取紀錄檢查。
.venv/bin/python -m scripts.server_smoke

# localhost 的相機安全環境與內網票券來源回歸檢查（不使用實體鏡頭）。
.venv/bin/python -m scripts.local_camera_smoke

# 報到→票券領卡→主持／投影→補發恢復，以及 200 人雙 worker 驗證。
.venv/bin/python -m scripts.bingo_browser_smoke
.venv/bin/python -m scripts.bingo_server_smoke

# 一般抽獎、領獎與重抽、回應遺失後重試，以及 200 人雙 worker 驗證。
.venv/bin/python -m scripts.raffle_browser_smoke
.venv/bin/python -m scripts.raffle_server_smoke

# 繁英切換、英文票券／領卡／得獎／抽獎投影，以及中文後台與手機尺寸。
.venv/bin/python -m scripts.i18n_browser_smoke

# 動畫狀態測試；只有這項測試需另有 Node.js，服務本身不需要。
node --test tests/bingo-animation.test.mjs tests/raffle-animation.test.mjs
```

若自行設定 `PLAYWRIGHT_BROWSERS_PATH`，安裝和執行時必須使用同一個路徑。瀏覽器腳本產生手機尺寸截圖及 `test-results/browser-results.json`，其中資料為測試假資料。本機結果與限制見 [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md)。

後端測試涵蓋 CSV 重匯／錯誤／容量、匿名與工作人員權限、CSRF、無效及撤銷票、10 個並行請求只寫一次、重試、補發、撤回、參加者資料隔離、帳號停用、限流、重啟保留、備份與還原。手機實機與 ARM Docker 執行驗收須另行完成。

現有 server smoke 是短時間並行及資料正確性驗證；200 人持續在線、滿載、尖峰及長時間運行，需在預定部署主機另外執行。測試情境、現有可跑指令、共用 Wi-Fi 限流與建議通過標準見 [活動前壓力測試](docs/LOAD_TESTING.md)。

事前設定測試另涵蓋密碼遮罩、設定檔權限、不覆寫既有設定、初始管理員只建立一次、並行啟動、改密碼後重啟不還原舊密碼，以及活動資訊顯示與 HTML 跳脫。

## 9. 安全設定與維護

正式模式會拒絕非 HTTPS 網址、過短或缺少的 `SECRET_KEY`，並啟用 Secure／HttpOnly／SameSite=Lax cookie、10 小時 session、HSTS、CSRF、CSP、no-store、noindex 及 no-referrer。參考 [Flask 安全文件](https://flask.palletsprojects.com/en/stable/web-security/)。密碼以 PBKDF2-SHA256（1,000,000 次）加隨機鹽雜湊保存。

登入每 IP 每 10 分鐘最多 30 次、每帳號 10 次；公開票券每 IP 每分鐘 300 次，員工查票每帳號每分鐘 300 次、搜尋 120 次。計數存在 SQLite，跨 worker 共用，固定時間窗；429 請等候窗結束（登入最長 10 分鐘）。不要開 debug 或啟用會記錄 POST 本文的代理、APM、分析／錯誤追蹤工具。

只在單一可信反向代理、且應用程式沒有對外公開埠時開 `TRUST_PROXY=1`；否則用 `0`。Docker 範本已限制應用程式僅在專用網路內供 Caddy 存取。

資料庫結構自動建立；賓果整合採新增 `bingo_*` 資料表，既有名冊、票券及報到保留。更新版本前先做一致性備份、停止所有 worker，閱讀變更並在獨立資料副本測試，再更新及重啟。套件版本固定於 requirements，未來應定期檢查上游安全修正後再測試更新。限制資料庫於單機本機磁碟，不使用 NFS 或多主機共享 SQLite。

## 專案結構

```text
checkin/              Flask app、權限、資料交易、頁面與自帶掃碼程式庫
examples/attendees.csv 20 人虛構名冊
tests/                隔離資料庫的自動化測試
scripts/              示範啟動、瀏覽器流程驗證
deploy/               Caddyfile、正式環境變數範例
docs/                 部署、測試結果、實機驗收
instance/             私有正式／示範資料（自動產生、不提交）
backups/              私有一致性備份（自行產生、不提交）
```
