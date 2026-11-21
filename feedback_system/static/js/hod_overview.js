/* HOD Overview: section tabs, recent forms, score bands, lazy panels, logo replay */
(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return [].slice.call((r || document).querySelectorAll(s)); };

  /* ---- section tabs ---- */
  var tabs = $$('.sec-tab');
  function lazy(k) {
    var m = document.getElementById(k === 'pending' ? 'pdMount' : k === 'results' ? 'rsMount' : '');
    if (!m || m.getAttribute('data-loaded')) return;
    m.setAttribute('data-loaded', '1');
    fetch(m.getAttribute('data-src'), { credentials: 'same-origin' })
      .then(function (r) { return r.text(); })
      .then(function (h) {
        var d = new DOMParser().parseFromString(h, 'text/html');
        var main = d.querySelector('main.container') || d.body;
        $$('.flashes,script,style', main).forEach(function (n) { n.remove(); });
        m.innerHTML = main.innerHTML +
          '<p class="sub" style="margin-top:1rem"><a href="' + m.getAttribute('data-src') + '">Open full page</a></p>';
      })
      .catch(function () {
        m.innerHTML = '<p class="sub">Could not load. <a href="' + m.getAttribute('data-src') + '">Open full page</a></p>';
      });
  }
  function show(k, keep) {
    tabs.forEach(function (t) {
      var on = t.getAttribute('data-sec') === k;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      document.getElementById('sec-' + t.getAttribute('data-sec')).hidden = !on;
    });
    lazy(k);
    if (!keep) { try { sessionStorage.setItem('ov_sec', k); } catch (e) {} }
  }
  tabs.forEach(function (t, i) {
    t.addEventListener('click', function () { show(t.getAttribute('data-sec')); });
    t.addEventListener('keydown', function (e) {
      var n = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : null;
      if (n === null) return;
      n = (n + tabs.length) % tabs.length; tabs[n].focus(); show(tabs[n].getAttribute('data-sec')); e.preventDefault();
    });
  });
  if (tabs.length) {
    var q = location.search + location.hash, k = 'recent';
    if (/pending/.test(q)) k = 'pending'; else if (/band/.test(q)) k = 'bands'; else if (/results/.test(q)) k = 'results';
    else { try { k = sessionStorage.getItem('ov_sec') || k; } catch (e) {} }
    show(k, true);
  }

  /* ---- recent forms ---- */
  var rf = document.getElementById('rfForm');
  if (rf) {
    var rows = $$('tbody tr', rf), chips = $$('.rf-chip', rf), all = $('#rfAll'), pub = $('#rfPubSel');
    function drafts() { return $$('input[name=form_ids]:not(:disabled)', rf); }
    function upd() {
      var d = drafts(), n = d.filter(function (c) { return c.checked; }).length;
      pub.disabled = !n; pub.textContent = 'Publish selected (' + n + ')';
      if (all) { all.checked = d.length > 0 && n === d.length; all.indeterminate = n > 0 && n < d.length; }
      rows.forEach(function (r) { var c = r.querySelector('input[name=form_ids]'); r.classList.toggle('rf-sel', !!(c && c.checked && !c.disabled)); });
    }
    chips.forEach(function (c) {
      c.addEventListener('click', function () {
        var f = c.getAttribute('data-flt');
        chips.forEach(function (x) { x.setAttribute('aria-pressed', x === c ? 'true' : 'false'); });
        rows.forEach(function (r) { r.hidden = f !== 'all' && r.getAttribute('data-status') !== f; });
      });
    });
    if (all) all.addEventListener('change', function () { drafts().forEach(function (c) { c.checked = all.checked; }); upd(); });
    rf.addEventListener('change', function (e) { if (e.target.name === 'form_ids') upd(); });

    var ov = $('#rfOv'), pend = null;
    function ask(title, msg, go) {
      $('#rfMt').textContent = title; $('#rfMs').textContent = msg; pend = go; ov.classList.add('on'); $('#rfYes').focus();
    }
    function close() { ov.classList.remove('on'); pend = null; }
    $('#rfNo').addEventListener('click', close);
    ov.addEventListener('click', function (e) { if (e.target === ov) close(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
    $('#rfYes').addEventListener('click', function () { var g = pend; close(); if (g) g(); });

    rf.addEventListener('submit', function (e) {
      if (rf.getAttribute('data-ok')) return;
      e.preventDefault();
      var n = drafts().filter(function (c) { return c.checked; }).length;
      if (!n) return;
      ask('Publish to students', 'Publish ' + n + ' form' + (n > 1 ? 's' : '') + ' to students?', function () {
        var body = new FormData(rf), data = { form_ids: body.getAll('form_ids') };
        fetch(rf.action, {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': (document.querySelector('meta[name=csrf-token]') || {}).content || '' },
          body: JSON.stringify(data)
        }).then(function () { location.reload(); }).catch(function () { location.reload(); });
      });
    });
    $$('[data-act]', rf).forEach(function (b) {
      b.addEventListener('click', function () {
        var st = b.getAttribute('data-act'), fid = b.getAttribute('data-fid');
        var label = b.textContent.trim();
        ask(label, label + ' — ' + b.getAttribute('data-title') + '?', function () {
          var f = $('#rfStatusForm');
          f.action = '/hod/forms/' + fid + '/status'; $('#rfStatusVal').value = st; f.submit();
        });
      });
    });
    upd();
  }

  /* ---- score bands ---- */
  var btabs = $$('.band-tab'), panels = $$('[data-band-panel]');
  function showBand(n) {
    btabs.forEach(function (t) { t.classList.toggle('active', t.getAttribute('data-band') === String(n)); });
    panels.forEach(function (p) { p.hidden = p.getAttribute('data-band-panel') !== String(n); });
    try { sessionStorage.setItem('ov_band', n); } catch (e) {}
  }
  btabs.forEach(function (t) {
    t.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); showBand(t.getAttribute('data-band')); });
  });
  panels.forEach(function (p) {
    var all = $('.band-all', p); if (!all) return;
    var cbs = $$('.band-cb', p), msg = $('.band-message', p), subj = $('.band-subject', p),
        defM = msg.value, defS = subj.value, info = $('.band-count-sel', p);
    function upd() {
      var n = cbs.filter(function (c) { return c.checked; }).length;
      all.checked = cbs.length > 0 && n === cbs.length; all.indeterminate = n > 0 && n < cbs.length;
      info.textContent = n + ' of ' + cbs.length + ' with email selected';
    }
    all.addEventListener('change', function () { cbs.forEach(function (c) { c.checked = all.checked; }); upd(); });
    cbs.forEach(function (c) { c.addEventListener('change', upd); });
    $('.band-reset', p).addEventListener('click', function () { msg.value = defM; subj.value = defS; });
    upd();
  });
  if (btabs.length) {
    var sv = null; try { sv = sessionStorage.getItem('ov_band'); } catch (e) {}
    if (sv && btabs.some(function (t) { return t.getAttribute('data-band') === sv; })) showBand(sv);
  }

  /* ---- logo replay ---- */
  var w = document.getElementById('auLogo');
  if (w) {
    var replay = function () { var s = w.querySelector('svg'); w.replaceChild(s.cloneNode(true), s); };
    w.addEventListener('click', replay);
    w.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); replay(); } });
  }
})();
