'use strict';
(() => {
  const token = location.hash.slice(1);
  const message = document.getElementById('ticket-message');
  const card = document.getElementById('ticket-card');
  const retry = document.getElementById('retry-ticket');
  const refresh = document.getElementById('refresh-ticket');
  // Keep the fragment for refresh/share. HTTP and Referrer headers never contain it.
  async function load() {
    refresh.disabled = true;
    retry.hidden = true;
    message.hidden = false;
    message.className = 'notice';
    message.textContent = '正在向伺服器確認票券狀態…';
    try {
      if (!/^[A-Za-z0-9_-]{43}$/.test(token)) {
        const error = new Error('票券連結不完整，請從主辦方訊息開啟完整連結，或洽現場工作人員。');
        error.status = 400; throw error;
      }
      const data = await Checkin.api('/api/ticket', { token });
      document.getElementById('ticket-name').textContent = data.name;
      document.getElementById('ticket-affiliation').textContent = data.affiliation || '歡迎蒞臨晚宴';
      document.getElementById('ticket-table').textContent = data.table_no || '現場安排';
      document.getElementById('ticket-qr').src = data.qr;
      const status = document.getElementById('ticket-status');
      status.textContent = data.checked_in ? '已報到' : '待報到';
      status.className = `badge ${data.checked_in ? 'success' : ''}`;
      document.getElementById('ticket-time').textContent = data.checked_at ? `報到時間：${Checkin.time(data.checked_at)}` : '';
      card.hidden = false; message.hidden = true;
    } catch (error) {
      card.hidden = true; // A stale QR must not remain visible after revocation or failed refresh.
      message.className = 'notice error';
      message.textContent = Checkin.errorMessage(error);
      retry.hidden = false;
    } finally { refresh.disabled = false; }
  }
  refresh.addEventListener('click', load);
  retry.addEventListener('click', load);
  window.addEventListener('pageshow', event => { if (event.persisted) load(); });
  window.addEventListener('hashchange', () => location.reload());
  load();
})();
