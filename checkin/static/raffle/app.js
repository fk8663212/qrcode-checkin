'use strict';
import {createRafflePresenter} from './animation.mjs';
(() => {
  const root = document.getElementById('raffle-app');
  const host = root.dataset.page === 'host';
  const $ = id => document.getElementById(id);
  const T = (source, values = {}) => window.GuestUI ? GuestUI.t(source, values) : source.replace(/\{([a-z_]+)\}/g, (match, key) => Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match);
  let state, archive = null, online = false, busy = false, refreshing = false;
  let configSignature = '', prizeSignature = '', winnerSignature = '', displaySignature = '', redrawWinner, roundAtDialog;
  const pendingKey = 'raffle-pending-' + location.origin + '-' + root.dataset.operator;
  let pending = null;
  let reelTimer = null, confirmedResult = null;
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const presenter = createRafflePresenter({
    render: next => {
      if(state && next.round.id!==state.round.id){$('raffle-animation').hidden=true;confirmedResult=null;}
      state=next;
      if(host)renderHost();else renderDisplay();
    },
    start: next => startReels(next.latest_draw.prize_name, T('本次抽出 {count} 位 · 幸運即將揭曉', {count: next.latest_draw.count})),
    stop: stopReels,
    reveal: revealWinners,
    canAnimate: () => !reducedMotion.matches && document.visibilityState==='visible',
  });
  if (host) {
    try { pending = JSON.parse(sessionStorage.getItem(pendingKey)); } catch (_) { /* The in-memory retry still works. */ }
  }
  function savePending(value) {
    pending = value;
    try { if (value) sessionStorage.setItem(pendingKey, JSON.stringify(value)); else sessionStorage.removeItem(pendingKey); } catch (_) { /* Private browsing can disable storage. */ }
    $('raffle-pending').hidden = !pending;
  }
  function el(tag, text, cls) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (cls) result.className = cls;
    return result;
  }
  function error(message) {
    $('raffle-error').textContent = message || ''; $('raffle-error').hidden = !message;
    root.querySelectorAll('dialog[open]').forEach(dialog => {
      let notice=dialog.querySelector('.raffle-dialog-error');
      if(!notice && message){notice=el('p',undefined,'notice error raffle-dialog-error');notice.setAttribute('role','alert');dialog.querySelector('form').prepend(notice);}
      if(notice){notice.textContent=message || '';notice.hidden=!message;}
    });
  }
  function stopReels() {
    clearInterval(reelTimer);reelTimer=null;
    $('raffle-animation').classList.remove('is-rolling');
    $('raffle-animation').removeAttribute('aria-busy');
  }
  function startReels(prize, hint) {
    stopReels();
    const stage=$('raffle-animation');stage.hidden=false;stage.classList.remove('is-revealed','without-motion');stage.classList.add('is-rolling');stage.setAttribute('aria-busy','true');
    $('animation-prize').textContent=prize;
    $('animation-title').textContent=T('幸運得主即將揭曉');
    $('animation-hint').textContent=hint;
    $('raffle-reels').hidden=false;$('animation-winners').hidden=true;
    let tick=0;
    if(!reducedMotion.matches)reelTimer=setInterval(() => {stage.querySelectorAll('.reel-digit').forEach((digit,i) => {digit.textContent=String((tick*(i+3)+i*7)%10);});tick++;},65);
    if(host)stage.scrollIntoView({behavior:reducedMotion.matches ? 'instant' : 'smooth',block:'center'});
    controls();
  }
  function announceResult() {
    if(!host || !confirmedResult)return;
    $('raffle-result').textContent=confirmedResult;
    $('raffle-result').hidden=false;
  }
  function revealWinners(next,{animate=true}={}) {
    const draw=next.latest_draw;if(!draw)return;
    stopReels();
    const winners=next.winners.filter(w => w.draw_id===draw.id && !w.voided_at);
    const stage=$('raffle-animation');stage.hidden=false;stage.dataset.drawId=draw.id;
    stage.classList.toggle('without-motion',!animate || reducedMotion.matches);
    stage.classList.remove('is-revealed');void stage.offsetWidth;stage.classList.add('is-revealed');
    $('animation-prize').textContent=draw.prize_name;
    $('animation-title').textContent=T('恭喜幸運得主！');
    $('raffle-reels').hidden=true;$('animation-winners').hidden=false;
    $('animation-winners').replaceChildren(...winners.slice(0,12).map(w => el('strong',host ? w.name+' · '+(w.table_no || '未安排')+' 桌' : w.card_label)));
    $('animation-hint').textContent=T('已揭曉 {count} 位得主{detail}', {count: winners.length, detail: T(winners.length>12 ? ' · 完整名單請見下方' : ' · 請核對本人後領獎')});
    announceResult();controls();
  }
  function options(prefix = '') {
    return {title:$(prefix ? 'new-round-name' : 'round-name').value,
      allow_repeat:$(prefix+'allow-repeat').checked, include_previous:$(prefix+'include-previous').checked,
      exclude_bingo:$(prefix+'exclude-bingo').checked};
  }
  function unsavedRules() {
    if (!state || state.round.status !== 'waiting') return false;
    const draft = options();
    return draft.title !== state.round.title || ['allow_repeat','include_previous','exclude_bingo'].some(k => draft[k] !== Boolean(state.round[k]));
  }
  function controls() {
    if (!host || !state) return;
    const historical = state.round.id !== state.latest_round_id;
    const locked = busy || !online || !!pending || presenter.isAnimating();
    const draft = unsavedRules();
    $('raffle-config-fields').disabled = locked || historical || state.round.status !== 'waiting';
    $('prize-add-fields').disabled = locked || historical;
    $('new-round-button').disabled = locked || historical;
    $('new-round-submit').disabled = locked;
    $('raffle-round').disabled = busy || !!pending || presenter.isAnimating();
    $('raffle-retry').disabled = busy || !online || presenter.isAnimating();
    $('raffle-pending').hidden = !pending;
    $('rule-hint').textContent = historical ? '歷史規則保留，可補登領獎。' : state.round.status !== 'waiting' ? '首次抽獎已完成，本輪規則已鎖定。' : draft ? '規則尚未儲存，請先儲存再抽獎。' : '規則已保存，首次抽獎後鎖定。';
    root.querySelectorAll('[data-write]').forEach(button => { button.disabled = locked || (historical && button.dataset.write !== 'claim'); });
    root.querySelectorAll('[data-prize-editor] input').forEach(input => { input.disabled = locked || historical; });
    root.querySelectorAll('[data-draw]').forEach(button => {
      const card = button.closest('.raffle-prize');
      const prize = state.prizes.find(p => p.id === Number(card.dataset.prizeId));
      const editor = card.querySelector('[data-prize-editor]');
      const edited = editor.elements.name.value !== prize.name || Number(editor.elements.quantity.value) !== prize.quantity;
      button.disabled = locked || historical || draft || edited || prize.awarded >= prize.quantity || !state.eligible_count;
    });
  }
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      const path = host ? '/api/raffle/admin/state'+(archive ? '?round='+archive : '') : '/api/raffle/display-state';
      const started=performance.now();
      const next = await Checkin.api(path, undefined, 'GET');
      if (next.csrf) document.querySelector('meta[name="csrf-token"]').content = next.csrf;
      online = true; $('raffle-connection').textContent = T('已連線 · 每 2 秒更新');
      if(!host)error('');
      presenter.update(next,{elapsedMs:performance.now()-started});
    } catch (e) {
      online = false; $('raffle-connection').textContent = T('連線中斷，等待恢復');
      error(host && [401,403].includes(e.status) ? '登入過期或權限不足，請回登入頁重新登入。' : Checkin.errorMessage(e));
    } finally { refreshing = false; controls(); }
  }
  async function mutate(action, data) {
    if (busy || pending || !online || presenter.isAnimating()) return false;
    busy = true; error(''); controls();
    try {
      await Checkin.api('/api/raffle/admin/'+action, {round_id:state.round.id, ...data});
      if(action==='configure')configSignature='';
      if(action==='prize')prizeSignature='';
      return true;
    }
    catch (e) { error(e.status ? e.message : '尚未確認儲存結果，請先核對畫面再重試。'); return false; }
    finally { busy = false; await refresh(); }
  }
  async function submitDraw(value) {
    if (busy || !online || presenter.isAnimating()) return;
    if (!pending) savePending(value);
    busy = true; error(''); $('raffle-result').hidden = true; controls();
    confirmedResult=null;
    const prize=state.prizes.find(p => p.id===pending.data.prize_id);
    startReels(prize?.name || '幸運抽獎','正在等待伺服器確認，請稍候…');
    try {
      const started=performance.now();
      const result = await Checkin.api('/api/raffle/admin/'+pending.action, pending.data);
      savePending(null);
      const winners = result.winners.filter(w => w.draw_id === result.result.draw_id);
      confirmedResult = winners.length ? '✓ 已確認抽出：'+winners.map(w => w.name+' · '+(w.table_no || '未安排')+' 桌 · '+w.prize_name).join('、') : '✓ 伺服器已確認原抽獎紀錄，請至對應歷史輪次查看。';
      presenter.update(result,{elapsedMs:performance.now()-started});
      if(!presenter.isAnimating()){
        if($('raffle-animation').classList.contains('is-rolling')){stopReels();$('raffle-animation').hidden=true;}
        announceResult();
      }
    } catch (e) {
      // A 5xx or lost response may follow a committed transaction. Keep the exact request for retry.
      if (e.status && e.status < 500 && ![401,403,408,429].includes(e.status)) savePending(null);
      error(e.status ? e.message : '抽獎結果尚未確認，請保留此頁並按「確認上次操作／安全重試」。');
      if(!presenter.isAnimating()){stopReels();$('raffle-animation').hidden=true;}
    } finally { busy = false; await refresh(); }
  }
  function renderHost() {
    $('round-title').textContent = state.round.title;
    $('eligible-count').textContent = state.eligible_count;
    $('prize-total').textContent = state.prizes.reduce((n,p) => n+p.quantity,0);
    const active = state.winners.filter(w => !w.voided_at);
    $('award-total').textContent = active.length+' / '+active.filter(w => w.claimed_at).length;
    const select = $('raffle-round');
    if (select.options.length !== state.rounds.length) {
      select.replaceChildren(...state.rounds.map(r => {const option = el('option',r.title+'（第 '+r.id+' 輪）');option.value=r.id;return option;}));
    }
    select.value = String(state.round.id);
    $('raffle-archive').textContent = state.round.id === state.latest_round_id ? '目前輪次 · '+(state.round.status === 'waiting' ? '尚未抽獎' : '抽獎進行中') : '正在檢視歷史輪次，抽獎與獎品設定請切回最新一輪。';
    // Ignore revision changes when keeping an unsaved rule draft.
    const config = JSON.stringify([state.round.id,state.round.title,state.round.status,state.round.allow_repeat,state.round.include_previous,state.round.exclude_bingo]);
    if (config !== configSignature) {
      configSignature = config; $('round-name').value = state.round.title;
      for (const [id,key] of [['allow-repeat','allow_repeat'],['include-previous','include_previous'],['exclude-bingo','exclude_bingo']]) $(id).checked = Boolean(state.round[key]);
    }
    const prizes = JSON.stringify([state.round.id,state.prizes]);
    if (prizes !== prizeSignature) { prizeSignature = prizes; renderPrizes(); }
    const winners = JSON.stringify([state.round.id,state.latest_round_id,state.winners]);
    if (winners !== winnerSignature) {winnerSignature=winners;renderWinners();}
    controls();
  }
  function renderPrizes() {
    const list = $('raffle-prizes'); list.replaceChildren();
    if (!state.prizes.length) { list.append(el('p','尚未設定獎品，請先新增。','raffle-empty')); return; }
    for (const prize of state.prizes) {
      const card = el('article',undefined,'raffle-prize'); card.dataset.prizeId = prize.id;
      card.append(el('h3',prize.name),el('p','已抽出 '+prize.awarded+' / '+prize.quantity+' 份 · 尚餘 '+(prize.quantity-prize.awarded)+' 份','muted'));
      const details = el('details'), editor = el('form'); editor.dataset.prizeEditor = '';
      details.append(el('summary','修改獎品／增加份數'));
      for (const [key,label,type,value] of [['name','獎品名稱','text',prize.name],['quantity','獎品份數','number',prize.quantity]]) {
        const wrapper = el('label',label), input = el('input'); input.name=key;input.type=type;input.value=value;input.required=true;
        if (type === 'number') {input.min=1;input.max=200;input.step=1;input.inputMode='numeric';} else input.maxLength=100;
        wrapper.append(input);editor.append(wrapper);
      }
      const save = el('button','儲存獎品');save.type='submit';save.dataset.write='prize';editor.append(save);
      editor.addEventListener('input',controls);
      editor.addEventListener('submit',async e => {e.preventDefault();await mutate('prize',{prize_id:prize.id,name:editor.elements.name.value,quantity:Number(editor.elements.quantity.value)});});
      details.append(editor); card.append(details);
      const form = el('form',undefined,'raffle-draw-form'), label = el('label','本次抽出人數'), count = el('input');
      count.type='number';count.min=1;count.max=200;count.step=1;count.inputMode='numeric';count.value=1;count.required=true;count.name='count';label.append(count);
      const button = el('button','抽出得主');button.type='submit';button.dataset.draw='';button.dataset.write='draw';form.append(label,button);
      form.addEventListener('submit',e => {e.preventDefault();if(unsavedRules())return;submitDraw({action:'draw',data:{round_id:state.round.id,prize_id:prize.id,count:Number(count.value),request_id:Checkin.requestId()}});});
      card.append(form);list.append(card);
    }
  }
  function renderWinners() {
    const list = $('raffle-winners'); list.replaceChildren();
    if (!state.winners.length) {list.append(el('p','尚無得獎紀錄。','raffle-empty'));return;}
    for (const winner of state.winners) {
      const row=el('article',undefined,'raffle-winner'+(winner.voided_at ? ' voided' : ''));row.dataset.winnerId=winner.id;
      const info=el('div');info.append(el('strong',winner.name),el('p',winner.prize_name+' · '+winner.attendee_id+' · '+(winner.table_no || '未安排')+' 桌'),el('p','紀錄 #'+winner.id+' · '+Checkin.time(winner.drawn_at)+' · '+winner.operator_name,'muted'));
      if(winner.replaces_id)info.append(el('p','替補原紀錄 #'+winner.replaces_id,'muted'));
      if(winner.voided_at)info.append(el('p','已撤銷 · '+winner.void_reason+' · '+Checkin.time(winner.voided_at),'muted'));
      else {
        const actions=el('div',undefined,'raffle-actions'),claim=el('button',winner.claimed_at ? '已領獎 ✓（取消標記）' : '標記領獎');claim.dataset.write='claim';claim.type='button';
        claim.addEventListener('click',() => {if(winner.claimed_at && !confirm('確認取消 '+winner.name+' 的領獎標記？'))return;mutate('claimed',{winner_id:winner.id,claimed:!winner.claimed_at});});actions.append(claim);
        if(!winner.claimed_at && state.round.id===state.latest_round_id){const redraw=el('button','撤銷並重抽','quiet');redraw.type='button';redraw.dataset.write='redraw';redraw.addEventListener('click',() => {redrawWinner=winner;$('redraw-reason').value='';$('redraw-confirm').checked=false;$('redraw-description').textContent=winner.name+' · '+winner.prize_name+'（紀錄 #'+winner.id+'）';$('redraw-dialog').showModal();});actions.append(redraw);}
        if(winner.claimed_at)info.append(el('p','領獎時間：'+Checkin.time(winner.claimed_at),'muted'));
        row.append(actions);
      }
      row.prepend(info);list.append(row);
    }
  }
  function renderDisplay() {
    $('display-round-title').textContent = ['一般抽獎','加碼抽獎'].includes(state.round.title) ? T(state.round.title) : state.round.title;
    const signature=JSON.stringify([state.round.id,state.prizes,state.winners]);
    if(signature===displaySignature)return;
    displaySignature=signature;
    const list=$('raffle-display-winners');list.replaceChildren();
    if(!state.winners.length)list.append(el('p',T('幸運得主即將揭曉，請等候主持人抽獎。'),'raffle-empty'));
    for(const winner of state.winners){const card=el('article',undefined,'raffle-display-winner');card.append(el('p',winner.prize_name),el('strong',winner.card_label),el('p',T(winner.claimed_at ? '已領獎 ✓' : '恭喜中獎')));list.append(card);}
    $('raffle-display-prizes').replaceChildren(...(state.prizes.length ? state.prizes.map(p => el('p',T('{name} · {awarded} / {quantity} 份', {name: p.name, awarded: p.awarded, quantity: p.quantity}))) : [el('p',T('尚未設定獎品，請等候主持人。'),'muted')]));
  }
  if(host){
    root.querySelectorAll('fieldset').forEach(fields => {fields.disabled=true;});
    $('new-round-button').disabled=true;
    $('raffle-config-form').addEventListener('input',controls);
    $('raffle-config-form').addEventListener('submit',async e => {e.preventDefault();await mutate('configure',options());});
    $('prize-add-form').addEventListener('submit',async e => {e.preventDefault();if(await mutate('prize',{name:$('prize-name').value,quantity:Number($('prize-quantity').value)})){$('prize-name').value='';$('prize-quantity').value=1;}});
    $('raffle-round').addEventListener('change',e => {archive=Number(e.target.value)===state.latest_round_id ? null : Number(e.target.value);$('raffle-result').hidden=true;refresh();});
    $('raffle-retry').addEventListener('click',() => submitDraw(pending));
    $('new-round-button').addEventListener('click',() => {roundAtDialog=state.round.id;$('new-round-name').value='加碼抽獎';for(const id of ['new-allow-repeat','new-include-previous','new-exclude-bingo'])$(id).checked=false;$('new-round-confirm').value='';$('new-round-dialog').showModal();});
    $('new-round-form').addEventListener('submit',async e => {e.preventDefault();if(await mutate('round',{round_id:roundAtDialog,...options('new-'),confirmation:$('new-round-confirm').value})){$('new-round-dialog').close();$('raffle-result').hidden=true;}});
    $('redraw-form').addEventListener('submit',e => {e.preventDefault();const data={round_id:redrawWinner.round_id,prize_id:redrawWinner.prize_id,count:1,winner_id:redrawWinner.id,reason:$('redraw-reason').value,confirm:$('redraw-confirm').checked,request_id:Checkin.requestId()};$('redraw-dialog').close();submitDraw({action:'redraw',data});});
    root.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click',() => $(b.dataset.close).close()));
  } else $('raffle-fullscreen').addEventListener('click',async () => {try{if(document.fullscreenElement)await document.exitFullscreen();else if(document.documentElement.requestFullscreen)await document.documentElement.requestFullscreen();else error(T('此瀏覽器不支援全螢幕，請使用投影電腦的全螢幕功能。'));}catch(_){error(T('請在投影電腦啟用瀏覽器全螢幕。'));}});
  window.addEventListener('online',refresh);
  window.addEventListener('offline',() => {online=false;$('raffle-connection').textContent=T('離線，請等待網路恢復');controls();});
  document.addEventListener('visibilitychange',() => {if(document.visibilityState==='visible')refresh();else presenter.finish();});
  reducedMotion.addEventListener('change',() => {if(reducedMotion.matches){presenter.finish();stopReels();}});
  setInterval(() => {if(document.visibilityState==='visible')refresh();},2000);
  refresh();
})();
