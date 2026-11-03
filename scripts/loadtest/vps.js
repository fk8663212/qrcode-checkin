import http from 'k6/http';
import {check, sleep} from 'k6';
import exec from 'k6/execution';
import {Counter, Rate, Trend} from 'k6/metrics';

// The launcher writes this credential file with 0600 permissions and deletes it
// on exit. There are no remote JS imports, HTTP-debug logs or cloud outputs.
const config = JSON.parse(open(__ENV.CHECKIN_LOAD_FIXTURE));
const stateMs = new Trend('state_ms', true);
const bingoMs = new Trend('bingo_state_ms', true);
const raffleMs = new Trend('raffle_state_ms', true);
const claimMs = new Trend('claim_ms', true);
const markMs = new Trend('mark_ms', true);
const invalid = new Rate('invalid_response');
const ready = new Counter('guests_ready');
const limited = new Counter('responses_429');
const serverErrors = new Counter('responses_5xx');
const networkErrors = new Counter('network_failures');
const marksSaved = new Counter('marks_saved');
let identity = null;

export const options = {
  scenarios: {guests: {executor: 'constant-vus', vus: config.users,
    duration: config.duration, gracefulStop: '15s'}},
  noCookiesReset: true,
  insecureSkipTLSVerify: false,
  maxRedirects: 0,
  thresholds: {
    http_req_failed: [{threshold: 'rate==0', abortOnFail: true, delayAbortEval: '10s'}],
    invalid_response: [{threshold: 'rate==0', abortOnFail: true, delayAbortEval: '10s'}],
    checks: ['rate==1'],
    state_ms: ['p(95)<1000', 'p(99)<2000'],
  },
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response'],
};

export function setup() {
  ready.add(0); limited.add(0); serverErrors.add(0); networkErrors.add(0); marksSaved.add(0);
}

function request(path, body, csrf, metric) {
  const started = Date.now();
  const response = http.request(body === undefined ? 'GET' : 'POST', config.base_url + path,
    body === undefined ? null : JSON.stringify(body), {
      timeout: '12s', redirects: 0,
      headers: {'Content-Type': 'application/json', 'X-UI-Language': 'en',
        ...(csrf ? {'X-CSRF-Token': csrf} : {})},
      tags: {name: path},
    });
  const elapsed = Date.now() - started;
  if (metric) metric.add(elapsed);
  if (response.status === 429) limited.add(1);
  if (response.status >= 500) serverErrors.add(1);
  if (response.status === 0) networkErrors.add(1);
  let value = null;
  try { value = response.json(); } catch (_) { /* Never log an HTTP body. */ }
  const ok = response.status === 200 && value && typeof value === 'object' && !Array.isArray(value);
  check(ok, {'HTTP 200 and JSON object': result => Boolean(result)});
  invalid.add(!ok);
  return ok ? value : null;
}

function validate(value, name, predicate) {
  const ok = Boolean(value && predicate(value));
  check(ok, {[name]: result => result});
  invalid.add(!ok);
  return ok;
}

function initializeGuest(guest) {
  const initial = request('/api/bingo/state');
  if (!initial?.csrf) exec.test.abort('無法建立獨立來賓 session／CSRF，已停止。');
  const claimed = request('/api/bingo/claim', {token: guest.token}, initial.csrf, claimMs);
  const ok = validate(claimed, 'Checked-in guest received their own card', data =>
    data.authenticated === true && data.game?.id === config.game_id &&
    data.card?.name === guest.name && Number.isInteger(data.card?.id) &&
    Array.isArray(data.card.numbers) && data.card.numbers.length === 25 && data.card.numbers[12] === 0);
  if (!ok) exec.test.abort('領卡失敗或資料不符；請核對已報到資格、領卡開關及最新票券。');
  identity = {id: claimed.card.id, numbers: JSON.stringify(claimed.card.numbers)};
  ready.add(1);
}

