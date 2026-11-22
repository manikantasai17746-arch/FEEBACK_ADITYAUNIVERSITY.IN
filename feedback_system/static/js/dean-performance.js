/* Dean > Faculty Performance interactions (CSP safe: external file, no inline handlers).
   - View (grid + band cards) opens the teacher's result in <dialog id="result-dialog">.
   - Mail teacher / Email HOD about ticked teachers / the inline composer post with fetch()
     and show the server's own result (what was delivered, skipped, created).
   Every dialog works without trusting the page: the server re-resolves scope and HODs. */
(function () {
  'use strict';
  var doc = document;
  function $(sel, root) { return (root || doc).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); }
  function token() {
    try { if (window.crypto && crypto.randomUUID) return crypto.randomUUID(); } catch (e) {}
    return 't' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  /* ---------- band checkboxes (moved here from an inline script) ---------- */
  var all = $('#band-all'), cbs = $$('.band-cb');
  if (all) {
    all.addEventListener('change', function () { cbs.forEach(function (c) { c.checked = all.checked; }); });
    cbs.forEach(function (c) {
      c.addEventListener('change', function () { all.checked = cbs.every(function (x) { return x.checked; }); });
    });
  }
  var msg = $('#band-message'), defMsg = msg ? msg.value : '';
  var reset = $('#band-reset');
  if (reset && msg) reset.addEventListener('click', function () { msg.value = defMsg; });

  /* ---------- shared: result box (textContent only) ---------- */
  function showResult(box, data) {
    box.hidden = false;
    box.className = 'mail-result is-' + (data.level || 'error');
    box.textContent = '';
    var p = doc.createElement('p');
    p.textContent = data.message || 'Something went wrong.';
    box.appendChild(p);
    if (data.duplicate) {
      var d = doc.createElement('p');
      d.className = 'mail-dup';
      d.textContent = 'This was already submitted. Nothing was sent or created a second time.';
      box.appendChild(d);
    }
    if (data.groups && data.groups.length) {
      var ul = doc.createElement('ul');
      data.groups.forEach(function (g) {
        var li = doc.createElement('li');
        var st = { sent: 'delivered', failed: 'not delivered', no_email: 'no email on file' }[g.status] || g.status;
        li.textContent = g.hod + ' (' + g.teachers.join(', ') + ') \u2014 ' + st;
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }
    if (data.actions_url) {
      var a = doc.createElement('a');
      a.className = 'mail-link';
      a.href = data.actions_url;
      a.textContent = 'View in Actions';
      box.appendChild(a);
    }
  }
  function post(form, extra, box, btn, done) {
    var fd = new FormData(form);
    Object.keys(extra || {}).forEach(function (k) { fd.set(k, extra[k]); });
    btn.disabled = true; btn.setAttribute('aria-busy', 'true');
    box.hidden = false; box.className = 'mail-result is-info'; box.textContent = 'Sending\u2026';
    fetch(form.dataset.url || form.action, {
      method: 'POST', body: fd, credentials: 'same-origin',
      headers: { 'X-Requested-With': 'fetch', 'Accept': 'application/json' }
    }).then(function (r) {
      return r.json().then(function (j) { return j; }, function () {
        return { level: 'error', message: 'The server answered with an unexpected response (' + r.status + '). Nothing is confirmed as sent.' };
      });
    }).then(function (j) { showResult(box, j); if (done) done(j); },
      function () {
        showResult(box, { level: 'error', message: 'Could not reach the server. Nothing is confirmed as sent; check your connection before trying again.' });
      }).then(function () { btn.disabled = false; btn.removeAttribute('aria-busy'); });
  }

  /* ---------- generic dialog helpers ---------- */
  var lastTrigger = null;
  function openDialog(dlg, trigger) {
    lastTrigger = trigger || null;
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    doc.documentElement.classList.add('rd-lock');
  }
  function bindDialog(dlg) {
    dlg.addEventListener('close', function () {
      doc.documentElement.classList.remove('rd-lock');
      if (lastTrigger && doc.contains(lastTrigger)) { try { lastTrigger.focus({ preventScroll: true }); } catch (e) {} }   // keep the page where it was
    });
    dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });   // backdrop
  }

  /* ---------- result popup ---------- */
  var rd = $('#result-dialog');
  if (rd) {
    bindDialog(rd);
    var slot = $('[data-rd-slot]', rd), seq = 0;
    doc.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[data-result-url]');
      if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
      e.preventDefault();
      var mine = ++seq;                                // only the latest click may open the popup
      fetch(a.dataset.resultUrl, { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (html) {
          if (mine !== seq || rd.open) return;         // superseded by a newer click, or already open
          slot.innerHTML = html;                       // server-rendered, auto-escaped template
          wireResult(slot);
          openDialog(rd, a);
          var x = $('[data-rd-close]', slot); if (x) x.focus();
          slot.scrollTop = 0;
        })
        .catch(function () { if (mine === seq) window.location.href = a.href; });   // full page still works
    });
  }
  function wireResult(root) {
    $$('img[data-fallback]', root).forEach(function (img) {
      img.addEventListener('error', function () {
        img.hidden = true;
        var s = img.nextElementSibling; if (s) s.hidden = false;
      });
    });
    $$('[data-rd-close]', root).forEach(function (b) { b.addEventListener('click', function () { rd.close(); }); });
    var pr = $('[data-rd-print]', root);
    if (pr) pr.addEventListener('click', function () {
      var cls = doc.documentElement.classList;
      cls.add('rd-printing');
      var off = function () { cls.remove('rd-printing'); window.removeEventListener('afterprint', off); };
      window.addEventListener('afterprint', off);
      window.print();
    });
    $$('.rd-card', root).forEach(function (card) {
      var list = $('[data-comments]', card); if (!list) return;
      var items = $$('.rd-c', list), more = $('[data-more]', card), empty = $('[data-empty]', card);
      var per = parseInt($('.rd', root).dataset.page, 10) || 5, shown = per, flt = 'all';
      function draw() {
        var match = items.filter(function (c) { return flt === 'all' || c.dataset.tag === flt; });
        items.forEach(function (c) { c.hidden = true; });
        match.slice(0, shown).forEach(function (c) { c.hidden = false; });
        empty.hidden = match.length > 0;
        var left = match.length - shown;
        more.hidden = left <= 0;
        more.textContent = 'Show more comments (' + left + ' left)';
      }
      $$('.rd-filters button', card).forEach(function (b) {
        b.addEventListener('click', function () {
          flt = b.dataset.f; shown = per;
          $$('.rd-filters button', card).forEach(function (o) {
            var on = o === b; o.classList.toggle('on', on); o.setAttribute('aria-pressed', on ? 'true' : 'false');
          });
          draw();
        });
      });
      more.addEventListener('click', function () { shown += per; draw(); });
      draw();
    });
  }

  /* ---------- mail dialog (one teacher OR the ticked teachers' HODs) ---------- */
  var md = $('#mail-dialog'), band = $('#band-mail-form');
  function actionToggle(scope) {
    var t = $('[data-act-toggle]', scope), f = $('[data-act-fields]', scope);
    if (t && f) t.addEventListener('change', function () { f.hidden = !t.checked; });
  }
  if (band) actionToggle(band);
  if (md) {
    bindDialog(md);
    actionToggle(md);
    var mf = $('[data-mail-form]', md), box = $('[data-result]', md), send = $('[data-md-send]', md);
    var tokenInput = mf.elements.submit_token;
    function fresh() { tokenInput.value = token(); }
    ['input', 'change'].forEach(function (ev) { mf.addEventListener(ev, fresh); });
    $$('[data-md-close]', md).forEach(function (b) { b.addEventListener('click', function () { md.close(); }); });
    function setIds(ids) {
      $$('input[name=form_id]', mf).forEach(function (i) { i.remove(); });
      ids.forEach(function (id) {
        var i = doc.createElement('input'); i.type = 'hidden'; i.name = 'form_id'; i.value = id; mf.appendChild(i);
      });
    }
    function openMail(mode, cards, trigger) {
      box.hidden = true; box.textContent = '';
      var act = $('[data-act-toggle]', md); act.checked = false; $('[data-act-fields]', md).hidden = true;
      var ownerWrap = $('[data-owner-wrap]', md), owner = $('[data-owner]', md), hint = $('[data-md-hint]', md);
      owner.textContent = '';
      if (mode === 'teacher') {
        var c = cards[0];
        mf.dataset.url = mf.getAttribute('action');
        $('[data-md-title]', md).textContent = 'Mail teacher \u2014 ' + c.dataset.teacher;
        $('[data-md-to]', md).textContent = 'To: ' + c.dataset.teacher + ' <' + c.dataset.email + '>';
        mf.elements.subject.value = $('#band-subject').value;
        mf.elements.message.value = defMsg;
        hint.textContent = 'Placeholders: {name} {score} {subject} {dean} {hod}';
        var opts = []; try { opts = JSON.parse(c.dataset.owners || '[]'); } catch (e) {}
        opts.forEach(function (o) {
          var op = doc.createElement('option'); op.value = o.id; op.textContent = o.name;
          if (String(o.id) === c.dataset.hodId) op.selected = true; owner.appendChild(op);
        });
        ownerWrap.hidden = !opts.length;
        if (!opts.length) { owner.disabled = true; } else { owner.disabled = false; }
        setIds([c.dataset.form]);
      } else {
        mf.dataset.url = band.dataset.hodUrl;
        var groups = {}, order = [], none = 0;
        cards.forEach(function (c) {
          if (!c.dataset.hodId) { none++; return; }
          if (!groups[c.dataset.hodId]) { groups[c.dataset.hodId] = { name: c.dataset.hod, t: [] }; order.push(c.dataset.hodId); }
          groups[c.dataset.hodId].t.push(c.dataset.teacher);
        });
        $('[data-md-title]', md).textContent = 'Email HOD about ticked teachers';
        var to = $('[data-md-to]', md); to.textContent = '';
        var ul = doc.createElement('ul'); ul.className = 'md-groups';
        order.forEach(function (id) {
          var li = doc.createElement('li');
          li.textContent = groups[id].name + ' \u2014 ' + groups[id].t.join(', '); ul.appendChild(li);
        });
        to.appendChild(doc.createTextNode(order.length + ' HOD(s) will each get one email about their own teachers:'));
        to.appendChild(ul);
        if (none) {
          var w = doc.createElement('p'); w.className = 'md-warn';
          w.textContent = none + ' ticked teacher(s) have no HOD on record and will be skipped.'; to.appendChild(w);
        }
        mf.elements.subject.value = band.dataset.hodSubject;
        mf.elements.message.value = band.dataset.hodMessage;
        hint.textContent = 'Placeholders: {hod} {dean} {count} {teachers}. The teacher list is always added.';
        ownerWrap.hidden = true; owner.disabled = true;
        setIds(cards.map(function (c) { return c.dataset.form; }));
      }
      fresh();
      openDialog(md, trigger);
      mf.elements.subject.focus();
    }
    doc.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('[data-mail-teacher]');
      if (b) { openMail('teacher', [b.closest('.band-card')], b); }
    });
    var hodBtn = $('[data-open-hod]');
    if (hodBtn) hodBtn.addEventListener('click', function () {
      var ticked = cbs.filter(function (c) { return c.checked; }).map(function (c) { return c.closest('.band-card'); });
      var rb = $('[data-result]', band);
      if (!ticked.length) {
        showResult(rb, { level: 'warning', message: 'Tick at least one teacher first.' }); return;
      }
      rb.hidden = true;
      openMail('hod', ticked, hodBtn);
    });
    mf.addEventListener('submit', function (e) {
      e.preventDefault();
      if (!mf.elements.subject.value.trim()) { showResult(box, { level: 'error', message: 'Enter a subject.' }); mf.elements.subject.focus(); return; }
      if (!mf.elements.message.value.trim()) { showResult(box, { level: 'error', message: 'Enter a message.' }); mf.elements.message.focus(); return; }
      if (!owner_ok(mf)) return;
      post(mf, null, box, send);
    });
    function owner_ok(form) {
      var act = $('[data-act-toggle]', form);
      var due = form.elements.due_date && form.elements.due_date.value;
      if (act.checked && due && due < form.elements.due_date.min) {
        showResult(box, { level: 'error', message: 'The due date cannot be in the past.' }); return false;
      }
      return true;
    }
  }

  /* ---------- inline composer: send to all ticked teachers ---------- */
  if (band) {
    var rbox = $('[data-result]', band), sbtn = $('[data-send-teachers]', band);
    var tin = doc.createElement('input'); tin.type = 'hidden'; tin.name = 'submit_token'; tin.value = token(); band.appendChild(tin);
    ['input', 'change'].forEach(function (ev) { band.addEventListener(ev, function () { tin.value = token(); }); });
    band.addEventListener('submit', function (e) {
      e.preventDefault();
      var n = cbs.filter(function (c) { return c.checked; }).length;
      if (!n) { showResult(rbox, { level: 'warning', message: 'Tick at least one teacher first.' }); return; }
      if (!window.confirm('Send this email to ' + n + ' ticked teacher' + (n === 1 ? '' : 's') + '?')) return;
      post(band, null, rbox, sbtn);
    });
  }
})();
