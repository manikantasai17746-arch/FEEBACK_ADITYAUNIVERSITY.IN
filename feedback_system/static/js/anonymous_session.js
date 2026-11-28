/* Anonymous session (front-end only).
   The authenticator page remembers the accepted code in this browser tab (sessionStorage),
   tied to the logged-in student. This script then:
     - on the feedback form and "submitted" pages, shows the code as the anonymous ID;
     - on the dashboard, blurs the student's details and counts while the ID is active;
     - "Log out of anonymous ID" clears the ID here, and the form post ends anonymous
       mode on the server. The ID itself is never sent to or stored on the server. */
(function () {
  'use strict';
  var KEY = 'ffs_anon';
  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function readCode(uid) {
    try {
      var o = JSON.parse(sessionStorage.getItem(KEY) || 'null');
      if (!o) return null;
      if (/^\d{6}$/.test(String(o.code)) && String(o.uid) === String(uid)) return String(o.code);
      sessionStorage.removeItem(KEY);              // left over from another student: discard
    } catch (e) {}
    return null;
  }
  function clearCode() { try { sessionStorage.removeItem(KEY); } catch (e) {} }

  function fillTiles(box, code, small) {
    box.textContent = '';
    code.split('').forEach(function (d, i) {
      var t = document.createElement('span');
      t.className = 'as-tile' + (small ? ' as-sm' : '') + (reduced ? '' : ' as-pop');
      t.textContent = d;
      if (!reduced) t.style.animationDelay = (i * 90) + 'ms';
      box.appendChild(t);
    });
  }

  /* ---- feedback form page: ID tag ---- */
  var chip = document.getElementById('asChip');
  if (chip) {
    var c1 = readCode(chip.getAttribute('data-uid'));
    if (c1) {
      fillTiles(chip.querySelector('.as-tiles'), c1, true);
      chip.setAttribute('aria-label', 'Your anonymous ID is ' + c1.split('').join(' '));
      chip.hidden = false;
    }
  }

  /* ---- dashboard: hide details while the anonymous ID is active ---- */
  var bar = document.getElementById('asBar');
  if (bar) {
    var code = readCode(bar.getAttribute('data-uid'));
    if (code) {
      document.body.classList.add('as-active');
      Array.prototype.forEach.call(document.querySelectorAll('.stu-chips, .tiles'),
        function (e) { e.setAttribute('aria-hidden', 'true'); });
      fillTiles(document.getElementById('asTiles'), code, false);
      bar.hidden = false;
    }
  }

  /* ---- "feedback submitted" page: show the ID ---- */
  var done = document.getElementById('asDone');
  if (done) {
    var c2 = readCode(done.getAttribute('data-uid'));
    if (c2) fillTiles(done, c2, false);
  }

  /* ---- log out of the anonymous ID: the digits drop away, then the form is sent ---- */
  Array.prototype.forEach.call(document.querySelectorAll('form[data-as-logout]'), function (form) {
    var sent = false;
    form.addEventListener('submit', function (e) {
      clearCode();
      var tiles = document.querySelectorAll('#asDone .as-tile, #asTiles .as-tile');
      if (reduced || !tiles.length) return;          // send straight away
      e.preventDefault();
      if (sent) return;
      sent = true;
      Array.prototype.forEach.call(tiles, function (t, i) {
        t.classList.remove('as-pop'); t.style.animation = 'none';
        t.style.transitionDelay = (i * 90) + 'ms';
        setTimeout(function () { t.classList.add('as-gone'); }, 20);
      });
      var lead = document.getElementById('asDoneLead');
      var note = document.getElementById('asDoneNote');
      if (lead) lead.textContent = 'Anonymous ID cleared.';
      if (note) note.textContent = 'Taking you back to your account.';
      setTimeout(function () { form.submit(); }, 1300);
    });
  });

  /* ---- progress page: numbers count up ---- */
  Array.prototype.forEach.call(document.querySelectorAll('[data-as-count]'), function (el) {
    var to = parseInt(el.textContent, 10);
    if (isNaN(to) || to <= 0 || reduced) return;
    var n = 0, step = Math.max(1, Math.round(to / 12));
    el.textContent = '0';
    var iv = setInterval(function () {
      n = Math.min(to, n + step);
      el.textContent = String(n);
      if (n === to) clearInterval(iv);
    }, 120);
  });

  /* a real logout also ends the anonymous ID */
  Array.prototype.forEach.call(document.querySelectorAll('a.nav-user[href*="logout"]'), function (a) {
    a.addEventListener('click', clearCode);
  });
})();
