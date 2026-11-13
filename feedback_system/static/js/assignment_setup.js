/* Phase 3 — Teacher Assignment Setup wizard.
 * The server renders every assignment; this script only handles step navigation,
 * client-side checks, the review summary and the single atomic save.  The server
 * re-validates everything and decides what the logged-in teacher may change. */
(function () {
  'use strict';
  var root = document.getElementById('tas');
  if (!root) { return; }

  var csrf = (document.querySelector('meta[name=csrf-token]') || {}).content || '';
  var saveUrl = root.dataset.apiSave;
  var dashboardUrl = root.dataset.dashboard;
  var roomMax = parseInt(root.dataset.roomMax, 10) || 20;
  var ROOM_RE = /^[A-Za-z0-9 \-\/_.,()#&]+$/;
  var LAB_TEXT = { available: 'Lab Available', none: 'No Lab', '': 'Not configured' };

  var panels = root.querySelectorAll('.tas-panel');
  var stepBtns = root.querySelectorAll('.tas-step');
  var prev = document.getElementById('tasPrev');
  var next = document.getElementById('tasNext');
  var save = document.getElementById('tasSave');
  var edit = document.getElementById('tasEdit');
  var cancel = document.getElementById('tasCancel');
  var alertBox = document.getElementById('tasAlert');
  var modal = document.getElementById('tasModal');
  var step = 1;
  var saving = false;

  function $all(sel, ctx) { return Array.prototype.slice.call((ctx || root).querySelectorAll(sel)); }
  function normRoom(v) { return (v || '').replace(/\s+/g, ' ').trim(); }

  /* ---------- state lives in the form controls, so navigation never loses it ---------- */
  function sessions() {
    return $all('.tas-room').map(function (inp) {
      var sid = inp.dataset.session;
      var group = root.querySelector('[data-lab-group="' + sid + '"]');
      var checked = group ? group.querySelector('input:checked') : null;
      var row = inp.closest('.tas-subject');
      return {
        id: parseInt(sid, 10),
        subject: row.querySelector('.tas-subject-name').textContent.trim(),
        code: row.querySelector('.tas-code').textContent.trim(),
        meta: (root.querySelector('[data-row="' + sid + '"] .tas-meta') || {}).textContent || '',
        room: normRoom(inp.value),
        savedRoom: normRoom(inp.dataset.saved),
        lab: checked ? checked.value : '',
        savedLab: group ? group.dataset.saved : ''
      };
    });
  }
  function isChanged(s) { return s.room !== s.savedRoom || s.lab !== s.savedLab; }
  function dirty() { return sessions().some(isChanged); }

  /* ---------- messages ---------- */
  function say(kind, text) {
    alertBox.className = 'tas-alert ' + kind;
    alertBox.textContent = text;
    alertBox.hidden = false;
    if (kind === 'ok') { alertBox.scrollIntoView({ block: 'nearest' }); }
  }
  function clearSay() { alertBox.hidden = true; }

  function rowError(sid, msg) {
    $all('[data-error-for="' + sid + '"]').forEach(function (el) {
      el.textContent = msg || ''; el.hidden = !msg;
    });
    $all('[data-row="' + sid + '"],[data-row-lab="' + sid + '"]').forEach(function (r) {
      r.classList.toggle('has-error', !!msg);
    });
    var inp = root.querySelector('.tas-room[data-session="' + sid + '"]');
    if (inp) { inp.classList.toggle('is-invalid', !!msg); }
  }
  function clearRowErrors() { sessions().forEach(function (s) { rowError(s.id, ''); }); }

  function validateRooms() {
    var firstBad = null;
    sessions().forEach(function (s) {
      var msg = '';
      if (s.room.length > roomMax) { msg = 'Room number is too long (max ' + roomMax + ' characters).'; }
      else if (s.room && !ROOM_RE.test(s.room)) { msg = 'Use only letters, digits, spaces and - / _ . , ( ) # &'; }
      rowError(s.id, msg);
      if (msg && !firstBad) { firstBad = s.id; }
    });
    if (firstBad) {
      go(2);
      var el = root.querySelector('.tas-room[data-session="' + firstBad + '"]');
      if (el) { el.focus(); }
      say('err', 'Please fix the highlighted room number.');
      return false;
    }
    return true;
  }

  /* ---------- steps ---------- */
  function go(n) {
    if (n === 4 && !validateRooms()) { return; }
    step = Math.max(1, Math.min(4, n));
    panels.forEach(function (p) { p.hidden = String(p.dataset.panel) !== String(step); });
    stepBtns.forEach(function (b) {
      var k = parseInt(b.dataset.go, 10);
      b.classList.toggle('is-active', k === step);
      b.classList.toggle('is-done', k < step);
      if (k === step) { b.setAttribute('aria-current', 'step'); } else { b.removeAttribute('aria-current'); }
    });
    prev.hidden = step === 1;
    next.hidden = step === 4;
    save.hidden = step !== 4;
    edit.hidden = step !== 4;
    cancel.hidden = !(step === 2 || step === 3);
    if (step === 4) { renderReview(); }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  /* ---------- review ---------- */
  function counts(list) {
    return {
      total: list.length,
      configured: list.filter(function (s) { return s.lab; }).length,
      pending: list.filter(function (s) { return !s.lab; }).length,
      available: list.filter(function (s) { return s.lab === 'available'; }).length,
      none: list.filter(function (s) { return s.lab === 'none'; }).length,
      noRoom: list.filter(function (s) { return !s.room; }).length
    };
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined) { e.textContent = text; }
    return e;
  }
  function renderReview() {
    var list = sessions();
    var c = counts(list);
    var stats = document.getElementById('tasStats');
    stats.textContent = '';
    [['Total assigned subjects', c.total, false], ['Subjects configured', c.configured, false],
     ['Pending configuration', c.pending, c.pending > 0], ['Lab Available', c.available, false],
     ['No Lab', c.none, false], ['Without room number', c.noRoom, false]].forEach(function (x) {
      var d = el('div', 'tas-stat' + (x[2] ? ' is-warn' : ''));
      d.appendChild(el('b', '', String(x[1])));
      d.appendChild(el('span', '', x[0]));
      stats.appendChild(d);
    });

    var host = document.getElementById('tasReview');
    host.textContent = '';
    var card = el('div', 'card tas-subject');
    var head = el('div', 'tas-rev-row');
    head.style.fontWeight = '700'; head.style.borderTop = '0'; head.style.background = 'var(--navy-soft)';
    ['Subject', 'Section', 'Room', 'Lab'].forEach(function (h) { head.appendChild(el('div', '', h)); });
    head.className += ' tas-rev-head';
    card.appendChild(head);
    list.forEach(function (s) {
      var r = el('div', 'tas-rev-row');
      var a = el('div'); a.appendChild(el('div', '', s.subject)); a.appendChild(el('span', 'tas-code', s.code));
      var sec = el('div'); sec.appendChild(el('span', 'lbl', 'Section'));
      var m = /Section\s+(\S+)/.exec(s.meta); sec.appendChild(document.createTextNode(m ? m[1] : '—'));
      var rm = el('div'); rm.appendChild(el('span', 'lbl', 'Room')); rm.appendChild(document.createTextNode(s.room || 'Empty'));
      var lb = el('div'); lb.appendChild(el('span', 'lbl', 'Lab'));
      lb.appendChild(el('span', 'tas-pill ' + (s.lab === 'available' ? 'lab' : s.lab === 'none' ? 'none' : 'pending'),
                        LAB_TEXT[s.lab]));
      if (isChanged(s)) { lb.appendChild(el('span', 'tas-pill changed', 'unsaved')); }
      [a, sec, rm, lb].forEach(function (x) { r.appendChild(x); });
      card.appendChild(r);
    });
    host.appendChild(card);
    document.getElementById('tasUnsaved').hidden = !dirty();
    save.disabled = saving;
  }

  /* ---------- saving ---------- */
  function openModal() {
    var c = counts(sessions());
    var body = 'You are saving ' + c.total + ' assignment(s): ' + c.available + ' Lab Available, ' +
      c.none + ' No Lab.';
    if (c.pending) { body += ' ' + c.pending + ' still have no lab choice and will stay pending.'; }
    body += ' You can come back and edit this any time.';
    document.getElementById('tasModalBody').textContent = body;
    modal.hidden = false;
    document.getElementById('tasConfirm').focus();
  }
  function closeModal() { modal.hidden = true; }

  function setBusy(b) {
    saving = b;
    save.disabled = b;
    document.getElementById('tasConfirm').disabled = b;
    save.textContent = b ? 'Saving…' : 'Save Configuration';
  }

  function applySaved(assignments, summary) {
    assignments.forEach(function (a) {
      var inp = root.querySelector('.tas-room[data-session="' + a.session_id + '"]');
      if (inp) { inp.dataset.saved = a.room || ''; inp.value = a.room || ''; }
      var g = root.querySelector('[data-lab-group="' + a.session_id + '"]');
      if (g) { g.dataset.saved = a.lab_status || ''; }
      var vr = root.querySelector('[data-view-room="' + a.session_id + '"]');
      if (vr) { vr.textContent = a.room || 'Not set'; }
      var vl = root.querySelector('[data-view-lab="' + a.session_id + '"]');
      if (vl) { vl.textContent = LAB_TEXT[a.lab_status || '']; }
    });
    var st = document.getElementById('tasStatus');
    st.className = 'tas-status ' + (summary.complete ? 'is-complete' : 'is-pending');
    st.textContent = summary.complete ? 'Setup complete' : summary.pending + ' of ' + summary.total + ' pending';
  }

  function doSave() {
    if (saving) { return; }
    clearRowErrors(); clearSay(); setBusy(true);
    var items = sessions().map(function (s) {
      return { session_id: s.id, room: s.room, lab_status: s.lab || null };
    });
    fetch(saveUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf,
                 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
      body: JSON.stringify({ items: items })
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: 'Unexpected server response.' }; })
        .then(function (j) { return { status: r.status, body: j }; });
    }).then(function (res) {
      setBusy(false); closeModal();
      if (res.body && res.body.ok) {
        applySaved(res.body.assignments, res.body.summary);
        renderReview();
        say('ok', res.body.summary.complete
          ? 'Configuration saved. Your setup is complete.'
          : 'Configuration saved. ' + res.body.summary.pending + ' assignment(s) still need a lab choice.');
        return;
      }
      (res.body.errors || []).forEach(function (e) { rowError(e.session_id, e.message); });
      say('err', (res.body && res.body.error) || 'Could not save. Please try again.');
      if ((res.body.errors || []).length) {
        go(res.body.errors[0].field === 'room' ? 2 : 3);
      }
    }).catch(function () {
      setBusy(false); closeModal();
      say('err', 'Network problem — nothing was saved. Please check your connection and try again.');
    });
  }

  /* ---------- wiring ---------- */
  next.addEventListener('click', function () { clearSay(); go(step + 1); });
  prev.addEventListener('click', function () { clearSay(); go(step - 1); });
  edit.addEventListener('click', function () { clearSay(); go(2); });
  stepBtns.forEach(function (b) {
    b.addEventListener('click', function () { clearSay(); go(parseInt(b.dataset.go, 10)); });
  });
  save.addEventListener('click', function () { if (validateRooms()) { openModal(); } });
  document.getElementById('tasConfirm').addEventListener('click', doSave);
  document.getElementById('tasModalCancel').addEventListener('click', closeModal);
  modal.addEventListener('click', function (e) { if (e.target === modal) { closeModal(); } });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !modal.hidden) { closeModal(); } });

  cancel.addEventListener('click', function () {
    if (dirty() && !window.confirm('Discard your unsaved changes and go back to the last saved values?')) { return; }
    $all('.tas-room').forEach(function (i) { i.value = i.dataset.saved; });
    $all('[data-lab-group]').forEach(function (g) {
      $all('input', g).forEach(function (i) { i.checked = (i.value === g.dataset.saved); });
    });
    clearRowErrors(); say('warn', 'Changes discarded.');
  });

  $all('[data-bulk]').forEach(function (b) {
    b.addEventListener('click', function () {
      var card = b.closest('.tas-subject');
      $all('[data-lab-group]', card).forEach(function (g) {
        var inp = g.querySelector('input[value="' + b.dataset.bulk + '"]');
        if (inp && !inp.disabled) { inp.checked = true; }
      });
    });
  });

  $all('.tas-room').forEach(function (i) {
    i.addEventListener('input', function () { rowError(i.dataset.session, ''); });
  });

  window.addEventListener('beforeunload', function (e) {
    if (dirty() && !saving) { e.preventDefault(); e.returnValue = ''; }
  });

  go(1);
})();
