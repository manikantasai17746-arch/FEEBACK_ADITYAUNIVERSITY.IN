/* Anonymous-ID animation for the feedback authenticator page (feedback_verify.html).
   Front-end only. The code is still checked by the normal /verify-code route; this script
   sends the same form POST in the background, and plays the animation only when the server
   accepts the code (it redirects on to the feedback form). A wrong code behaves as before:
   the page shows the server's message and lets the student try again. */
(function () {
  'use strict';
  var form = document.querySelector('form[data-fv-form]');
  var card = document.getElementById('fvCard');
  var stage = document.getElementById('fvStage');
  if (!form || !card || !stage || !window.fetch || !window.FormData) return;   // plain form post still works

  var MSG_KEY = 'ffs_verify_msg';
  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var busy = false;
  var timers = [];

  function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
  function clearTimers() { timers.forEach(clearTimeout); timers = []; }
  function $(sel) { return stage.querySelector(sel); }

  /* ---- server message (wrong code, locked out) ---- */
  function flashBox() {
    var box = document.querySelector('.flashes');
    if (!box) {
      box = document.createElement('div');
      box.className = 'flashes';
      var main = document.querySelector('main.container');
      if (main) main.insertBefore(box, main.firstChild);
    }
    return box;
  }
  function showFlash(text, cls) {
    var box = flashBox();
    var el = document.createElement('div');
    el.className = cls || 'flash flash-error';
    el.textContent = text;                       // text only, never markup
    box.appendChild(el);
  }
  try {                                          // message saved just before the page reloaded
    var saved = sessionStorage.getItem(MSG_KEY);
    if (saved) {
      sessionStorage.removeItem(MSG_KEY);
      var o = JSON.parse(saved);
      if (o && typeof o.text === 'string') showFlash(o.text, o.cls === 'flash flash-warning' ? o.cls : 'flash flash-error');
    }
  } catch (e) {}

  /* ---- the animation ---- */
  function play(code, dest) {
    try {                                        // remembered in this tab for the anonymous session
      sessionStorage.setItem('ffs_anon', JSON.stringify({ code: code, uid: stage.getAttribute('data-uid') || '' }));
    } catch (e) {}
    card.classList.add('fv-playing');
    document.body.classList.add('fv-anon');      // hide the account bar and messages
    stage.hidden = false;

    var tiles = $('.fv-tiles');
    tiles.textContent = '';
    code.split('').forEach(function (d) {
      var t = document.createElement('span');
      t.className = 'fv-tile';
      t.textContent = d;
      tiles.appendChild(t);
    });
    var go = $('.fv-go');
    go.onclick = function () { window.location.assign(dest); };

    if (reduced) {                               // no motion: show the end state, then continue
      Array.prototype.forEach.call(tiles.children, function (t) { t.classList.add('fv-in'); });
      stage.classList.add('fv-on', 'fv-showid', 'fv-done');
      later(function () { window.location.assign(dest); }, 2500);
      return;
    }
    later(function () { stage.classList.add('fv-on'); }, 30);
    later(function () { stage.classList.add('fv-showid'); }, 500);
    Array.prototype.forEach.call(tiles.children, function (t, i) {         // the code drops in as the ID
      later(function () { t.classList.add('fv-in'); }, 650 + i * 120);
    });
    later(function () { stage.classList.add('fv-done'); }, 1500);
    later(function () { window.location.assign(dest); }, 3300);
  }

  function reset() {
    clearTimers();
    busy = false;
    card.classList.remove('fv-playing');
    document.body.classList.remove('fv-anon');
    stage.hidden = true;
    stage.className = 'fv-stage';
  }
  window.addEventListener('pageshow', function (e) { if (e.persisted) reset(); });   // Back button

  /* ---- submit ---- */
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (busy) return;
    var input = form.elements['code'];
    var code = ((input && input.value) || '').replace(/\D/g, '');
    if (code.length !== 6) { if (form.reportValidity) form.reportValidity(); return; }
    busy = true;

    var nextField = form.elements['next'];
    var nextPath = null;
    try { nextPath = new URL((nextField && nextField.value) || '', window.location.href).pathname; } catch (err) {}

    fetch(form.getAttribute('action') || window.location.href, {
      method: 'POST', body: new FormData(form), credentials: 'same-origin', redirect: 'follow'
    }).then(function (res) {
      if (res.redirected) {
        var dest = new URL(res.url);
        if (dest.origin === window.location.origin && nextPath && dest.pathname === nextPath) {
          play(code, res.url);                   // accepted: the server sent us on to the feedback form
        } else {
          window.location.assign(res.url);       // sent somewhere else (for example, session ended)
        }
        return null;
      }
      return res.text().then(function (html) {   // not accepted: take the server's message and start over
        var msg = '', cls = 'flash flash-error';
        try {
          var doc = new DOMParser().parseFromString(html, 'text/html');
          var f = doc.querySelector('.flashes .flash');
          if (f) { msg = (f.textContent || '').trim(); if (f.classList.contains('flash-warning')) cls = 'flash flash-warning'; }
        } catch (err) {}
        try { sessionStorage.setItem(MSG_KEY, JSON.stringify({ text: msg || 'That code is not correct. Check your authenticator app and try again.', cls: cls })); } catch (err) {}
        window.location.reload();                // fresh page: the code box and auto-submit work again
      });
    }).catch(function () {
      busy = false;                              // network problem: nothing was checked
      showFlash('Could not reach the server. Check your connection and try again.', 'flash flash-error');
    });
  });
})();
