'use strict';
(() => {
  const T = GuestUI.t;
  const token = location.hash.slice(1);
  const message = document.getElementById('ticket-message');
  const card = document.getElementById('ticket-card');
  const retry = document.getElementById('retry-ticket');
  const refresh = document.getElementById('refresh-ticket');
  const joinBingo = document.getElementById('join-bingo');
  const bingoMessage = document.getElementById('bingo-message');
  // Keep the fragment for refresh/share. HTTP and Referrer headers never contain it.
  async function load() {
    refresh.disabled = true;
    retry.hidden = true;
    message.hidden = false;
    message.className = 'notice';
    message.textContent = T('正在向伺服器確認票券狀態…');
    try {
      if (!/^[A-Za-z0-9_-]{43}$/.test(token)) {
        const error = new Error(T('票券連結不完整，請從主辦方訊息開啟完整連結，或洽現場工作人員。'));
        error.status = 400; throw error;
      }
      const data = await Checkin.api('/api/ticket', { token });
      document.getElementById('ticket-name').textContent = data.name;
      document.getElementById('ticket-affiliation').textContent = data.affiliation || T('歡迎蒞臨晚宴');
      document.getElementById('ticket-table').textContent = data.table_no || T('現場安排');
      const raffleLabel = document.getElementById('ticket-raffle-label');
      if (raffleLabel) raffleLabel.textContent = data.raffle_label || '—';
      document.getElementById('ticket-qr').src = data.qr;
      const status = document.getElementById('ticket-status');
      status.textContent = T(data.checked_in ? '已報到' : '待報到');
      status.className = `badge ${data.checked_in ? 'success' : ''}`;
      document.getElementById('ticket-time').textContent = data.checked_at ? T('報到時間：{time}', {time: Checkin.time(data.checked_at)}) : '';
      if (joinBingo) {
        joinBingo.hidden = !data.checked_in;
        bingoMessage.className = 'small muted top-space';
        bingoMessage.textContent = T(data.checked_in ? '主持人開放領卡後，點選上方按鈕即可領取或恢復同一張卡。' : '完成報到後，可使用這張電子票券領取賓果卡。');
      }
      card.hidden = false; message.hidden = true;
    } catch (error) {
      card.hidden = true; // A stale QR must not remain visible after revocation or failed refresh.
      message.className = 'notice error';
      message.textContent = Checkin.errorMessage(error);
      retry.hidden = false;
    } finally { refresh.disabled = false; }
  }
  refresh.addEventListener('click', load);
  if (joinBingo) joinBingo.addEventListener('click', async () => {
    joinBingo.disabled = true;
    try {
      await Checkin.api('/api/bingo/claim', {token});
      location.assign('/bingo');
    } catch (error) {
      bingoMessage.className = 'notice error top-space';
      bingoMessage.textContent = error.status ? error.message : T('網路中斷或逾時，領卡結果尚未確認。可安全重試，系統會恢復同一張卡。');
    } finally { joinBingo.disabled = false; }
  });
  retry.addEventListener('click', load);
  window.addEventListener('pageshow', event => { if (event.persisted) load(); });
  window.addEventListener('hashchange', () => location.reload());
  load();
})();
