"""Small, shared public UI catalog. Operator pages always stay in Chinese.

Language selection never changes authorization, ticket credentials or game data.
Organizer-entered text and personal data are deliberately not translated.
"""
from flask import g, request
from werkzeug.datastructures import LanguageAccept


PUBLIC_PATHS = frozenset({
    '/', '/ticket', '/bingo', '/bingo/display', '/raffle/display',
    '/api/ticket', '/api/bingo/state', '/api/bingo/display-state',
    '/api/bingo/claim', '/api/bingo/marks', '/api/bingo/leave',
    '/api/raffle/display-state',
})
COOKIE_NAME = 'guest_language'
LANGUAGES = ('zh-TW', 'en')

# Chinese source strings are the keys for both Jinja and JavaScript. Placeholders
# use {name}; translations are plain text, never HTML or executable code.
ENGLISH = {
    '語言': 'Language',
    '電子報到': 'Event check-in',
    '示範環境 · 僅含虛構來賓，與正式資料分開': 'Demo environment · Fictional guests only',
    '晚宴電子報到': 'Event check-in',
    '時間皆為台北時間 UTC+8': 'All times are Taipei time (UTC+8)',
    '相聚，從這裡開始。': 'A warm welcome awaits.',
    '電子報到系統': 'Event check-in',
    '歡迎蒞臨 {event}': 'Welcome to {event}',
    '參加者請開啟主辦方傳送的個人票券，並在入口出示 QR Code。': 'Open the personal ticket sent by the organizer and show its QR code at the entrance.',
    '開啟票券不會完成報到，請由現場工作人員核對。': 'Please ask a staff member to check you in. Opening your ticket does not check you in.',
    '工作人員登入': 'Staff sign-in',
    '個人電子票券': 'Your personal ticket',
    '正在讀取票券…': 'Loading your ticket…',
    '正在向伺服器確認票券狀態…': 'Checking your ticket with the server…',
    '待報到': 'Not checked in',
    '已報到': 'Checked in',
    '您的桌次': 'Your table',
    '您的抽獎號碼：': 'Your lucky draw number: ',
    '完成報到後，依主持人設定參加抽獎；投影以此號碼公告得主。': 'After check-in, you can enter the lucky draw under the host’s rules. Winners are announced by this number.',
    '個人報到 QR Code': 'Your personal check-in QR code',
    '請在入口出示此 QR Code，由工作人員核對並確認。': 'Show this QR code at the entrance so a staff member can confirm your check-in.',
    '更新報到狀態': 'Refresh check-in status',
    '領取／開啟賓果卡': 'Get / open your bingo card',
    '完成報到後，可使用這張電子票券領取賓果卡。': 'Once checked in, use this ticket to get your bingo card.',
    '此票券僅供本人使用，請妥善保管，避免轉傳。': 'Keep this personal ticket private. Please do not forward it.',
    '開啟票券與自行掃碼都不會自動報到。': 'Opening this ticket or scanning your own QR code does not check you in.',
    '重新讀取票券': 'Reload ticket',
    '歡迎蒞臨晚宴': 'Welcome to the event',
    '現場安排': 'Ask staff for your table',
    '報到時間：{time}': 'Checked in at: {time} (Taipei)',
    '主持人開放領卡後，點選上方按鈕即可領取或恢復同一張卡。': 'When the host opens registration, use the button above to get or restore the same card.',
    '票券連結不完整，請從主辦方訊息開啟完整連結，或洽現場工作人員。': 'This ticket link is incomplete. Open the full link sent by the organizer, or ask a staff member for help.',
    '網路中斷或逾時，領卡結果尚未確認。可安全重試，系統會恢復同一張卡。': 'The connection was lost or timed out. Your card has not been confirmed. It is safe to retry; the same card will be restored.',
    '活動資訊': 'Event information',
    '活動時間': 'Date and time (Taipei, UTC+8)',
    '活動地點': 'Venue',
    '主辦單位': 'Organizer',
    '聯絡方式': 'Contact',
    '暫時無法完成操作': 'Unable to complete this request',
    '重新登入': 'Sign in again',
    '返回首頁': 'Back to home',
    '伺服器無法完成操作，請重試。': 'The server could not complete your request. Please try again.',
    '無法連線或請求逾時，請確認網路後重試。': 'The connection failed or timed out. Check your internet connection and try again.',
    '連線中斷或請求逾時，尚未確認報到結果。請保留此畫面並再次按「確認報到」；安全重試不會重複計數。': 'Check-in has not been confirmed. Keep this page open and retry after reconnecting. Retrying will not count you twice.',
    '賓果': 'Bingo',
    '我的卡片': 'My card',
    '現場開獎': 'Live draw',
    '導生晚宴手機賓果，每人一張卡，一起等候幸運號碼。': 'One bingo card per guest. Join us for the lucky numbers.',
    '連線中': 'Connecting',
    '連線中…': 'Connecting…',
    '本機示範活動 · 資料與正式活動分開': 'Demo event · Separate from the real event',
    '本機示範活動': 'Demo event',
    '今晚，好運與你同席。': 'A little luck for your evening.',
    '一張卡片，一條連線，就有機會帶走驚喜。': 'One card, one line — a chance to win a little surprise.',
    '領取你的幸運卡': 'Get your lucky card',
    '請先完成報到，再使用主辦方發送的個人電子票券領卡。': 'Check in first, then use your personal ticket from the organizer to get your card.',
    '個人電子票券連結': 'Personal ticket link',
    '貼上個人電子票券完整連結': 'Paste your full personal ticket link',
    '請先完成報到。同一張電子票券可恢復原卡；姓名與桌次沿用活動名冊，請勿轉傳票券。': 'Check in first. Your ticket restores the same card, with your name and table from the guest list. Keep your ticket private.',
    '領卡／恢復原卡': 'Get / restore my card',
    '{name}的賓果卡': '{name}’s bingo card',
    '第 {game} 局': 'Game {game}',
    '最新開獎': 'Latest number',
    '等待主持人抽號': 'Waiting for the host',
    '5 乘 5 賓果卡': '5 by 5 bingo card',
    '已開出': 'Called',
    '手動勾選': 'Your marks',
    '勾選自動保存': 'Marks saved automatically',
    '點擊格子做記號。中獎由系統自動判定，無須搶按按鈕。': 'Tap a square to mark it. Wins are checked automatically; there is no need to rush to press anything.',
    '開獎紀錄': 'Draw history',
    '好運正在路上，請等待第一個號碼。': 'Your luck is on its way. Waiting for the first number.',
    '今晚的遊戲方式': 'How to play',
    '每人一張 5×5 卡片，中央星星為免費格。': 'One 5×5 card per guest. The star in the center is a free square.',
    '號碼 1～49；橫、直或對角任一連線即可。': 'Numbers run from 1 to 49. Complete a row, column or diagonal to make a line.',
    '本局獎品份數載入中，每人每局限得獎一次。': 'Loading the prize count. Each guest can win once per game.',
    '本局共 {count} 份獎品，每人每局限得獎一次。': 'Prizes this game: {count}. Each guest can win once per game.',
    '依開獎回合判定；同回合超額由系統隨機抽選。': 'Earlier completed lines win first. If too many guests finish in the same draw, winners are selected at random.',
    '切換票券': 'Switch ticket',
    '相聚一晚，留下一點幸運。': 'A lovely evening, a little luck to remember.',
    '投影畫面 ↗': 'Live display ↗',
    '全螢幕 ⛶': 'Full screen ⛶',
    '今晚的幸運號碼': 'Tonight’s lucky number',
    '等待開獎': 'Waiting for the draw',
    '與你同席的好運，即將登場。': 'A little luck is about to arrive.',
    '位來賓': 'guests',
    '個號碼已開出': 'numbers called',
    '幸運得主': 'Lucky winners',
    '一條連線，一份今晚的驚喜。': 'One line, one lovely surprise.',
    '賓果說明頁 QR 碼': 'QR code for the bingo page',
    '你的好運，準備好了嗎？': 'Ready for a little luck?',
    '先完成活動報到': 'Check in at the entrance first',
    '從個人電子票券領取賓果卡': 'Get your bingo card from your personal ticket',
    '已開號碼': 'Numbers called',
    '依開獎順序排列 →': 'In draw order →',
    '請開啟主辦方發送的個人電子票券，或貼上本活動的完整票券連結。': 'Open your personal ticket from the organizer, or paste the full ticket link for this event.',
    '即時連線': 'Connected',
    '重新連線中': 'Reconnecting',
    '連線中斷，請確認網路。畫面恢復後會自動同步。': 'Connection lost. Please check your internet connection. This page will sync when reconnected.',
    '操作失敗，請重新整理。': 'The request failed. Please refresh the page.',
    '最新號碼 {number}': 'Latest number: {number}',
    '第 {round} 回合開獎中': 'Drawing number {round}',
    '第 {round} 回合 · 幸運號碼即將揭曉': 'Draw {round} · The lucky number is coming',
    '第 {round} 回合，號碼 {number}': 'Draw {round}, number {number}',
    '第 {round} 回合': 'Draw {round}',
    '開獎進行中': 'Game in progress',
    '本局已結束': 'Game finished',
    '領卡已開放，請先完成活動報到，再使用個人電子票券領卡。': 'Registration is open. Check in first, then use your personal ticket to get your card.',
    '目前未開放新領卡。已領卡者可使用最新的個人電子票券恢復原卡。': 'New card registration is closed. If you already have a card, use your latest personal ticket to restore it.',
    '第 {round} 回合 · {status}': 'Draw {round} · {status}',
    'BINGO！今晚的幸運得主 ✦': 'BINGO! You’re a lucky winner ✦',
    '第 {round} 回合達成連線，{detail}': 'You completed a line on draw {round}. {detail}',
    '已完成領獎。': 'Your prize has been collected.',
    '請帶著 {card} 與此畫面向工作人員領獎。': 'Show {card} and this screen to a staff member to collect your prize.',
    '已達成連線，謝謝參與！': 'You completed a line. Thank you for playing!',
    '第 {round} 回合多人同時連線，剩餘名額由系統隨機抽選，本次未入選。': 'Several guests completed a line on draw {round}. The remaining prizes were assigned at random, and your card was not selected this time.',
    '謝謝你一起參與，下一次好運再相聚。': 'Thank you for playing. May luck find you next time!',
    '幸運卡已就位': 'Your lucky card is ready',
    '等候你的幸運連線': 'Waiting for your lucky line',
    '重新整理保留原卡；換裝置時，使用最新的個人電子票券恢復。': 'Refreshing keeps the same card. On another device, use your latest personal ticket to restore it.',
    '已開出的格子會有金色邊框。即使沒有勾選，仍會自動判定中獎。': 'Called numbers have a gold border. Wins are checked automatically, even if you do not mark the squares.',
    '中央免費格，已勾選': 'Center free square, marked',
    '號碼 {number}，{called}，{marked}': 'Number {number}, {called}, {marked}',
    '尚未開出': 'not called',
    '已勾選': 'marked',
    '未勾選': 'not marked',
    '儲存中…': 'Saving…',
    '已保存': 'Saved',
    '待連線後儲存': 'Will save when reconnected',
    '請使用最新電子票券恢復原卡，手動勾選暫存在此裝置。': 'Use your latest ticket to restore your card. Your marks are temporarily saved on this device.',
    '幸運席位，為你保留': 'A lucky place awaits',
    '第一位得主即將登場。': 'The first winner is coming soon.',
    '幸運來賓': 'Lucky guest',
    '{card} · 第 {round} 回合連線': '{card} · Line completed on draw {round}',
    '已領獎': 'Prize collected',
    '恭喜中獎': 'Congratulations!',
    '本局已結束，恭喜今晚的幸運得主。': 'Game finished. Congratulations to our lucky winners!',
    '第 {round} 回合 · 幸運正在發生': 'Draw {round} · A little luck is happening',
    '卡片已就位，祝你今晚好運！': 'Your card is ready. Good luck tonight!',
    '此瀏覽器不支援全螢幕，請使用投影電腦的瀏覽器。': 'Full screen is not supported here. Use the browser on the projection computer.',
    '請在投影電腦上使用瀏覽器全螢幕功能。': 'Please use the browser’s full screen option on the projection computer.',
    '離線 · 等待網路恢復': 'Offline · Waiting to reconnect',
    '抽獎投影': 'Lucky draw display',
    '一般抽獎': 'Lucky draw',
    '加碼抽獎': 'Bonus draw',
    '全螢幕': 'Full screen',
    '以卡號公告得主，請依主持人指示核對本人並領獎。': 'Winners are announced by card number. Follow the host’s instructions to verify your ticket and collect your prize.',
    '今晚的獎品': 'Tonight’s prizes',
    '抽獎揭曉': 'Lucky draw reveal',
    '幸運得主即將揭曉': 'Our lucky winners are coming',
    '本次抽出 {count} 位 · 幸運即將揭曉': 'Winners to reveal: {count} · A little luck is coming',
    '恭喜幸運得主！': 'Congratulations to our lucky winners!',
    '已揭曉 {count} 位得主{detail}': 'Winners revealed: {count}{detail}',
    ' · 完整名單請見下方': ' · See the full list below',
    ' · 請核對本人後領獎': ' · Please verify your ticket to collect your prize',
    '已連線 · 每 2 秒更新': 'Connected · Updates every 2 seconds',
    '連線中斷，等待恢復': 'Connection lost · Waiting to reconnect',
    '幸運得主即將揭曉，請等候主持人抽獎。': 'Our lucky winners are coming. Please wait for the host to start the draw.',
    '已領獎 ✓': 'Prize collected ✓',
    '{name} · {awarded} / {quantity} 份': '{name} · {awarded} / {quantity} awarded',
    '此瀏覽器不支援全螢幕，請使用投影電腦的全螢幕功能。': 'Full screen is not supported here. Use the full screen option on the projection computer.',
    '請在投影電腦啟用瀏覽器全螢幕。': 'Please enable full screen on the projection computer.',
    '離線，請等待網路恢復': 'Offline · Waiting to reconnect',
    '尚未設定獎品，請等候主持人。': 'Prizes have not been announced yet. Please wait for the host.',
}

