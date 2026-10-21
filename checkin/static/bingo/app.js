'use strict';
import {createDrawPresenter} from './draw-animation.mjs';

const $ = (id) => document.getElementById(id);
const page = document.body.dataset.page;
let state = null, csrf = document.querySelector('meta[name="csrf-token"]').content, busy = false, archiveId = null, latestGameId = null;
let resetGameId = null;
let refreshing = false, refreshAgain = false, online = false, toastTimer;
let pendingMarks = null, savingMarks = false, lastCardSignature = '', lastTicketSignature = '';
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
let rollTimer = null, revealTimer = null;
const numberId = page === 'host' ? 'host-latest' : page === 'display' ? 'display-latest' : 'player-latest';
const captionId = page === 'host' ? 'host-round' : page === 'display' ? 'display-round' : 'player-round';
const stageId = page === 'player' ? 'player-draw-stage' : 'draw-stage';
const drawPresenter = createDrawPresenter({
  render: renderSnapshot,
  start: startNumberRoll,
  stop: stopNumberRoll,
  reveal: revealNumber,
  canAnimate: () => !reducedMotion.matches && document.visibilityState === 'visible',
});
const ticketToken = location.hash.slice(1);

function ticketCredential(value) {
  value = value.trim();
  if (/^[A-Za-z0-9_-]{43}$/.test(value)) return value;
  try {
    const url = new URL(value);
    const allowed = [location.origin, new URL(document.body.dataset.ticketBase).origin];
    if (allowed.includes(url.origin) && ['/ticket', '/bingo'].includes(url.pathname)
        && /^[A-Za-z0-9_-]{43}$/.test(url.hash.slice(1))) return url.hash.slice(1);
  } catch (_) { /* Show the same helpful message for malformed input. */ }
  throw new Error('請開啟主辦方發送的個人電子票券，或貼上本活動的完整票券連結。');
}
function storageKey(cardId) { return 'bingo-marks-' + state.scope + '-' + cardId; }

