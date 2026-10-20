'use strict';
(() => {
  const byId = id => document.getElementById(id);
  const message = byId('staff-message');
  const confirmation = byId('confirmation');
  const confirm = byId('confirm-checkin');
  const next = byId('next-guest');
  const start = byId('start-camera');
  const stop = byId('stop-camera');
  const ticketOrigins = new Set([location.origin, new URL(byId('reader').dataset.ticketBaseUrl).origin]);
  let scanner, running = false, chosen = null, busy = false, cameraBusy = false;

  function notice(text, kind = 'error') {
    message.textContent = text;
    message.className = `notice ${kind}`;
    message.hidden = false;
  }
  function tokenFrom(text) {
    text = text.trim();
    if (/^[A-Za-z0-9_-]{43}$/.test(text)) return text;
    try {
      const url = new URL(text);
      if (ticketOrigins.has(url.origin) && url.pathname === '/ticket' && /^[A-Za-z0-9_-]{43}$/.test(url.hash.slice(1))) return url.hash.slice(1);
    } catch (_error) { /* Show a local, understandable error below. */ }
    const error = new Error('這不是本活動的有效票券，請核對網址，或改用姓名／編號搜尋。');
    error.status = 400; throw error;
  }
  function showGuest(guest, success = false) {
    byId('result-name').textContent = guest.name;
    byId('result-id').textContent = guest.attendee_id;
    byId('result-affiliation').textContent = guest.affiliation || '未填寫';
    byId('result-table').textContent = guest.table_no || '現場安排';
    byId('result-status').textContent = guest.checked_in ? '已報到' : '尚未報到';
    byId('result-status').className = guest.checked_in ? 'success-text' : '';
    byId('result-label').textContent = success ? '✓ 報到成功' : (guest.checked_in ? '已報到 · 請勿重複計數' : '請核對來賓');
    byId('result-history').textContent = guest.checked_at ? `原報到時間：${Checkin.time(guest.checked_at)} · 操作者：${guest.operator_name}` : '確認來賓身分後，再按下方按鈕。';
    confirm.hidden = guest.checked_in;
    confirm.disabled = false;
    confirm.textContent = '確認報到';
    confirmation.hidden = false;
    confirmation.classList.toggle('completed', guest.checked_in);
    confirmation.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  function selectGuest(guest, credential) {
    if (busy) return;
    chosen = { ...credential, request_id: Checkin.requestId() };
    if (running) { scanner.pause(true); }
    message.hidden = true;
    byId('search-results').replaceChildren();
    showGuest(guest);
  }
  async function lookup(text) {
    if (busy || chosen) return;
    busy = true;
    try {
      const token = tokenFrom(text);
      const guest = await Checkin.api('/api/staff/lookup', { token });
      busy = false;
      selectGuest(guest, { token });
      byId('ticket-input').value = '';
    } catch (error) {
      notice(Checkin.errorMessage(error));
    } finally { busy = false; }
  }

  start.addEventListener('click', async () => {
    if (cameraBusy || running) return;
    if (!window.isSecureContext) {
      const localUrl = new URL('/staff', location.href);
      localUrl.hostname = 'localhost';
      notice(`目前的 HTTP 網址無法使用相機。若在伺服器這台電腦測試，請改開 ${localUrl.href}；手機請使用受信任的 HTTPS。也可使用姓名／編號搜尋。`);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      notice('瀏覽器未提供相機功能。請更新 Safari／Chrome 並檢查相機設定，或使用姓名／編號搜尋。');
      return;
    }
    cameraBusy = true; start.disabled = true;
    try {
      byId('reader').querySelector('.camera-placeholder')?.remove();
      // Bundled ZXing software decoder: do not rely on native BarcodeDetector.
      if (!scanner) scanner = new Html5Qrcode('reader', {
        formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE],
        experimentalFeatures: { useBarCodeDetectorIfSupported: false }, verbose: false
      });
      await scanner.start({ facingMode: 'environment' }, {
        fps: 8, qrbox: (width, height) => ({ width: Math.min(width, height, 260) * 0.8, height: Math.min(width, height, 260) * 0.8 }),
        aspectRatio: 1
      }, text => { if (!chosen && !busy) lookup(text); }, () => {});
      byId('reader').querySelectorAll('div').forEach(element => {
        if (element.textContent === 'Scanner paused') element.textContent = '掃描已暫停 · 請核對來賓';
      });
      running = true; stop.hidden = false; start.hidden = true;
      if (chosen) scanner.pause(true);
      message.hidden = true;
    } catch (_error) {
      notice('相機未能開啟。請允許相機權限、關閉其他相機程式，再重新開啟；或改用人工搜尋。');
    } finally { cameraBusy = false; start.disabled = false; }
  });
  async function stopCamera() {
    if (!running || cameraBusy) return;
    cameraBusy = true;
    try { await scanner.stop(); }
    catch (_error) { /* Camera may have been released by the browser. */ }
    finally { running = false; cameraBusy = false; start.hidden = false; stop.hidden = true; }
  }
  stop.addEventListener('click', stopCamera);
  window.addEventListener('pagehide', stopCamera);
  byId('token-form').addEventListener('submit', event => {
    event.preventDefault();
    if (chosen) { notice('請先按「繼續下一位」，再讀取新票券。'); return; }
    lookup(byId('ticket-input').value);
  });
  byId('search-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    if (chosen) { notice('請先按「繼續下一位」，再搜尋下一位來賓。'); return; }
    busy = true;
    const button = event.currentTarget.querySelector('button');
    button.disabled = true;
    try {
      const query = byId('search-input').value.trim();
      const data = await Checkin.api(`/api/staff/search?q=${encodeURIComponent(query)}`, null, 'GET');
      const results = byId('search-results');
      results.replaceChildren();
      data.results.forEach(guest => {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'search-result';
        const name = document.createElement('strong'); name.textContent = `${guest.name} · ${guest.attendee_id}`;
        const detail = document.createElement('span'); detail.textContent = `${guest.affiliation || '未填單位'} ｜ 桌次 ${guest.table_no || '待安排'} ｜ ${guest.checked_in ? '已報到' : '未報到'}`;
        button.append(name, detail);
        button.addEventListener('click', () => selectGuest(guest, { attendee_pk: guest.id }));
        results.append(button);
      });
      notice(data.results.length ? '請選擇來賓並核對資料，選取不會完成報到。' : '查無來賓，請核對拼字或編號；臨時來賓請洽管理員新增。', '');
    } catch (error) { notice(Checkin.errorMessage(error)); }
    finally { busy = false; button.disabled = false; }
  });
  confirm.addEventListener('click', async () => {
    if (!chosen || busy) return;
    busy = true; confirm.disabled = true; next.disabled = true;
    confirm.textContent = '伺服器確認中…';
    notice('正在等待伺服器確認，請稍候。', '');
    try {
      const data = await Checkin.api('/api/staff/checkin', chosen);
      // Only a successful server response may enter the success state.
      showGuest(data.attendee, data.created);
      notice(data.created ? `${data.attendee.name} · ${data.attendee.table_no || '現場安排'} · 報到成功` : '此來賓已報到，原紀錄未變更。', 'success');
    } catch (error) {
      notice(Checkin.errorMessage(error, true));
      confirm.disabled = false;
      confirm.textContent = '確認報到（安全重試）';
    } finally { busy = false; next.disabled = false; }
  });
  next.addEventListener('click', () => {
    if (busy) return;
    chosen = null; confirmation.hidden = true; message.hidden = true;
    byId('search-results').replaceChildren();
    byId('search-input').value = ''; byId('ticket-input').value = '';
    if (running) scanner.resume();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
  // Avoid a lost first tap while deferred scripts are still loading.
  start.disabled = false;
  byId('token-form').querySelector('button').disabled = false;
  byId('search-form').querySelector('button').disabled = false;
})();