ERRORS = {
    '網域不正確，請使用主辦方提供的正式網址。': 'This address is not valid. Please use the official link from the organizer.',
    '資料格式錯誤，請重新確認。': 'The request is invalid. Check the information and try again.',
    '請先登入工作人員帳號。': 'Only signed-in staff can perform this action. Please ask a staff member for help.',
    '權限不足或驗證已過期，請重新整理後再試。': 'Access is not allowed or your security check has expired. Refresh the page and try again.',
    '查無資料，票券可能已失效。請洽現場工作人員。': 'No record was found. Your ticket may no longer be valid. Please ask a staff member for help.',
    '此操作方式不受支援。': 'This request method is not supported. Please use the buttons on the page.',
    '資料已變更，請重新整理後再試。': 'The information has changed. Refresh the page and try again.',
    '檔案過大，請使用 1 MB 以下的 UTF-8 CSV。': 'This request is too large. Please check the information and try again.',
    '操作過於頻繁，請稍候再試。': 'Too many requests. Please wait a minute before trying again.',
    '無法完成操作。': 'Your request could not be completed. Please refresh or ask a staff member for help.',
    '安全驗證已過期，請重新整理頁面後再試。': 'Your security check has expired. Refresh this page and try again.',
    '伺服器尚未確認寫入，請稍後安全重試，或聯絡管理員。': 'The server has not confirmed the change. It is safe to retry later, or ask a staff member for help.',
    '票券不存在或已補發失效，請向主辦方索取最新票券。': 'This ticket is invalid or has been replaced. Please ask the organizer for your latest ticket.',
    '賓果尚未準備完成，請聯絡管理員重新啟動服務。': 'Bingo is not ready yet. Please ask the organizer for help.',
    '局次已更新，請重新整理後操作。': 'A new game has started. Refresh the page before continuing.',
    '賓果存取已失效或報到已撤回，請使用最新電子票券重新領卡；仍需保持已報到狀態。': 'Your bingo access has expired or your check-in was withdrawn. You must be checked in; use your latest ticket to restore your card, or ask staff for help.',
    '請先由工作人員確認報到，再使用此電子票券領取賓果卡。': 'Please have a staff member check you in first, then use your ticket to get your bingo card.',
    '此瀏覽器已有另一位來賓的賓果卡，請先按「切換票券」，再開啟本人的電子票券。': 'This browser already has another guest’s card. Select “Switch ticket” first, then open your own personal ticket.',
    '目前未開放新領卡，請等候主持人開放。已領卡者可用最新票券恢復原卡。': 'New card registration is closed. Please wait for the host. If you already have a card, use your latest ticket to restore it.',
    '勾選格子資料無效。': 'These card marks are invalid. Refresh the page and try again.',
    '本局尚未領卡，請使用本人票券領卡。': 'You do not have a card for this game yet. Use your own ticket to get one.',
    '抽獎尚未準備完成，請重新啟動服務。': 'The lucky draw is not ready yet. Please ask the organizer for help.',
    '抽獎輪次已更新，請重新整理後操作。': 'A new draw has started. Please refresh the page.',
}


