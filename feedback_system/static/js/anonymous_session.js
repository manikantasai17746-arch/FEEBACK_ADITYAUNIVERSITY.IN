/* Anonymous session (front-end only).
   The authenticator page remembers the accepted code in this browser tab (sessionStorage),
   tied to the logged-in student. This script then:
     - on the feedback form page, shows the code as the student's anonymous ID tag;
     - on the dashboard, hides the student's details and counts, and offers
       "Log out of anonymous ID". Logging out clears the ID and reveals the details.
   Nothing is sent to the server and nothing is stored there. */
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

  /* ---- dashboard: hide details, offer log out ---- */
  var bar = document.getElementById('asBar');
  if (bar) {
    var code = readCode(bar.getAttribute('data-uid'));
    if (code) {
      var hidden = document.querySelectorAll('.stu-chips, .tiles');
      var tilesBox = document.getElementById('asTiles');
      var sub = document.getElementById('asSub');
      var out = document.getElementById('asOut');
      document.body.classList.add('as-active');
      Array.prototype.forEach.call(hidden, function (e) { e.setAttribute('aria-hidden', 'true'); });
      fillTiles(tilesBox, code, false);
      bar.hidden = false;

      out.addEventListener('click', function () {
        out.disabled = true;
        clearCode();
        var tiles = tilesBox.children;
        Array.prototype.forEach.call(tiles, function (t, i) {      // the digits drop away
          t.classList.remove('as-pop'); t.style.animation = 'none';
          t.style.transitionDelay = (i * 90) + 'ms';
          setTimeout(function () { t.classList.add('as-gone'); }, 20);
        });
        sub.textContent = 'Anonymous ID cleared.';
        setTimeout(reveal, reduced ? 0 : 800);
      });

      function reveal() {
        document.body.classList.remove('as-active');
        Array.prototype.forEach.call(hidden, function (e) { e.removeAttribute('aria-hidden'); });
        bar.classList.add('as-collapse');
        setTimeout(function () { bar.hidden = true; }, reduced ? 0 : 700);
        Array.prototype.forEach.call(document.querySelectorAll('.tiles .num'), countUp);
      }
      function countUp(el) {
        var to = parseInt((el.textContent || '').trim(), 10);
        if (isNaN(to) || to <= 0 || reduced) return;
        var n = 0, step = Math.max(1, Math.round(to / 12));
        el.textContent = '0';
        var iv = setInterval(function () {
          n = Math.min(to, n + step);
          el.textContent = String(n);
          if (n === to) clearInterval(iv);
        }, 60);
      }
    }
  }

  /* a real logout also ends the anonymous ID */
  Array.prototype.forEach.call(document.querySelectorAll('a.nav-user[href*="logout"]'), function (a) {
    a.addEventListener('click', clearCode);
  });
})();