function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}
function show(id, visible) { if ($(id)) $(id).hidden = !visible; }
function text(id, value) { if ($(id)) $(id).textContent = value; }
function connection(ok, message) {
  online = ok;
  const el = $('connection');
  el.className = 'connection ' + (ok ? 'online' : 'offline');
  el.textContent = message || (ok ? '即時連線' : '重新連線中');
  if (page === 'host' && state) hostButtons();
}
function toast(message) {
  text('toast', message); show('toast', true); clearTimeout(toastTimer);
  toastTimer = setTimeout(() => show('toast', false), 5000);
}
function error(message) { text('page-error', message); show('page-error', !!message); }
async function api(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout ? AbortSignal.timeout(15000) : undefined,
    });
  } catch (cause) {
    connection(false);
    throw new Error('連線中斷，請確認網路。畫面恢復後會自動同步。', { cause });
  }
  const result = await response.json();
  if (!response.ok) {
    const e = new Error(result.error || '操作失敗，請重新整理。');
    e.status = response.status;
    throw e;
  }
  return result;
}
async function refresh() {
  if (refreshing) { refreshAgain = true; return; }
  refreshing = true;
  try {
    const url = page === 'host' ? '/api/bingo/admin/state' + (archiveId ? '?game=' + archiveId : '') : page === 'display' ? '/api/bingo/display-state' : '/api/bingo/state';
    const requestedAt = performance.now();
    const next = await api(url);
    const elapsedMs = performance.now() - requestedAt;
    csrf = next.csrf || '';
    if (page === 'host') latestGameId = next.games[0].id;
    show('demo-banner', next.demo);
    connection(true);
    error('');
    drawPresenter.update(next, {elapsedMs});
  } catch (e) {
    if (page === 'host' && [401, 403].includes(e.status)) {
      drawPresenter.reset();
      state = null; show('host-console', false); show('login-link', true);
      error('主持人登入已過期或權限不足，請使用管理員帳號重新登入。');
      connection(false, '請重新登入');
    } else { error(e.message); }
  } finally {
    refreshing = false;
    if (refreshAgain) { refreshAgain = false; setTimeout(refresh, 100); }
  }
}
function renderSnapshot(next) {
  if (state && state.game.id !== next.game.id) stopNumberRoll();
  state = next;
  if (page === 'host') renderHost();
  else if (page === 'player') renderPlayer();
  else renderDisplay();
  renderHistory();
  const stage = $(stageId);
  if (stage && page !== 'player') stage.setAttribute('aria-label', state.draws.length ? '最新號碼 ' + latestNumber() : '等待開獎');
}
function stopNumberRoll() {
  clearTimeout(rollTimer); clearTimeout(revealTimer);
  rollTimer = null; revealTimer = null;
  $(stageId)?.classList.remove('is-rolling', 'is-revealing');
  $(stageId)?.removeAttribute('aria-busy');
}
function startNumberRoll({round, remainingMs, pool}) {
  stopNumberRoll();
  const stage = $(stageId);
  if (!stage) return;
  stage.classList.add('is-rolling');
  stage.setAttribute('aria-busy', 'true');
  if (page !== 'player') stage.setAttribute('aria-label', '第 ' + round + ' 回合開獎中');
  text(captionId, '第 ' + round + ' 回合 · 幸運號碼即將揭曉');
  text('draw-announcement', '第 ' + round + ' 回合開獎中');
  if (page === 'host') { text('host-game-status', '開獎中'); hostButtons(); }
  const started = performance.now();
  let previous = null;
  function tick() {
    if (!drawPresenter.isAnimating()) return;
    const choices = pool.length > 1 ? pool.filter(n => n !== previous) : pool;
    previous = choices[Math.floor(Math.random() * choices.length)];
    text(numberId, String(previous).padStart(2, '0'));
    const progress = Math.min(1, (performance.now() - started) / remainingMs);
    rollTimer = setTimeout(tick, 55 + 185 * progress ** 3);
  }
  tick();
}
function revealNumber(next, effects) {
  const draw = next.draws.at(-1);
  text('draw-announcement', '第 ' + draw.round + ' 回合，號碼 ' + draw.number);
  if (!effects) return;
  const stage = $(stageId);
  stage?.classList.add('is-revealing');
  $('draw-history').lastElementChild?.classList.add('just-revealed');
  const newWinners = next.winners.filter(w => w.round === draw.round);
  if (newWinners.length) {
    const winnerList = $(page === 'host' ? 'host-winners' : 'display-winners');
    if (winnerList) {
      for (const row of winnerList.children) {
        if (newWinners.some(w => String(w.player_id) === row.dataset.playerId)) row.classList.add('just-won');
      }
    }
  }
  revealTimer = setTimeout(() => { stage?.classList.remove('is-revealing'); revealTimer = null; }, 1000);
}
function renderHistory() {
  text('history-count', state.draws.length + ' / 49');
  const history = $('draw-history'); history.replaceChildren();
  if (!state.draws.length) { history.append(node('p', 'empty', '好運正在路上，請等待第一個號碼。')); return; }
  state.draws.forEach((d, i) => {
    const ball = node('span', 'history-ball' + (i === state.draws.length - 1 ? ' latest' : ''), d.number);
    ball.title = '第 ' + d.round + ' 回合';
    ball.setAttribute('aria-label', '第 ' + d.round + ' 回合，號碼 ' + d.number);
    history.append(ball);
  });
}
function latestNumber() { return state.draws.length ? state.draws[state.draws.length - 1].number : '—'; }
function gameStatus() { return ({waiting: '等待開獎', playing: '開獎進行中', finished: '本局已結束'})[state.game.status]; }

