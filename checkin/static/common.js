'use strict';
window.Checkin = {
  async api(path, data, method = 'POST') {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(path, {
        method, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: { 'Content-Type': 'application/json',
          'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content },
        ...(method === 'GET' ? {} : { body: JSON.stringify(data) })
      });
      const value = await response.json();
      if (!response.ok) {
        const error = new Error(value.error || '伺服器無法完成操作，請重試。');
        error.status = response.status;
        throw error;
      }
      return value;
    } finally { clearTimeout(timer); }
  },
  time(value) {
    if (!value) return '—';
    return new Intl.DateTimeFormat('zh-TW', {
      timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
    }).format(new Date(value));
  },
  requestId() {
    const bytes = new Uint8Array(24);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
  },
  errorMessage(error, writing = false) {
    if (error.status) return error.message;
    return writing ? '連線中斷或請求逾時，尚未確認報到結果。請保留此畫面並再次按「確認報到」；安全重試不會重複計數。'
      : '無法連線或請求逾時，請確認網路後重試。';
  }
};
document.querySelectorAll('[data-copy]').forEach(button => {
  button.addEventListener('click', async () => {
    const input = document.getElementById(button.dataset.copy);
    const message = document.getElementById('copy-message');
    try {
      await navigator.clipboard.writeText(input.value);
      message.textContent = '連結已複製，請單獨發送給本人。';
    } catch (_error) {
      input.focus(); input.select(); input.setSelectionRange(0, input.value.length);
      message.textContent = '請長按已選取的連結，選擇「複製」。';
    }
  });
});
