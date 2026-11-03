"""Run k6 against an explicit test site; credentials stay on the operator's PC.

Public mode only reads projection APIs. Bingo mode uses exported TEST* tickets,
normal CSRF/session flows and, optionally, a staff-confirmed test check-in.
This module deliberately does not import wsgi or open an application database.
"""
import argparse
import csv
import getpass
import http.cookiejar
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo


class LoadTestError(Exception):
    pass


def normalize_url(value, allow_local_http=False):
    try:
        parsed = urlsplit(value)
        valid = (parsed.hostname and not parsed.username and not parsed.password
                 and parsed.path in ('', '/') and not parsed.query and not parsed.fragment
                 and not any(character.isspace() or ord(character) < 32 for character in value)
                 and (parsed.port is None or 1 <= parsed.port <= 65535))
        local_http = (allow_local_http and parsed.scheme == 'http'
                      and parsed.hostname in ('localhost', '127.0.0.1', '::1'))
        if not valid or not (parsed.scheme == 'https' or local_http):
            raise ValueError()
    except (TypeError, ValueError):
        raise LoadTestError('網址需為 HTTPS 根網址，不可含帳密、路徑、查詢或片段。') from None
    return parsed.scheme + '://' + parsed.netloc.lower()


def origin(value):
    parsed = urlsplit(value)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


def duration_seconds(value):
    match = re.fullmatch(r'([1-9][0-9]*)(s|m|h)', value)
    if not match:
        raise LoadTestError('時間格式請用 30s、5m、30m 或 2h。')
    seconds = int(match[1]) * {'s': 1, 'm': 60, 'h': 3600}[match[2]]
    if not 5 <= seconds <= 7200:
        raise LoadTestError('每輪測試請設為 5 秒至 2 小時。')
    return seconds


def read_guests(path, base_url):
    """Select only fake TEST/TEST120/TEST200 IDs; never print bearer links."""
    try:
        if path.stat().st_size > 1024 * 1024:
            raise LoadTestError('票券清單不得超過 1 MB。')
        reader = csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'), newline=''), strict=True)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not {'attendee_id', 'name', 'ticket_url'}.issubset(fields):
            raise LoadTestError('請使用後台匯出的票券連結 CSV，需有 attendee_id、name、ticket_url。')
        guests, ids, tokens = [], set(), set()
        for number, row in enumerate(reader, 2):
            if number > 201 or None in row or any(value is None for value in row.values()):
                raise LoadTestError('票券清單格式錯誤或超過 200 位來賓。')
            attendee_id = row['attendee_id'].strip()
            if not re.fullmatch(r'TEST(?:[0-9]{1,3})?-[0-9]{3}', attendee_id):
                continue
            url = urlsplit(row['ticket_url'])
            if (origin(row['ticket_url']) != origin(base_url) or url.path != '/ticket'
                    or url.username or url.password or url.query
                    or not re.fullmatch(r'[A-Za-z0-9_-]{43}', url.fragment)):
                raise LoadTestError(f'第 {number} 列票券不是指定測試站的完整票券連結，請重新匯出。')
            name = row['name'].strip()
            if not name or len(name) > 100 or any(ord(char) < 32 for char in name):
                raise LoadTestError(f'第 {number} 列姓名格式錯誤。')
            if attendee_id in ids or url.fragment in tokens:
                raise LoadTestError('測試來賓編號或票券在檔案內重複。')
            ids.add(attendee_id)
            tokens.add(url.fragment)
            guests.append({'attendee_id': attendee_id, 'name': name, 'token': url.fragment})
    except (OSError, UnicodeError, csv.Error, ValueError):
        raise LoadTestError('無法讀取 UTF-8 票券 CSV，請確認路徑及檔案格式。') from None
    if not guests:
        raise LoadTestError('清單沒有 TEST-xxx、TEST120-xxx 或 TEST200-xxx 測試來賓。')
    return guests


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class Client:
    def __init__(self, base_url):
        self.base_url = base_url
        self.csrf = None
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())

    def request(self, path, data=None, form=False):
        headers = {'X-UI-Language': 'en'}
        if self.csrf:
            headers['X-CSRF-Token'] = self.csrf
        if data is not None:
            payload = (urlencode(data) if form else json.dumps(data)).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        else:
            payload = None
        request = Request(self.base_url + path, data=payload, headers=headers)
        try:
            try:
                response = self.opener.open(request, timeout=15)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise LoadTestError('預檢回應過大，已停止。')
                return response.status, raw.decode('utf-8'), response.headers
        except (URLError, TimeoutError, OSError, UnicodeError):
            raise LoadTestError('HTTPS 連線、憑證或預檢請求失敗；若已送出報到，請核對後安全重跑。') from None

    def json(self, path, data=None):
        status, text, _headers = self.request(path, data)
        if status not in (200, 201):
            raise LoadTestError(f'{path} 回應 HTTP {status}；請核對登入、票券、領卡開關或限流後再試。')
        try:
            result = json.loads(text)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except ValueError:
            raise LoadTestError(f'{path} 未回傳系統預期的 JSON；請檢查網域及代理設定。') from None

    def login(self, username, password):
        status, text, _headers = self.request('/login')
        self.csrf = csrf_from_html(text) if status == 200 else None
        if not self.csrf:
            raise LoadTestError('無法讀取登入頁安全驗證。')
        status, _text, headers = self.request('/login', {'username': username, 'password': password,
                                                        'csrf_token': self.csrf}, form=True)
        destination = urljoin(self.base_url, headers.get('Location', ''))
        if (status != 302 or origin(destination) != origin(self.base_url)
                or urlsplit(destination).path not in ('/staff', '/admin')):
            raise LoadTestError('工作人員登入失敗，請核對帳密或等待登入限流結束。')
        status, text, _headers = self.request(urlsplit(destination).path)
        self.csrf = csrf_from_html(text) if status == 200 else None
        if not self.csrf:
            raise LoadTestError('登入後無法取得安全驗證。')


