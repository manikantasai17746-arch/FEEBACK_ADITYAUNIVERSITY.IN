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
    var qBox = $('#rfQ'), qClr = $('#rfQx'), emptyMsg = $('#rfEmpty'), flt = 'all', term = '';
    var semSel = $('#rfSem'), sem = '';
    rows.forEach(function (r) { var s = r.querySelector('.rf-sub'); if (s) s.setAttribute('data-orig', s.textContent); });
    function esc(t) { return t.replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
    function mark(el) {                       /* highlight the search term in the subject / faculty line */
      var t = el.getAttribute('data-orig'), i = term ? t.toLowerCase().indexOf(term) : -1;
      el.innerHTML = i < 0 ? esc(t) : esc(t.slice(0, i)) + '<mark>' + esc(t.slice(i, i + term.length)) + '</mark>' + esc(t.slice(i + term.length));
    }
    function drafts() { return $$('input[name=form_ids]:not(:disabled)', rf).filter(function (c) { return !c.closest('tr').hidden; }); }
    function upd() {
      var d = drafts(), n = $$('input[name=form_ids]:checked:not(:disabled)', rf).length;
      pub.disabled = !n; pub.textContent = 'Publish selected (' + n + ')';
      if (all) { all.checked = d.length > 0 && n === d.length; all.indeterminate = n > 0 && n < d.length; }
      rows.forEach(function (r) { var c = r.querySelector('input[name=form_ids]'); r.classList.toggle('rf-sel', !!(c && c.checked && !c.disabled)); });
    }
    function matches(r) {
      return (!sem || r.getAttribute('data-sem') === sem) &&
             (!term || (r.getAttribute('data-q') || '').indexOf(term) > -1);
    }
    function apply() {                        /* status chip + semester + search together; counts follow semester and search */
      var counts = { all: 0, draft: 0, open: 0, closed: 0 }, shown = 0;
      rows.forEach(function (r) {
        var st = r.getAttribute('data-status'), m = matches(r);
        if (m) { counts.all++; if (counts[st] !== undefined) counts[st]++; }
        var vis = m && (flt === 'all' || st === flt);
        r.hidden = !vis; if (vis) shown++;
        var c = r.querySelector('input[name=form_ids]');
        if (c && !vis) c.checked = false;     /* never publish a draft the person cannot see */
        var sub = r.querySelector('.rf-sub'); if (sub) mark(sub);
      });
      chips.forEach(function (c) { var k = c.getAttribute('data-flt'); c.textContent = c.getAttribute('data-lbl') + ' (' + counts[k] + ')'; });
      if (emptyMsg) {
        emptyMsg.hidden = shown > 0;
        emptyMsg.textContent = term ? 'No forms match \u201c' + qBox.value.trim() + '\u201d. Try an employee ID or a subject code or name.'
          : sem ? 'No forms for this semester in this status.' : 'No forms in this status.';
      }
      if (qClr) qClr.hidden = !term;
      upd();
    }
    chips.forEach(function (c) {
      c.addEventListener('click', function () {
        flt = c.getAttribute('data-flt');
        chips.forEach(function (x) { x.setAttribute('aria-pressed', x === c ? 'true' : 'false'); });
        apply();
      });
    });
    if (semSel) semSel.addEventListener('change', function () { sem = semSel.value; apply(); });
    if (qBox) {
      qBox.addEventListener('input', function () { term = qBox.value.trim().toLowerCase(); apply(); });
      qBox.addEventListener('keydown', function (e) { if (e.key === 'Enter') e.preventDefault(); });
      qClr.addEventListener('click', function () { qBox.value = ''; term = ''; apply(); qBox.focus(); });
    }
    /* Close is allowed only before the deadline: a page left open past it disables the button
       (the server refuses too, and closes the form by itself). */
    function lateSweep() {
      rows.forEach(function (r) {
        var dl = r.getAttribute('data-deadline'), b = r.querySelector('[data-act=closed]');
        if (!b || !dl) return;
        var t = Date.parse(dl.length === 10 ? dl + 'T00:00:00Z' : (/[zZ]|[+-]\d\d:?\d\d$/.test(dl) ? dl : dl + 'Z'));
        if (isNaN(t) || t > Date.now()) return;
        b.disabled = true; b.removeAttribute('data-act');
        b.title = 'The deadline has passed. This form closes automatically.';
        var n = document.createElement('span'); n.className = 'rf-note rf-late';
        n.textContent = 'Deadline passed \u2014 closes automatically'; b.parentNode.appendChild(n);
      });
    }
    lateSweep(); setInterval(lateSweep, 30000);
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
    /* Change deadline: a small dialog. "This form only" posts to the form's own deadline route;
       "All Phase N forms" posts to the phase route. HOD department scope is checked on the server. */
    var dlOv = $('#rfDl'), dlForm = $('#rfDlForm'), dlIn = $('#rfDlIn');
    if (dlOv && dlForm && dlIn) {
      /* The page container is its own stacking layer (z-index 1), below the sticky header (z-index 50),
         so a dialog left inside it is partly hidden behind the header. Attach it to <body> instead. */
      document.body.appendChild(dlOv);
      var dlOne = '', dlStatus = '', dlHint = $('#rfDlH'), dlAllW = $('#rfDlAllW');
      var dlClose = function () { dlOv.classList.remove('on'); };
      var dlScope = function () { var r = dlForm.querySelector('input[name=scope]:checked'); return r ? r.value : 'current'; };
      var dlSync = function () {
        var all = dlScope() === 'phase';
        dlForm.action = all ? dlForm.getAttribute('data-phase-url') : dlOne;
        var msg = all ? 'Closed forms in this phase are not changed. Draft and published forms get the new deadline.'
          : (dlStatus === 'closed' ? 'This form is closed. Saving a new future deadline will open it again for students.' : '');
        dlHint.textContent = msg; dlHint.hidden = !msg;
      };
      $$('[data-dl]', rf).forEach(function (b) {
        b.addEventListener('click', function () {
          dlOne = b.getAttribute('data-dl'); dlStatus = b.getAttribute('data-status') || '';
          $('#rfDlS').textContent = b.getAttribute('data-title') || '';
          dlIn.value = b.getAttribute('data-val') || '';
          var ph = b.getAttribute('data-phase') || '';
          $('#rfDlPhase').value = ph;
          $('#rfDlPh').textContent = b.getAttribute('data-phase-label') || '';
          dlAllW.hidden = !ph;                       /* custom-named forms have no phase to apply to */
          dlForm.querySelector('input[name=scope][value=current]').checked = true;
          dlSync(); dlOv.classList.add('on'); dlIn.focus();
        });
      });
      $$('input[name=scope]', dlForm).forEach(function (r) { r.addEventListener('change', dlSync); });
      $('#rfDlNo').addEventListener('click', dlClose);
      dlOv.addEventListener('click', function (e) { if (e.target === dlOv) dlClose(); });
      document.addEventListener('keydown', function (e) { if (e.key === 'Escape') dlClose(); });
    }
    upd();
  }

  /* ---- score bands ---- */
  function initBands(root) {
    var btabs = $$('.band-tab', root), panels = $$('[data-band-panel]', root);
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
  }
  initBands(document);

  var bandSection = document.getElementById('sec-bands');
  if (bandSection) {
    var pendingFilter;
    var renderedSearch = location.search;
    async function loadFilter(url, link) {
      if (pendingFilter) pendingFilter.abort();
      var request = new AbortController();
      pendingFilter = request;
      bandSection.setAttribute('aria-busy', 'true');
      try {
        var response = await fetch(url, { signal: request.signal, credentials: 'same-origin' });
        if (!response.ok || response.redirected) throw new Error('Dashboard unavailable');
        var page = new DOMParser().parseFromString(await response.text(), 'text/html');
        if (request.signal.aborted) return;
        var next = page.getElementById('sec-bands');
        if (!next) throw new Error('Score bands unavailable');
        var restoreFocus = link && document.activeElement === link;
        bandSection.replaceChildren(...next.children);
        initBands(bandSection);
        if (link && url !== location.href) history.pushState(null, '', url);
        renderedSearch = location.search;
        if (restoreFocus) {
          var selected = $$('#bands a', bandSection).find(function (a) {
            return a.textContent === link.textContent && a.href === url;
          });
          if (selected) selected.focus({ preventScroll: true });
        }
      } catch (error) {
        if (!request.signal.aborted) {
          if (url === location.href) location.reload();
          else location.assign(url);
        }
      } finally {
        if (request === pendingFilter) bandSection.removeAttribute('aria-busy');
      }
    }

    bandSection.addEventListener('click', function (e) {
      var link = e.target.closest('#bands a[href]');
      if (!link || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      loadFilter(link.href, link);
    });
    window.addEventListener('popstate', function () {
      if (pendingFilter) pendingFilter.abort();
      if (location.search !== renderedSearch) loadFilter(location.href, null);
    });
  }

  /* ---- logo replay ---- */
  var w = document.getElementById('auLogo');
  if (w) {
    var replay = function () { var s = w.querySelector('svg'); w.replaceChild(s.cloneNode(true), s); };
    w.addEventListener('click', replay);
    w.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); replay(); } });
  }
})();