function renderPlayer() {
  const card = state.card;
  show('claim-panel', !card); show('card-panel', !!card);
  if (!card) {
    if (ticketToken && !$('ticket-code').value) $('ticket-code').value = ticketToken;
    text('claim-hint', state.game.registration_open ? '領卡已開放，請先完成活動報到，再使用個人電子票券領卡。' : '目前未開放新領卡。已領卡者可使用最新的個人電子票券恢復原卡。');
    return;
  }
  text('card-label', card.card_label + ' · YOUR LUCKY CARD');
  text('player-greeting', card.name); text('game-badge', '第 ' + state.game.id + ' 局');
  text('player-latest', latestNumber());
  text('player-round', state.draws.length ? '第 ' + state.draws.length + ' 回合 · ' + gameStatus() : '等待主持人抽號');
  if (pendingMarks && pendingMarks.cardId !== card.id) {
    removeOutbox(pendingMarks.cardId); pendingMarks = null;
  }
  if (!pendingMarks) {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey(card.id)));
      if (saved && saved.gameId === state.game.id && Array.isArray(saved.marks)) pendingMarks = saved;
    } catch (_) { /* Storage may be disabled in private browsing. Server saves still work. */ }
  }
  renderCard();
  if (pendingMarks && !savingMarks) saveMarks();
  const win = state.winners.find(w => w.player_id === card.player_id);
  const status = $('player-status'); status.replaceChildren();
  let title, detail;
  if (win) {
    title = 'BINGO！今晚的幸運得主 ✦';
    detail = '第 ' + win.round + ' 回合達成連線，' + (win.claimed_at ? '已完成領獎。' : '請帶著 ' + card.card_label + ' 與此畫面向工作人員領獎。');
  } else if (card.qualified_round) {
    title = '已達成連線，謝謝參與！';
    detail = '第 ' + card.qualified_round + ' 回合多人同時連線，剩餘名額由系統隨機抽選，本次未入選。';
  } else if (state.game.status === 'finished') {
    title = '本局已結束'; detail = '謝謝你一起參與，下一次好運再相聚。';
  } else {
    title = state.game.status === 'waiting' ? '幸運卡已就位' : '等候你的幸運連線';
    detail = state.game.status === 'waiting' ? '重新整理保留原卡；換裝置時，使用最新的個人電子票券恢復。' : '已開出的格子會有金色邊框。即使沒有勾選，仍會自動判定中獎。';
  }
  status.append(node('strong', '', title), node('span', '', detail));
}
function renderCard() {
  const card = state.card;
  const marks = new Set(pendingMarks && pendingMarks.cardId === card.id ? pendingMarks.marks : card.marks);
  const drawn = new Set(state.draws.map(d => d.number));
  const winning = new Set(card.lines.flat());
  const signature = JSON.stringify([card.id, [...marks], [...drawn], [...winning]]);
  if (signature === lastCardSignature) return;
  lastCardSignature = signature;
  const focusIndex = document.activeElement?.dataset?.index;
  const grid = $('bingo-card'); grid.replaceChildren();
  card.numbers.forEach((number, i) => {
    const free = i === 12;
    const cell = node('button', 'bingo-cell' + (drawn.has(number) ? ' called' : '') + (marks.has(i) ? ' marked' : '') + (free ? ' free' : '') + (winning.has(i) ? ' winning' : ''));
    cell.type = 'button'; cell.dataset.index = String(i);
    cell.setAttribute('aria-pressed', marks.has(i) ? 'true' : 'false');
    cell.setAttribute('aria-label', free ? '中央免費格，已勾選' : '號碼 ' + number + (drawn.has(number) ? '，已開出' : '，尚未開出') + (marks.has(i) ? '，已勾選' : '，未勾選'));
    cell.append(node('span', '', free ? '✦' : number));
    if (free) { cell.append(node('small', '', 'FREE')); cell.disabled = true; }
    else cell.addEventListener('click', () => toggleMark(i));
    grid.append(cell);
  });
  if (focusIndex !== undefined) grid.querySelector('[data-index="' + focusIndex + '"]')?.focus({preventScroll: true});
}
function persistOutbox() {
  try { localStorage.setItem(storageKey(pendingMarks.cardId), JSON.stringify(pendingMarks)); } catch (_) { /* Optional offline cache. */ }
}
function removeOutbox(cardId) {
  try { localStorage.removeItem(storageKey(cardId)); } catch (_) { /* Optional offline cache. */ }
}
function toggleMark(i) {
  const current = new Set(pendingMarks ? pendingMarks.marks : state.card.marks);
  current.has(i) ? current.delete(i) : current.add(i); current.add(12);
  pendingMarks = { cardId: state.card.id, gameId: state.game.id, marks: [...current].sort((a,b) => a-b) };
  persistOutbox(); text('save-status', '儲存中…'); renderCard(); saveMarks();
}
async function saveMarks() {
  if (savingMarks || !pendingMarks) return;
  savingMarks = true;
  try {
    while (pendingMarks) {
      const submitted = pendingMarks;
      await api('/api/bingo/marks', {game_id: submitted.gameId, marks: submitted.marks});
      if (state.card && state.card.id === submitted.cardId) state.card.marks = submitted.marks;
      drawPresenter.patchPending(snapshot => {
        if (snapshot.card?.id === submitted.cardId) snapshot.card.marks = submitted.marks;
      });
      if (pendingMarks === submitted) { removeOutbox(submitted.cardId); pendingMarks = null; }
    }
    text('save-status', '已保存');
    if (state.card) renderCard();
  } catch (e) {
    text('save-status', '待連線後儲存');
    if (e.status === 409 && pendingMarks) { removeOutbox(pendingMarks.cardId); pendingMarks = null; refresh(); }
    else if (e.status === 401) { toast('請使用最新電子票券恢復原卡，手動勾選暫存在此裝置。'); refresh(); }
  } finally { savingMarks = false; }
}

