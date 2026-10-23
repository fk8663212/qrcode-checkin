import test from 'node:test';
import assert from 'node:assert/strict';
import {createRafflePresenter, REVEAL_MS} from '../checkin/static/raffle/animation.mjs';

function snapshot(count, {round=1, age=0, serverTime=1000, claimed=false}={}) {
  return {round:{id:round}, server_time:serverTime, draw_count:count,
    latest_draw:count ? {id:count, drawn_at:new Date(serverTime*1000-age).toISOString(),count:1,prize_name:'禮券'} : null,
    winners:count ? [{draw_id:count,claimed_at:claimed ? 'confirmed' : null}] : []};
}
function harness() {
  let clock=0, sequence=0, motion=true;
  const tasks=new Map(), renders=[], starts=[], reveals=[];
  const presenter=createRafflePresenter({render:value=>renders.push(value),start:value=>starts.push(value),stop:()=>{},
    reveal:(value,options)=>reveals.push({value,...options}),canAnimate:()=>motion,
    schedule:(fn,ms)=>{const id=++sequence;tasks.set(id,{at:clock+ms,fn});return id;},cancel:id=>tasks.delete(id)});
  function advance(ms) {
    const end=clock+ms;
    while(true){const next=[...tasks.entries()].filter(([,value])=>value.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;clock=next[1].at;tasks.delete(next[0]);next[1].fn();}
    clock=end;
  }
  return {presenter,renders,starts,reveals,advance,reduce:()=>{motion=false;}};
}

test('initial load shows existing awards without replaying an old draw',()=>{
  const h=harness();h.presenter.update(snapshot(4));
  assert.equal(h.renders.length,1);assert.equal(h.starts.length,0);assert.equal(h.reveals.length,0);
});
test('fresh draw holds winners until the server deadline and reveals once',()=>{
  const h=harness();h.presenter.update(snapshot(0));h.presenter.update(snapshot(1));
  assert.equal(h.presenter.isAnimating(),true);assert.equal(h.renders.at(-1).draw_count,0);
  h.advance(REVEAL_MS-1);assert.equal(h.renders.length,1);
  h.advance(1);assert.equal(h.renders.at(-1).draw_count,1);assert.equal(h.reveals.length,1);assert.equal(h.reveals[0].animate,true);
});
test('polling preserves the deadline and newest committed claim status',()=>{
  const h=harness();h.presenter.update(snapshot(0));h.presenter.update(snapshot(1));h.advance(1000);
  h.presenter.update(snapshot(1,{serverTime:1001,age:1000,claimed:true}));
  h.advance(REVEAL_MS-1000);assert.equal(h.renders.at(-1).winners[0].claimed_at,'confirmed');assert.equal(h.starts.length,1);
  h.presenter.update(snapshot(1,{serverTime:1004,age:4000,claimed:true}));assert.equal(h.reveals.length,1);
});
test('age and network latency shorten the countdown using server time',()=>{
  const h=harness();h.presenter.update(snapshot(0));h.presenter.update(snapshot(1,{age:1000}),{elapsedMs:200});
  h.advance(1899);assert.equal(h.presenter.isAnimating(),true);
  h.advance(1);assert.equal(h.presenter.isAnimating(),false);
});
test('an older in-flight response cannot cancel a newer reveal',()=>{
  const h=harness();h.presenter.update(snapshot(0));h.presenter.update(snapshot(1,{serverTime:1001}));
  h.presenter.update(snapshot(0,{serverTime:1000.5}));assert.equal(h.presenter.isAnimating(),true);
  h.advance(REVEAL_MS);assert.equal(h.renders.at(-1).draw_count,1);
});
test('new round cancels the old countdown and cannot reveal its old winners',()=>{
  const h=harness();h.presenter.update(snapshot(0));h.presenter.update(snapshot(1));h.presenter.update(snapshot(0,{round:2}));
  h.advance(REVEAL_MS);assert.equal(h.renders.at(-1).round.id,2);assert.equal(h.reveals.length,0);
});
test('catch-up and expired results display immediately without a replay',()=>{
  for(const next of [snapshot(3),snapshot(1,{age:REVEAL_MS+1})]){
    const h=harness();h.presenter.update(snapshot(0));h.presenter.update(next);
    assert.equal(h.starts.length,0);assert.equal(h.renders.at(-1),next);assert.equal(h.reveals[0].animate,false);
  }
});
test('reduced motion shows only committed results and no rolling phase',()=>{
  const h=harness();h.reduce();h.presenter.update(snapshot(0));h.presenter.update(snapshot(1));
  assert.equal(h.starts.length,0);assert.equal(h.reveals[0].animate,false);assert.equal(h.presenter.isAnimating(),false);
});
test('backgrounding finishes the reveal without celebration and never replays it',()=>{
  const h=harness();h.presenter.update(snapshot(0));h.presenter.update(snapshot(1));h.presenter.finish();
  assert.equal(h.renders.at(-1).draw_count,1);assert.equal(h.reveals[0].animate,false);
  h.advance(REVEAL_MS);h.presenter.update(snapshot(1,{age:REVEAL_MS+1}));assert.equal(h.reveals.length,1);
});
test('a newer draw during a roll cancels its prior timer and reveals the newest snapshot',()=>{
  const h=harness();h.presenter.update(snapshot(0));h.presenter.update(snapshot(1));h.advance(1000);
  h.presenter.update(snapshot(2,{serverTime:1001}));h.advance(REVEAL_MS-1000);assert.equal(h.reveals.length,0);
  h.advance(1000);assert.equal(h.reveals.length,1);assert.equal(h.reveals[0].value.draw_count,2);
});
