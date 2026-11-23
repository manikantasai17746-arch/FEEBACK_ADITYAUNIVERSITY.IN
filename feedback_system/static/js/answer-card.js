// Answer card on the landing page: a fake cursor taps ratings on its own,
// the stamp slams down on each pick, and the card follows the visitor's mouse once they move it.
(function () {
  var sheet = document.getElementById('demo'), box = document.getElementById('float'), cur = document.getElementById('fcur');
  var bubbles = Array.prototype.slice.call(sheet.querySelectorAll('.bubble'));
  var idle = document.getElementById('receiptIdle'), body = document.getElementById('receiptBody'), label = document.getElementById('pickLabel');
  var touched = false, timers = [], step = 0;
  var q = document.getElementById('demoQ');
  q.innerHTML = q.textContent.trim().split(/\s+/).map(function (w, i) { return '<span class="w" style="--i:' + i + '">' + w + '</span>'; }).join(' ');
  bubbles.forEach(function (b, n) { b.style.setProperty('--n', n); });
  function replay(el, c) { el.classList.remove(c); void el.offsetWidth; el.classList.add(c); }
  var ORDER = [3, 4, 2, 1, 0];          // Very good, Excellent, Good, Average, Poor
  var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
  function clearAll() { timers.forEach(clearTimeout); timers = []; }
  function stamp() { sheet.classList.remove('is-stamped'); void sheet.offsetWidth; sheet.classList.add('is-stamped'); }
  function pick(i) {
    bubbles.forEach(function (b, j) { b.setAttribute('aria-checked', j === i ? 'true' : 'false'); b.tabIndex = j === i ? 0 : -1; });
    label.textContent = bubbles[i].getAttribute('data-label');
    idle.hidden = true; body.hidden = false; stamp();
    replay(label, 'swap'); replay(body, 'redo');
  }
  function moveTo(el) {            // put the arrow tip on the middle of the circle
    var a = box.getBoundingClientRect(), r = el.querySelector('i').getBoundingClientRect();
    cur.style.transform = 'translate(' + (r.left - a.left + r.width / 2) + 'px,' + (r.top - a.top + r.height / 2) + 'px)';
  }
  function clearHover() { bubbles.forEach(function (b) { b.classList.remove('fake-hover'); }); }
  function auto() {
    if (touched) return;
    var i = ORDER[step % ORDER.length]; step++;
    var b = bubbles[i];
    moveTo(b);                                           // cursor glides over
    later(function () { clearHover(); b.classList.add('fake-hover'); }, 850);
    later(function () { cur.classList.add('press'); pick(i); b.classList.remove('fake-hover'); }, 1150);   // click
    later(function () { cur.classList.remove('press'); }, 1350);
    later(auto, 3200);
  }
  function start() {
    clearAll(); touched = false; step = 0; clearHover(); following = false; box.classList.remove('follow'); box.style.animationPlayState = ''; cur.classList.remove('press');
    idle.hidden = false; body.hidden = true; sheet.classList.remove('is-stamped'); replay(sheet, 'intro');
    bubbles.forEach(function (b) { b.setAttribute('aria-checked', 'false'); });
    var a = box.getBoundingClientRect();
    cur.style.transition = 'none';                         // start below the card, then glide in
    cur.style.transform = 'translate(' + (a.width * .78) + 'px,' + (a.height + 30) + 'px)';
    void cur.offsetWidth; cur.style.transition = ''; cur.classList.add('show');
    later(auto, 2100);
  }
  var following = false;
  function follow(e) { var a = box.getBoundingClientRect(); cur.style.transform = 'translate(' + (e.clientX - a.left) + 'px,' + (e.clientY - a.top) + 'px)'; }
  box.addEventListener('pointermove', function (e) {         // the visitor takes over: the A becomes their cursor
    if (e.pointerType !== 'mouse') return;
    if (!following) {
      following = true; touched = true; clearAll(); clearHover(); cur.classList.remove('press');
      box.classList.add('follow'); box.style.animationPlayState = 'paused';
      cur.classList.add('show'); cur.style.transition = 'transform .25s ease-out';
    } else { cur.style.transition = 'transform .03s linear'; }
    follow(e);
  });
  box.addEventListener('pointerleave', function () { if (following) { box.classList.remove('follow'); cur.classList.remove('show'); } });
  box.addEventListener('pointerenter', function (e) { if (following && e.pointerType === 'mouse') { box.classList.add('follow'); cur.classList.add('show'); } });
  box.addEventListener('pointerdown', function () { if (following) cur.classList.add('press'); });
  window.addEventListener('pointerup', function () { if (following) cur.classList.remove('press'); });
  bubbles.forEach(function (b, i) {
    b.addEventListener('click', function () {
      if (!following) {                                       // touch or keyboard: the A taps the circle, then leaves
        touched = true; clearAll(); clearHover(); moveTo(b);
        setTimeout(function () { cur.classList.add('press'); setTimeout(function () { cur.classList.remove('press'); cur.classList.remove('show'); }, 320); }, 800);
      }
      pick(i);
    });
  });
  if (!calm) start(); else { pick(3); }
})();