function renderWinners(id, host) {
  const list = $(id); list.replaceChildren();
  list.classList.toggle('has-many', !host && state.winners.length > 5);
  if (!state.winners.length) {
    const empty = node('div', 'empty-winners');
    empty.append(node('span', 'empty-symbol', '✧'), node('h3', '', '幸運席位，為你保留'), node('p', 'muted', '第一位得主即將登場。')); list.append(empty); return;
  }
  state.winners.forEach(w => {
    const row = node('div', 'winner-row');
    row.dataset.playerId = String(w.player_id);
    const info = node('div', 'winner-name');
    info.append(node('strong', '', w.name), node('small', '', w.card_label + (host && w.table_no ? ' · ' + w.table_no + ' 桌' : '') + ' · 第 ' + w.round + ' 回合連線'));
    row.append(node('span', 'winner-rank', String(w.position).padStart(2, '0')), info);
    if (host) {
      const button = node('button', 'button small' + (w.claimed_at ? ' claimed' : ''), w.claimed_at ? '已領獎 ✓' : '標記領獎');
      button.disabled = busy || !online || drawPresenter.isAnimating();
      button.setAttribute('aria-label', w.name + (w.claimed_at ? '已領獎，點擊取消標記' : '標記為已領獎'));
      button.addEventListener('click', () => {
        if (w.claimed_at && !confirm('取消 ' + w.name + ' 的已領獎標記？')) return;
        hostAction('/api/bingo/admin/claimed', {game_id: state.game.id, player_id: w.player_id, claimed: !w.claimed_at}, '領獎紀錄已更新');
      }); row.append(button);
    } else row.append(node('span', 'winner-tag', w.claimed_at ? '已領獎' : '恭喜中獎'));
    list.append(row);
  });
}
function hostButtons() {
  if (!state) return;
  const historical = state.game.id !== latestGameId;
  const rolling = drawPresenter.isAnimating();
  $('draw-button').disabled = busy || rolling || !online || historical || state.game.status === 'finished' || !state.player_count;
  text('draw-button', rolling ? '幸運號碼揭曉中…' : busy ? '處理中，請稍候…' : state.game.status === 'finished' ? '本局已結束' : '抽出下一個號碼 ✦');
  $('registration-button').disabled = busy || rolling || !online || historical || state.game.status !== 'waiting';
  $('reset-button').disabled = busy || rolling || !online || historical;
  $('game-select').disabled = busy || rolling;
}
function renderHost() {
  show('host-console', true); show('login-link', false);
  text('host-title', state.game.title); show('archive-banner', state.game.id !== latestGameId);
  const select = $('game-select');
  if (select.options.length !== state.games.length) {
    select.replaceChildren(...state.games.map(g => {
      const option = node('option', '', '第 ' + g.id + ' 局' + (g.id === latestGameId ? '（目前）' : ''));
      option.value = String(g.id); return option;
    }));
  }
  select.value = String(state.game.id);
  text('stat-players', state.player_count); text('stat-draws', state.draws.length);
  text('stat-winners', state.winners.length + ' / ' + state.game.max_winners);
  text('stat-claimed', state.winners.filter(w => w.claimed_at).length);
  text('host-latest', latestNumber()); text('host-game-status', gameStatus());
  text('host-round', state.draws.length ? '第 ' + state.draws.length + ' 回合 · 尚餘 ' + (49-state.draws.length) + ' 個號碼' : '每一次開獎，都是新的期待');
  text('registration-button', state.game.registration_open ? '關閉領卡' : '開放領卡');
  text('registration-hint', state.game.status === 'waiting' ? (state.game.registration_open ? '目前開放 · 首次開獎後自動關閉' : '目前關閉 · 已領卡者可恢復') : '已開始開獎 · 停止新領卡');
  hostButtons(); renderWinners('host-winners', true); renderTickets(); renderAudit();
}
function renderTickets() {
  text('ticket-summary', '本局共 ' + state.players.length + ' 位來賓已領卡');
  const signature = JSON.stringify(state.players);
  if (signature === lastTicketSignature) return;
  lastTicketSignature = signature;
  const table = $('ticket-table'); table.replaceChildren();
  state.players.forEach(t => {
    const row = node('tr');
    row.append(node('td', '', 'P' + String(t.id).padStart(3,'0')), node('td', '', t.name), node('td', '', t.table_no || '未安排'), node('td', '', t.qualified_round || '尚未連線'));
    table.append(row);
  });
}
function renderAudit() {
  const audit = $('lottery-audit'); audit.replaceChildren();
  const results = state.round_results.filter(r => r.candidates.length);
  if (!results.length) { audit.append(node('p', 'empty', '尚無達成連線的回合。')); return; }
  const name = id => {
    const p = state.players.find(p => p.id === id);
    return 'P' + String(id).padStart(3,'0') + (p ? ' ' + p.name : '');
  };
  results.forEach(r => {
    const section = node('div', 'audit-round');
    section.append(node('strong', '', '第 ' + r.round + ' 回合 · 號碼 ' + r.number + (r.candidates.length > r.selected.length ? ' · 同回合超額抽選' : ' · 全數入選')),
      node('p', '', '達成連線：' + r.candidates.map(name).join('、')),
      node('p', '', '隨機順序：' + r.lottery_order.map(name).join(' → ')),
      node('p', '', '入選得主：' + r.selected.map(name).join('、')));
    audit.append(section);
  });
}
async function hostAction(path, body, message) {
  if (busy || drawPresenter.isAnimating()) return;
  busy = true; hostButtons();
  try { await api(path, body); if (message) toast(message); }
  catch (e) { error(e.message); toast(e.message); }
  finally { busy = false; await refresh(); }
}
function renderDisplay() {
  text('display-game', '第 ' + state.game.id + ' 局');
  text('display-latest', state.draws.length ? latestNumber() : '✦');
  text('display-round', state.game.status === 'finished' ? '本局已結束，恭喜今晚的幸運得主。' : state.draws.length ? '第 ' + state.draws.length + ' 回合 · 幸運正在發生' : '與你同席的好運，即將登場。');
  text('display-players', state.player_count); text('display-drawn', state.draws.length);
  text('display-winner-count', state.winners.length + ' / ' + state.game.max_winners);
  renderWinners('display-winners', false);
}