def tr(source, **values):
    translated = ENGLISH.get(source, source) if getattr(g, 'ui_language', 'zh-TW') == 'en' else source
    return translated.format_map(values) if values else translated


def public_error(message):
    if getattr(g, 'public_ui', False) and g.ui_language == 'en':
        return ERRORS.get(message, ERRORS['無法完成操作。'])
    return message


def install_i18n(app):
    app.jinja_env.globals['tr'] = tr

    @app.before_request
    def choose_language():
        g.public_ui = request.path in PUBLIC_PATHS
        g.ui_language = 'zh-TW'
        if not g.public_ui:
            return
        explicit = request.args.get('lang')
        header = request.headers.get('X-UI-Language') if request.path.startswith('/api/') else None
        preferred = next((language for language in (explicit, header, request.cookies.get(COOKIE_NAME))
                          if language in LANGUAGES), None)
        if preferred is None:
            # Match base languages as well: en-US/en-GB -> en, zh-Hant/zh-CN -> zh-TW.
            # Normalize variants before matching so a lower-priority exact
            # zh-TW match cannot outrank a preferred en-US browser language.
            accepted = LanguageAccept([(tag.replace('_', '-').split('-')[0].lower(), quality)
                                       for tag, quality in request.accept_languages])
            match = accepted.best_match(['zh', 'en'], default='zh')
            preferred = 'en' if match == 'en' else 'zh-TW'
        g.ui_language = preferred

    @app.after_request
    def remember_language(response):
        if getattr(g, 'public_ui', False):
            response.headers['Content-Language'] = g.ui_language
            response.vary.update(('Accept-Language', 'Cookie', 'X-UI-Language'))
            # Only an explicit choice writes a cookie. Do not refresh it on polling.
            if request.method == 'GET' and request.args.get('lang') in LANGUAGES:
                response.set_cookie(COOKIE_NAME, g.ui_language, max_age=365 * 24 * 60 * 60,
                                    secure=app.config['SESSION_COOKIE_SECURE'], httponly=True,
                                    samesite='Lax')
        return response

    @app.context_processor
    def language_context():
        language = getattr(g, 'ui_language', 'zh-TW')
        return {'public_ui': getattr(g, 'public_ui', False),
                'ui_language': language, 'html_language': 'en' if language == 'en' else 'zh-Hant',
                'public_strings': ENGLISH if language == 'en' else {key: key for key in ENGLISH}}