export default function () {
  const started = Date.now();
  try {
    if (config.mode === 'public') {
      const bingo = exec.vu.idInTest % 2 === 1;
      const value = request(bingo ? '/api/bingo/display-state' : '/api/raffle/display-state',
        undefined, undefined, bingo ? bingoMs : raffleMs);
      stateMs.add(Date.now() - started);
      validate(value, 'Public state has expected schema', data => bingo
        ? Number.isInteger(data.game?.id) && Array.isArray(data.draws) && Array.isArray(data.winners) && !data.players && !data.card
        : Number.isInteger(data.round?.id) && Array.isArray(data.prizes) && Array.isArray(data.winners) && !data.draws);
      return;
    }
    const guest = config.guests[exec.vu.idInTest - 1];
    if (!guest) exec.test.abort('虛擬使用者沒有對應的獨立測試票券，已停止。');
    if (!identity) initializeGuest(guest);
    const requestedAt = Date.now();
    const value = request('/api/bingo/state', undefined, undefined, bingoMs);
    stateMs.add(Date.now() - requestedAt);
    const ok = validate(value, 'Session retains the same guest, game and card', data =>
      data.authenticated === true && data.game?.id === config.game_id && data.card?.id === identity.id &&
      data.card.name === guest.name && JSON.stringify(data.card.numbers) === identity.numbers && Array.isArray(data.card.marks));
    if (!ok) exec.test.abort('來賓 session、票券或局次已變更；已停止，避免把未驗證頁面算成成功。');
    if (config.mark_every && (exec.vu.iterationInScenario + 1) % config.mark_every === 0) {
      const marks = new Set(value.card.marks);
      marks.has(0) ? marks.delete(0) : marks.add(0);
      marks.add(12);
      const saved = request('/api/bingo/marks', {game_id: config.game_id, marks: [...marks].sort((a,b) => a-b)}, value.csrf, markMs);
      if (validate(saved, 'Server confirmed card marks', data => data.ok === true)) marksSaved.add(1);
    }
  } finally {
    // Pace by iteration start; slow requests reduce achieved throughput. The
    // summary reports the actual rate, so slow traffic cannot look like 100 rps.
    sleep(Math.max(0, config.poll_seconds - (Date.now() - started) / 1000));
  }
}

export function handleSummary(data) {
  const names = ['http_reqs', 'http_req_failed', 'checks', 'state_ms', 'bingo_state_ms', 'raffle_state_ms',
    'claim_ms', 'mark_ms', 'guests_ready', 'invalid_response', 'responses_429', 'responses_5xx', 'network_failures', 'marks_saved'];
  const metrics = {};
  for (const name of names) if (data.metrics[name]) metrics[name] = data.metrics[name];
  const failures = Object.entries(data.metrics).flatMap(([name, metric]) =>
    Object.entries(metric.thresholds || {}).filter(([,result]) => !result.ok).map(([rule]) => `${name}: ${rule}`));
  const elapsedMs = data.state?.testRunDurationMs || 0;
  const hasSamples = (data.metrics.http_reqs?.values.count || 0) > 0 && data.metrics.state_ms?.values['p(95)'] !== undefined;
  const fullDuration = elapsedMs >= config.duration_seconds * 1000;
  const allGuestsReady = config.mode === 'public' || (data.metrics.guests_ready?.values.count || 0) === config.users;
  const passed = hasSamples && fullDuration && allGuestsReady && failures.length === 0;
  const summary = {format_version: 1, target: config.base_url, mode: config.mode, users: config.users,
    planned_duration: config.duration, elapsed_ms: elapsedMs, poll_seconds: config.poll_seconds,
    mark_every: config.mark_every, passed, full_duration: fullDuration, all_guests_ready: allGuestsReady,
    failed_thresholds: failures, metrics,
    scope: config.mode === 'public' ? 'Public projection API load only; no personal tickets or check-ins tested.'
      : 'Independent checked-in test ticket sessions, bingo claims, polling and optional card marks; no host draws performed.'};
  const latency = data.metrics.state_ms?.values || {};
  const consoleSummary = `${passed ? 'PASS' : '未通過／未完成'} · ${config.mode} · ${config.users} users\n` +
    `API p95=${latency['p(95)'] ?? 'n/a'}ms, p99=${latency['p(99)'] ?? 'n/a'}ms; ` +
    `actual HTTP rate=${data.metrics.http_reqs?.values.rate ?? 0}/s\n` +
    `429=${data.metrics.responses_429?.values.count || 0}, 5xx=${data.metrics.responses_5xx?.values.count || 0}, ` +
    `network errors=${data.metrics.network_failures?.values.count || 0}\n` +
    `報告：${__ENV.CHECKIN_LOAD_REPORT}\n`;
  return {stdout: consoleSummary, [__ENV.CHECKIN_LOAD_REPORT]: JSON.stringify(summary, null, 2)};
}
