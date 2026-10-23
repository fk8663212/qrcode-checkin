export const REVEAL_MS = 3000;

// Hold the committed snapshot until its shared server deadline. This module never draws a winner.
export function createRafflePresenter({render, start, stop, reveal, canAnimate,
  schedule = (fn, ms) => setTimeout(fn, ms), cancel = id => clearTimeout(id)}) {
  let visible = null, held = null, timer = null;
  const key = snapshot => snapshot?.latest_draw ? snapshot.round.id + ':' + snapshot.latest_draw.id : null;
  function clear() {
    const active = timer !== null || !!held;
    if (timer !== null) cancel(timer);
    timer = null;
    if (active) stop();
  }
  function finish(celebrate = true) {
    if (!held) return;
    const next = held;
    held = null; clear(); visible = next; render(next);
    reveal(next, {animate:celebrate && canAnimate()});
  }
  return {
    update(next, {elapsedMs = 0} = {}) {
      if (visible && next.round.id === visible.round.id && next.server_time < (held || visible).server_time) return;
      if (!visible || next.round.id !== visible.round.id) {
        held = null; clear(); visible = next; render(next); return;
      }
      if (held && key(next) === key(held)) {
        held = next;
        if (!canAnimate()) finish(false);
        return;
      }
      const previous = held || visible;
      const age = Math.max(0, next.server_time * 1000 - Date.parse(next.latest_draw?.drawn_at));
      const remaining = REVEAL_MS - age - Math.max(0, elapsedMs) / 2;
      const fresh = key(next) && key(next) !== key(previous)
        && next.draw_count === previous.draw_count + 1 && Number.isFinite(remaining) && remaining > 0;
      if (fresh && canAnimate()) {
        held = next; clear(); start(next);
        timer = schedule(() => finish(), remaining);
      } else {
        const changed = key(next) && key(next) !== key(previous);
        held = null; clear(); visible = next; render(next);
        if (changed) reveal(next, {animate:false});
      }
    },
    finish: () => finish(false),
    isAnimating: () => !!held,
  };
}