def csrf_from_html(text):
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', text)
    return match[1] if match else None


def prepare_bingo(base_url, guests, check_in=False, username=None, password=None):
    public = Client(base_url)
    initial = public.json('/api/bingo/state')
    public.csrf = initial.get('csrf')
    game_id = initial.get('game', {}).get('id')
    if not public.csrf or type(game_id) is not int:
        raise LoadTestError('無法取得賓果局次與安全驗證。')
    if not initial['game'].get('registration_open') and initial.get('player_count') == 0:
        raise LoadTestError('目前尚未開放領卡；請先到測試站的「賓果主持台」按「開放領卡」。')
    pending = []
    for guest in guests:
        data = public.json('/api/ticket', {'token': guest['token']})
        if data.get('name') != guest['name'] or type(data.get('checked_in')) is not bool:
            raise LoadTestError('票券與匯出名冊不一致，已停止；請重新匯出測試站票券。')
        if not data['checked_in']:
            pending.append(guest)
    if pending and not check_in:
        raise LoadTestError(f'有 {len(pending)} 位尚未報到；請先報到，或使用 --check-in --staff-user 測試工作人員帳號。')
    checked = 0
    if check_in and pending:
        username = username or input('測試工作人員帳號：').strip()
        password = password if password is not None else getpass.getpass('測試工作人員密碼（不顯示、不保存）：')
        if not username or not password:
            raise LoadTestError('自動報到需要測試工作人員帳號及終端機遮罩輸入的密碼。')
        staff = Client(base_url)
        staff.login(username, password)
        # Verify every selected identity before the first attendance mutation.
        for guest in guests:
            data = staff.json('/api/staff/lookup', {'token': guest['token']})
            if data.get('attendee_id') != guest['attendee_id'] or data.get('name') != guest['name']:
                raise LoadTestError('工作人員查票與測試名冊不一致，未繼續報到。')
        for guest in pending:
            data = staff.json('/api/staff/checkin', {'token': guest['token'], 'request_id': secrets.token_hex(24)})
            attendee = data.get('attendee', {})
            if attendee.get('attendee_id') != guest['attendee_id'] or not attendee.get('checked_in'):
                raise LoadTestError('伺服器尚未確認預期報到結果，請先核對後台。')
            checked += 1
    return {'game_id': game_id, 'guests': guests}, checked