if (page === 'player') {
  $('leave-player').addEventListener('click', async () => {
    try { await api('/api/bingo/leave', {}); pendingMarks = null; location.href = '/bingo'; }
    catch (e) { error(e.message); }
  });
  $('claim-form').addEventListener('submit', async e => {
    e.preventDefault(); const button = e.target.querySelector('button'); button.disabled = true;
    try {
      await api('/api/bingo/claim', {token: ticketCredential($('ticket-code').value)});
      $('ticket-code').value = ''; toast('卡片已就位，祝你今晚好運！'); await refresh();
    } catch (err) { error(err.message); }
    finally { button.disabled = false; }
  });
}
if (page === 'host') {
  $('draw-button').addEventListener('click', () => hostAction('/api/bingo/admin/draw', {game_id: state.game.id, expected_round: state.draws.length}));
  $('registration-button').addEventListener('click', () => hostAction('/api/bingo/admin/registration', {game_id: state.game.id, open: !state.game.registration_open}));
  $('game-select').addEventListener('change', e => { archiveId = Number(e.target.value) === latestGameId ? null : Number(e.target.value); refresh(); });
  $('reset-button').addEventListener('click', () => { resetGameId = state.game.id; $('reset-confirm').value = ''; $('reset-dialog').showModal(); });
  $('cancel-reset').addEventListener('click', () => $('reset-dialog').close());
  $('reset-form').addEventListener('submit', async e => {
    e.preventDefault();
    if ($('reset-confirm').value !== '開啟下一局') { toast('請完整輸入「開啟下一局」。'); return; }
    $('reset-dialog').close();
    await hostAction('/api/bingo/admin/reset', {game_id: resetGameId, confirmation: $('reset-confirm').value}, '新局已準備好');
  });

}
if (page === 'display') {
  $('fullscreen').addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (document.documentElement.requestFullscreen) await document.documentElement.requestFullscreen();
      else toast('此瀏覽器不支援全螢幕，請使用投影電腦的瀏覽器。');
    } catch (_) { toast('請在投影電腦上使用瀏覽器全螢幕功能。'); }
  });
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') { refresh(); }
  else { drawPresenter.finish(); stopNumberRoll(); }
});
reducedMotion.addEventListener('change', () => { if (reducedMotion.matches) { drawPresenter.finish(); stopNumberRoll(); } });
window.addEventListener('online', () => { refresh(); });
window.addEventListener('offline', () => connection(false, '離線 · 等待網路恢復'));
setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 2000);
refresh();
