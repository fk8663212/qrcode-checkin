# 參加者雙語介面

首頁、個人電子票券、賓果玩家頁、賓果投影與一般抽獎投影提供繁體中文及英文。登入、工作人員報到、管理後台、主持台與管理報表維持繁體中文。

## 開啟與切換

首次依瀏覽器的語言偏好選擇：英文變體（例如 en-US、en-GB）使用英文；中文變體使用繁體中文，無支援語言時預設繁體中文。頁首「繁體中文／English」可隨時切換，明確選擇以獨立 `guest_language` cookie 記住一年；清除 cookie 或換瀏覽器後會重新依瀏覽器偏好選擇。正式 HTTPS 的偏好 cookie 與登入 cookie 同樣設為 Secure、HttpOnly、SameSite=Lax。

也可以在網址指定語言，優先於 cookie 與瀏覽器設定：

| 用途 | 指定英文的路徑 |
| --- | --- |
| 首頁 | `/?lang=en` |
| 個人票券 | `/ticket?lang=en#原本的完整憑證` |
| 賓果玩家 | `/bingo?lang=en` |
| 賓果投影 | `/bingo/display?lang=en` |
| 一般抽獎投影 | `/raffle/display?lang=en` |

指定繁體中文使用 `?lang=zh-TW`。票券的 `?lang=en` 要放在 `#` **之前**；憑證仍只放在片段，不得搬到查詢字串或路徑。直接使用原始票券連結也能切換，不需要補發票券或重新產生 QR。

投影可各自選擇語言，主持台仍為中文；投影使用獨立瀏覽器／視窗時，可直接開啟上表指定語言的網址。語言在頁面載入時選定，後續同步、動畫、得獎及網路錯誤提示沿用同一語言，直到重新載入或切換。

## 參加者流程

1. 開啟主辦方傳送的票券，需要時在頁首選 English。
2. 在入口出示 QR code，由工作人員核對並確認報到。`Not checked in` 表示待報到；開票或切換語言不會報到。
3. 更新後顯示 `Checked in`、台北時間及桌次。自己的抽獎號碼仍為相同的 `Pxxx` 卡號。
4. 主持人開放領卡後，按 `Get / open your bingo card`。賓果規則、儲存狀態、連線中斷與得獎／領獎提示均有英文。
5. 切換語言、重新整理或使用補發後的新票，仍依既有資格恢復同一局原卡及已保存勾選。舊票券不會因切換語言重新有效。

## 主辦方自訂內容

介面翻譯不改寫活動資料。活動名稱、地點、單位、聯絡方式、提醒、參加者姓名、桌號、獎品名稱及自訂抽獎輪次名稱均照輸入原文顯示。內建的「一般抽獎／加碼抽獎」標題在英文投影對應 Lucky draw／Bonus draw。

若需要完整英文活動資訊，請在事前設定或原有編輯介面填寫雙語內容，例如：

- 活動名稱：`導生晚宴 / Mentor Dinner`
- 活動地點：`學生活動中心 / Student Activity Center`
- 提醒：`請攜帶學生證 / Please bring your student ID.`
- 獎品：`咖啡禮券 / Coffee voucher`

不需上傳另一份英文名冊，不需更換參加者編號，不會額外建立活動、報到、卡片或抽獎紀錄。所有時間仍為 Asia/Taipei（UTC+8），語言選擇不改變資格或存取權限。

## 維護與驗證

共用翻譯集中於 `checkin/i18n.py`，提供 Jinja 與 JavaScript 使用；只包含固定介面文字，沒有名冊或票券憑證。新增或修改文案時請同步維護英文。前端以文字節點呈現翻譯與個資，無外部翻譯服務或額外框架。

```sh
.venv/bin/python -m pytest -q tests/test_i18n.py
PLAYWRIGHT_BROWSERS_PATH=/安裝瀏覽器的位置 .venv/bin/python -m scripts.i18n_browser_smoke
```

瀏覽器腳本使用隔離資料庫、正常管理員及工作人員登入，涵蓋 Chromium／WebKit 英文與繁中切換、320／390px 排版、報到後領卡、勾選與得獎、投影動畫、斷線重試、補發及票券片段保留。結果保存在 `test-results/i18n-browser-results.json`，截圖 `*-english-*.png` 只含虛構資料。

真實 iPhone／Android、LINE 內建瀏覽器、現場投影及網路仍須完成 [實機驗收清單](ACCEPTANCE.md)，不能以桌面模擬視為已驗收。
