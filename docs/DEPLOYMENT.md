# Oracle ARM VPS 部署操作範本

尚未實際部署。目標為 Oracle Ampere A1（Linux ARM64）、單一 Docker 主機、本機持久磁碟、獨立網域。本範本預期是一台可使用 80／443 的專用 VPS，**不要直接在既有網站主機啟動，或替換原有反向代理設定**。既有 80／443 服務需另行規劃共用代理，本次未做。

## 前置準備

1. 在預定 VPS 安裝官方 Docker Engine 與支援 `docker compose` 指令的 Compose（v2 以上）；確認 `uname -m` 是 `aarch64`、`docker compose version` 可用。
2. 準備獨立子網域，例如 `checkin.example.org`，A 記錄指向該 VPS。若未配置 IPv6，不要留下錯誤 AAAA。
3. Oracle VCN Security List／NSG 與主機防火牆允許 TCP 80／443；UDP 443 可選供 HTTP/3。SSH 依既有管理政策限制來源。不要公開 8000。
4. 在新的私有目錄複製本專案；不要包含本機 `.venv`、`instance`、`.env`、demo 資料、測試截圖或備份。

容器採 `python:3.12-slim` 和 `caddy:2-alpine` 多架構映像，Compose 指定 `linux/arm64`。在 x86 開發機要測試時須調整 platform 或準備模擬環境；這不代表已驗證 Oracle ARM。上線時可將基礎映像標籤替換成已驗證的 ARM64 digest 以固定版本。[Python 官方映像](https://hub.docker.com/_/python)

## 設定與啟動

```sh
cp deploy/production.env.example .env
chmod 600 .env
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

將隨機值填入 `.env` 的 `SECRET_KEY`，設定 `DOMAIN`、`ACME_EMAIL`、`EVENT_NAME`。不要將金鑰貼到聊天、issue 或版本庫。不要填 `https://` 在 DOMAIN 中，也不要填子路徑。

```sh
docker compose config --quiet
docker compose build
docker compose run --rm app python -m flask --app wsgi create-admin
docker compose up -d
docker compose ps
docker compose logs --tail 50 app proxy
```

Caddy 會在 DNS 和 80／443 可用時申請／續期 HTTPS 憑證；應用程式只透過專用 Docker 網路提供給代理。[Caddy 反向代理文件](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)

確認 `https://你的網域/healthz` 回 `{"status":"ok"}`；再登入 `/login`，匯入自己的 CSV、建立每人獨立員工帳號、測試票券，完成 [實機清單](ACCEPTANCE.md) 才發送正式票。正式環境沒有預設帳號、示範資料或自動產生管理員。

應用程式以 UID/GID 10001 執行，根檔案系統唯讀，只有 `/data` 卷及暫存空間可寫。若改用 bind mount，需自行讓目錄擁有者為 10001:10001 且權限 700；不要用 777。預設 named volume 第一次建立時繼承映像 `/data` 的擁有者。

## 資料與重建

- `checkin_data`：SQLite、WAL、私有備份；Compose 專案名固定 `qrcode-checkin`。
- `caddy_data`：TLS 憑證與自動續期狀態。
- `caddy_config`：Caddy 執行狀態。

`docker compose up -d --build` 或 `docker compose down` **保留** named volumes，重建不會清除名冊。**不要執行 `docker compose down -v` 或針對這些卷做 prune**。不要更改專案名或換新卷後誤以為資料遺失。正式金鑰另外安全備存，不只存在容器。

## 線上備份與取回

在主機建立僅管理員可讀的備份目錄，使用不重複檔名：

```sh
mkdir -p backups
chmod 700 backups
umask 077
docker compose exec app python -m flask --app wsgi backup-db /data/backups/before-event.sqlite3
docker compose cp app:/data/backups/before-event.sqlite3 ./backups/before-event.sqlite3
chmod 600 backups/before-event.sqlite3
```

`backup-db` 使用 SQLite Online Backup API，會做 integrity check。將這份一致性快照與 `.env` 安全複製到主機外私人儲存空間；勿從公開 static 或 HTTP 備份下載。不要用正在運行的原始 `.sqlite3` 單檔複製替代備份。

## 停機還原

還原會退回備份當時狀態，先向主辦方核對備份時間，停止所有報到操作。先透過 app 自己的 UID 10001 上傳私有快照至資料卷；不要以 root 複製後再 chown，本範本已移除容器的全部 capabilities。來源目的檔若已存在，請改用新的檔名。

```sh
# stdin 上傳不會將資料庫內容放進命令列或一般 log。
docker compose exec -T app python -c 'import os,sys; from pathlib import Path; os.umask(0o077); Path("/data/backups").mkdir(parents=True,exist_ok=True); f=open("/data/backups/restore.sqlite3","xb"); f.write(sys.stdin.buffer.read()); f.close()' < ./backups/before-event.sqlite3
docker compose stop proxy app
docker compose run --rm --no-deps app python -m flask --app wsgi restore-db /data/backups/restore.sqlite3 --confirm-stopped
docker compose up -d
```

不要對執行中的 app 做還原。指令保留還原前快照於 `/data/checkin.sqlite3.before-restore-*`，來源經驗證後才覆寫，並撤銷備份中的舊登入。重新登入核對總人數、票券、已報到數和歷史，確認後恢復入口報到。建議先在另一個隔離資料卷演練，不以正式卷試錯。

## 記錄與代理信任

票券憑證只位於網址 `#` 片段與必要的 POST 本文，程式不把它放進 request path、query 或一般 log。Gunicorn access log 只記錄 IP、method、path、status、耗時；Caddy 存取記錄設為 discard，不記錄本文或敏感 header。[Caddy log 設定](https://caddyserver.com/docs/caddyfile/directives/log)

Caddy 明確覆寫客戶端 IP／scheme 轉送標頭；app 的 `TRUST_PROXY=1` 只適用這個單一代理拓撲。不要公開 app 的 port、添加未配置的 CDN／第二層代理，或開啟 debug／HTTP 本文紀錄。

## 故障處理

| 現象 | 處理 |
| --- | --- |
| 首次啟動拒絕 | 確認 DOMAIN、SECRET_KEY 非空、金鑰至少 32 字、PUBLIC_BASE_URL 為 HTTPS |
| app unhealthy | 查看 app log、卷權限、磁碟空間；healthcheck 會帶正式 Host 以通過網域驗證 |
| Caddy 無法取憑證 | 檢查 DNS、Oracle 與主機防火牆、80／443 是否已被使用；不要停止未知既有服務 |
| 相機被拒絕 | 檢查有效 HTTPS、瀏覽器權限，改用 Safari／Chrome 或人工搜尋 |
| 429 | 等待限流窗結束；共用 Wi-Fi 的公共票券查詢共用 IP 額度 |
| 503／資料庫忙碌或磁碟滿 | 不宣告成功；檢查私人磁碟空間／卷，恢復後由工作人員安全重試 |
| 重建後看不到名冊 | 核對 Compose 專案名、卷掛載與 DATABASE_PATH；先查卷，不重新匯入覆蓋或刪除 |

本次交付不包含雲端帳號配置、DNS 變更、防火牆修改、憑證簽發或 VPS 實際部署。Docker 執行、ARM64 映像建置和 HTTPS 端到端測試仍須在部署階段完成。