def main(argv=None):
    parser = argparse.ArgumentParser(description='對指定 HTTPS 測試站執行 k6 多人壓測；不讀取本機活動資料庫。')
    parser.add_argument('--url', required=True, help='VPS 測試站 HTTPS 根網址')
    parser.add_argument('--mode', choices=('public', 'bingo'), default='public')
    parser.add_argument('--users', type=int, default=10, help='虛擬使用者數量，1～200；預設 10')
    parser.add_argument('--duration', default='60s', help='例如 30s、5m、30m；最長 2h')
    parser.add_argument('--tickets', type=Path, help='管理員匯出的票券連結 CSV；不是只有姓名的名冊 CSV')
    parser.add_argument('--check-in', action='store_true', help='透過正常工作人員 POST，報到所選 TEST 假來賓')
    parser.add_argument('--staff-user', help='搭配 --check-in；可省略以互動輸入帳號，密碼一律遮罩輸入')
    parser.add_argument('--mark-every', type=int, default=0, help='賓果每幾次輪詢勾選一次；0 不寫入勾選')
    parser.add_argument('--poll-seconds', type=float, default=2, help='每位使用者的同步週期，至少 2 秒')
    parser.add_argument('--k6-bin', default='k6', help='k6 可執行檔名稱或完整路徑')
    parser.add_argument('--allow-local-http', action='store_true', help='僅供 localhost／loopback 隔離回歸測試')
    args = parser.parse_args(argv)
    try:
        base_url = normalize_url(args.url, args.allow_local_http)
        seconds = duration_seconds(args.duration)
        if not 1 <= args.users <= 200 or not 2 <= args.poll_seconds <= 60 or not 0 <= args.mark_every <= 1000:
            raise LoadTestError('人數需 1～200、同步週期需 2～60 秒、勾選間隔需 0～1000。')
        if args.mode == 'public' and (args.tickets or args.check_in or args.staff_user or args.mark_every):
            raise LoadTestError('public 模式僅讀公開狀態；票券、報到及勾選選項限 bingo 模式。')
        if args.mode == 'bingo' and (not args.tickets or (args.staff_user and not args.check_in)):
            raise LoadTestError('bingo 模式需 --tickets；--staff-user 僅能搭配 --check-in。')
        executable = shutil.which(args.k6_bin)
        if not executable:
            raise LoadTestError('找不到 k6；Mac 可先執行 brew install k6，或用 --k6-bin 指定位置。')
        guests = read_guests(args.tickets, base_url) if args.mode == 'bingo' else []
        if args.mode == 'bingo' and len(guests) < args.users:
            raise LoadTestError(f'清單只有 {len(guests)} 位 TEST 假來賓，不足以模擬 {args.users} 人。')
        health = Client(base_url).json('/healthz')
        if health.get('status') != 'ok':
            raise LoadTestError('測試站健康檢查未通過。')
        print(f'目標：{base_url}；模式：{args.mode}；人數：{args.users}；時間：{args.duration}', flush=True)
        config = {'version': 1, 'base_url': base_url, 'mode': args.mode, 'users': args.users,
                  'duration': args.duration, 'duration_seconds': seconds, 'poll_seconds': args.poll_seconds,
                  'mark_every': args.mark_every}
        if args.mode == 'bingo':
            print('將核對所選測試票券；領卡會保存同一局原卡，不會抽號或抽獎。', flush=True)
            prepared, checked = prepare_bingo(base_url, guests[:args.users], args.check_in, args.staff_user)
            config.update(prepared)
            print(f'票券預檢完成；本次協助報到 {checked} 位。', flush=True)
        root = Path(__file__).resolve().parent.parent
        stamp = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y%m%d-%H%M%S')
        output = root / 'test-results' / f'vps-load-{stamp}-{secrets.token_hex(3)}'
        output.mkdir(mode=0o700, parents=True)
        with tempfile.TemporaryDirectory(prefix='qrcode-load-private-') as directory:
            fixture = Path(directory) / 'fixture.json'
            fd = os.open(str(fixture), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as file:
                json.dump(config, file, ensure_ascii=False)
            # Do not inherit HTTP-debug, cloud outputs, TLS bypasses or VU /
            # duration overrides from an unrelated k6 installation/config.
            environment = {key: value for key, value in os.environ.items() if not key.startswith('K6_')}
            environment.update(CHECKIN_LOAD_FIXTURE=str(fixture),
                               CHECKIN_LOAD_REPORT=str(output / 'summary.json'), K6_NO_USAGE_REPORT='true')
            clean_options = Path(directory) / 'k6-options.json'
            clean_options.write_text('{}', encoding='utf-8')
            result = subprocess.run([executable, '--config', str(clean_options), '--no-color',
                                     'run', '--no-usage-report', str(root / 'scripts' / 'loadtest' / 'vps.js')],
                                    env=environment, cwd=str(root))
        print(f'壓測摘要：{output / "summary.json"}', flush=True)
        if result.returncode:
            return result.returncode
        try:
            summary = json.loads((output / 'summary.json').read_text())
        except (OSError, ValueError):
            raise LoadTestError('壓測未產生有效摘要，不能視為通過。') from None
        return 0 if summary.get('passed') else 1
    except LoadTestError as error:
        parser.exit(2, f'無法開始壓測：{error}\n')
    except KeyboardInterrupt:
        parser.exit(130, '測試已中止；測試報到或已領卡紀錄會保留，私人暫存票券檔已清除。\n')
    except OSError:
        parser.exit(2, '無法執行 k6 或保存私有測試檔；請檢查路徑及檔案權限。\n')
    except EOFError:
        parser.exit(2, '請在可互動輸入帳號與密碼的本機終端機執行，不要把密碼貼到聊天或命令列。\n')


if __name__ == '__main__':
    raise SystemExit(main())
