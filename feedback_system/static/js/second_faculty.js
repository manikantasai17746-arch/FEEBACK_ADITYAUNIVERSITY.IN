/* Second Faculty assignment (Phase 4).
 * Cards -> employee search -> employee confirmation -> eligible-student preview
 * -> final confirmation -> save.  All data comes from the JSON API; every rule is
 * enforced again on the server, so nothing here is trusted. */
(function () {
  'use strict';
  var root = document.getElementById('sf-root');
  if (!root) return;

  var API = root.getAttribute('data-api');                  // base url, no trailing slash
  var csrf = (document.querySelector('meta[name=csrf-token]') || {}).content || '';
  var cards = JSON.parse(document.getElementById('sf-data').textContent || '[]');
  var grid = document.getElementById('sf-cards');
  var toastBox = document.getElementById('sf-toast');
  var modal = document.getElementById('sf-modal');
  var body = document.getElementById('sf-modal-body');
  var title = document.getElementById('sf-modal-title');
  var crumbs = document.getElementById('sf-steps');

  // .container is its own stacking context (below the sticky top bar), so the
  // dialog and toasts are mounted on <body> to sit above everything.
  document.body.appendChild(modal);
  document.body.appendChild(toastBox);

  var state = null;     // {card, replace, emp, page, view}
  var searchSeq = 0, debounceT = null, saving = false, lastFocus = null;

  /* ---------- helpers ---------- */
  function h(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k.indexOf('on') === 0) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    });
    [].concat(kids || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  function api(path, opts) {
    opts = opts || {};
    var init = { method: opts.method || 'GET', credentials: 'same-origin',
                 headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' } };
    if (opts.body) {
      init.headers['Content-Type'] = 'application/json';
      init.headers['X-CSRF-Token'] = csrf;
      init.body = JSON.stringify(opts.body);
    }
    return fetch(API + path, init).then(function (r) {
      if (r.redirected || (r.status === 200 && (r.headers.get('content-type') || '').indexOf('json') < 0)) {
        throw { message: 'Your session has expired. Please sign in again.' };
      }
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok || j.ok === false) {
          throw { status: r.status, code: j.error, message: j.message || 'Something went wrong. Please try again.' };
        }
        return j;
      });
    });
  }
  function qs(o) {
    return Object.keys(o).filter(function (k) { return o[k] !== null && o[k] !== undefined && o[k] !== ''; })
      .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&');
  }

  function toast(msg, kind) {
    var t = h('div', { class: 'sf-toast sf-toast-' + (kind || 'info'), role: kind === 'error' ? 'alert' : 'status', text: msg });
    toastBox.appendChild(t);
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, kind === 'error' ? 8000 : 4500);
  }

  function avatar(emp, size) {
    var wrap = h('span', { class: 'sf-avatar', style: 'width:' + size + 'px;height:' + size + 'px' });
    var ini = h('span', { class: 'sf-avatar-ini', text: emp.initials || '?' });
    var img = h('img', { src: emp.photo_url, alt: emp.name || 'Employee photo', loading: 'lazy',
                         width: size, height: size });
    img.addEventListener('error', function () { img.style.display = 'none'; });
    wrap.appendChild(ini); wrap.appendChild(img);      // initials sit behind the photo
    return wrap;
  }

  function spinner(text) { return h('div', { class: 'sf-loading', role: 'status' }, [h('span', { class: 'sf-spin' }), text || 'Loading…']); }
  function banner(kind, text) { return h('div', { class: 'sf-banner sf-banner-' + kind, role: kind === 'error' ? 'alert' : 'status', text: text }); }

  /* ---------- cards ---------- */
  function cardNode(c) {
    var seconds = c.second_faculty.length ? c.second_faculty.map(function (s) {
      var actions = s.editable ? [
        h('button', { type: 'button', class: 'btn btn-ghost btn-sm', 'data-act': 'replace',
                      'aria-label': 'Replace ' + s.name, onclick: function () { openFlow(c.session_id, s.assignment_id); } }, 'Replace'),
        h('button', { type: 'button', class: 'btn btn-ghost btn-sm sf-danger', 'data-act': 'remove',
                      'aria-label': 'Remove ' + s.name, onclick: function () { confirmRemove(c, s); } }, 'Remove')
      ] : [h('span', { class: 'sf-tag', title: 'Set by the timetable import; managed by the administrator or HOD.', text: 'Imported' })];
      return h('li', { class: 'sf-second' }, [
        avatar(s, 38),
        h('div', { class: 'sf-second-meta' }, [h('strong', { text: s.name }), h('span', { class: 'sub', text: 'ID ' + s.emp_code + ' · ' + s.designation })]),
        h('div', { class: 'sf-second-actions' }, actions)
      ]);
    }) : [h('li', { class: 'sf-empty', text: 'No Second Faculty assigned yet.' })];

    return h('article', { class: 'card sf-card', 'data-session': c.session_id }, [
      h('header', {}, [
        h('h3', { text: c.subject_name }),
        h('div', { class: 'sf-code', text: c.subject_code })
      ]),
      h('dl', { class: 'sf-facts' }, [
        fact('Main Faculty', c.main_name + ' (' + c.main_emp_code + ')'),
        fact('Academic year', c.academic_year || '—'),
        fact('Semester', c.semester),
        fact('Branch', c.branch),
        fact('Section', c.section),
        fact('Room', c.room || 'Not set'),
        fact('Lab', c.lab_available === null ? 'Not set' : (c.lab_available ? 'Lab available' : 'No lab')),
        fact('Eligible students', String(c.eligible_students))
      ]),
      h('div', { class: 'sf-second-head' }, [
        h('strong', { text: 'Second Faculty' }),
        h('span', { class: 'badge badge-published', text: c.second_count + ' assigned' })
      ]),
      h('ul', { class: 'sf-second-list' }, seconds),
      h('button', { type: 'button', class: 'btn btn-accent sf-assign', 'data-act': 'assign',
                    onclick: function () { openFlow(c.session_id, null); } },
        c.second_count ? '＋ Add another Second Faculty' : 'Assign Second Faculty')
    ]);
  }
  function fact(k, v) { return h('div', {}, [h('dt', { text: k }), h('dd', { text: String(v) })]); }

  function render() {
    clear(grid);
    if (!cards.length) {
      grid.appendChild(h('div', { class: 'card sf-emptystate' }, [
        h('h3', { text: 'No subject assignments yet' }),
        h('p', { class: 'sub', text: 'You have no subjects mapped to a section in the timetable. Contact the administrator if this looks wrong.' })]));
      return;
    }
    cards.forEach(function (c) { grid.appendChild(cardNode(c)); });
  }
  function replaceCard(card) {
    if (!card) return;
    for (var i = 0; i < cards.length; i++) if (cards[i].session_id === card.session_id) cards[i] = card;
    render();
  }
  function cardById(id) { return cards.filter(function (c) { return c.session_id === id; })[0]; }

  /* ---------- modal shell ---------- */
  function openModal() {
    lastFocus = document.activeElement;
    modal.hidden = false; document.body.classList.add('sf-lock');
  }
  function closeModal() {
    if (saving) return;
    modal.hidden = true; document.body.classList.remove('sf-lock');
    state = null; clearTimeout(debounceT); searchSeq++;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  function setStep(n, heading) {
    title.textContent = heading;
    [].forEach.call(crumbs.children, function (li, i) {
      li.className = i + 1 === n ? 'is-on' : (i + 1 < n ? 'is-done' : '');
    });
    crumbs.hidden = n === 0;
  }
  function contextBar() {
    var c = state.card;
    return h('div', { class: 'sf-context' }, [
      h('strong', { text: c.subject_name }), ' (' + c.subject_code + ') · ' + c.branch + ' · Section ' + c.section +
      ' · Sem ' + c.semester + (c.academic_year ? ' · ' + c.academic_year : '')]);
  }
  function footer(buttons) { return h('div', { class: 'sf-footer' }, buttons); }

  function openFlow(sessionId, replaceId) {
    var card = cardById(sessionId); if (!card) return;
    state = { card: card, replace: replaceId || null, emp: null, page: 1 };
    openModal(); viewSearch('');
  }

  /* ---------- step 1: search ---------- */
  function viewSearch(prefill) {
    state.emp = null;
    setStep(1, state.replace ? 'Replace Second Faculty' : 'Assign Second Faculty');
    clear(body);
    var input = h('input', { type: 'search', id: 'sf-q', class: 'sf-input', autocomplete: 'off', maxlength: 60,
                             placeholder: 'Search by Employee ID or name', 'aria-label': 'Search employees', value: prefill || '' });
    var results = h('div', { id: 'sf-results', 'aria-live': 'polite' });
    body.appendChild(contextBar());
    body.appendChild(h('label', { class: 'field-label', for: 'sf-q', text: 'Find an employee' }));
    body.appendChild(input); body.appendChild(results);
    body.appendChild(footer([h('button', { type: 'button', class: 'btn btn-ghost', onclick: closeModal }, 'Cancel')]));
    input.addEventListener('input', function () {
      clearTimeout(debounceT);
      debounceT = setTimeout(function () { runSearch(input.value, 1, false); }, 300);   // debounce
    });
    results.appendChild(h('p', { class: 'sub sf-hint', text: 'Type at least 2 characters of an Employee ID or name.' }));
    input.focus();
    if (prefill) runSearch(prefill, 1, false);
  }

  function runSearch(q, page, append) {
    var results = document.getElementById('sf-results'); if (!results || !state) return;
    var seq = ++searchSeq;
    if (!append) { clear(results); results.appendChild(spinner('Searching…')); }
    api('/api/employees?' + qs({ session_id: state.card.session_id, q: q, page: page })).then(function (j) {
      if (seq !== searchSeq || !state) return;               // a newer search superseded this one
      var more = results.querySelector('.sf-more'); if (more) more.remove();
      var sp = results.querySelector('.sf-loading'); if (sp) sp.remove();
      var hint = results.querySelector('.sf-hint'); if (hint) hint.remove();
      if (!append) clear(results);
      if (j.hint) { results.appendChild(h('p', { class: 'sub sf-hint', text: j.hint })); return; }
      if (!j.results.length && !append) {
        results.appendChild(h('div', { class: 'sf-noresults' }, [h('strong', { text: 'No employees found' }),
          h('p', { class: 'sub', text: 'Nothing matches “' + j.query + '”. Check the spelling or try the Employee ID.' })]));
        return;
      }
      var list = results.querySelector('.sf-list') || results.appendChild(h('ul', { class: 'sf-list' }));
      j.results.forEach(function (e) { list.appendChild(employeeRow(e)); });
      if (j.has_more) results.appendChild(h('button', { type: 'button', class: 'btn btn-ghost btn-sm sf-more',
        onclick: function () { runSearch(q, page + 1, true); } }, 'Show more'));
    }).catch(function (e) {
      if (seq !== searchSeq) return;
      clear(results);
      results.appendChild(banner('error', e.message || 'Search failed.'));
      results.appendChild(h('button', { type: 'button', class: 'btn btn-ghost btn-sm', onclick: function () { runSearch(q, 1, false); } }, 'Try again'));
    });
  }

  function employeeRow(e) {
    var btn = h('button', { type: 'button', class: 'btn btn-sm', disabled: e.already_assigned,
                            'aria-label': 'Select ' + e.name, onclick: function () { state.emp = e; viewConfirmEmployee(); } },
                e.already_assigned ? 'Already assigned' : 'Select');
    return h('li', { class: 'sf-emp' }, [
      avatar(e, 48),
      h('div', { class: 'sf-emp-meta' }, [
        h('strong', { text: e.name }),
        h('span', { class: 'sub', text: 'ID ' + e.emp_code + ' · ' + e.designation + (e.departments.length ? ' · ' + e.departments.join(', ') : '') })]),
      btn]);
  }

  /* ---------- step 2: confirm employee ---------- */
  function viewConfirmEmployee() {
    var e = state.emp, c = state.card;
    setStep(2, 'Confirm selection');
    clear(body);
    body.appendChild(contextBar());
    body.appendChild(h('div', { class: 'sf-profile' }, [
      avatar(e, 96),
      h('div', {}, [h('h3', { text: e.name }), h('p', { text: 'Employee ID: ' + e.emp_code }),
        h('p', { class: 'sub', text: e.designation + (e.departments.length ? ' · ' + e.departments.join(', ') : '') })])]));
    body.appendChild(h('dl', { class: 'sf-facts sf-facts-sm' }, [
      fact('Subject', c.subject_name + ' (' + c.subject_code + ')'),
      fact('Main Faculty', c.main_name + ' (' + c.main_emp_code + ')'),
      fact('Academic year', c.academic_year || '—'), fact('Semester', c.semester),
      fact('Branch', c.branch), fact('Section', c.section)]));
    body.appendChild(banner('info', 'Nothing is saved yet. Next you will review the students affected.'));
    body.appendChild(footer([
      h('button', { type: 'button', class: 'btn btn-ghost', onclick: closeModal }, 'Cancel'),
      h('button', { type: 'button', class: 'btn btn-ghost', onclick: function () { viewSearch(''); } }, 'Change employee'),
      h('button', { type: 'button', class: 'btn', onclick: function () { state.page = 1; viewPreview(); } }, 'Confirm selection')]));
  }

  /* ---------- step 3: eligible students ---------- */
  function viewPreview() {
    setStep(3, 'Review eligible students');
    clear(body); body.appendChild(spinner('Loading eligible students…'));
    var req = { session_id: state.card.session_id, emp_code: state.emp.emp_code, page: state.page, replace_id: state.replace };
    api('/api/preview?' + qs(req)).then(function (p) {
      if (!state) return;
      renderPreview(p);
    }).catch(function (err) {
      clear(body);
      body.appendChild(banner('error', err.message || 'Could not load the preview.'));
      body.appendChild(footer([
        h('button', { type: 'button', class: 'btn btn-ghost', onclick: viewConfirmEmployee }, 'Go back'),
        h('button', { type: 'button', class: 'btn', onclick: viewPreview }, 'Try again')]));
    });
  }

  function renderPreview(p) {
    clear(body);
    var a = p.assignment, st = p.students;
    body.appendChild(h('h3', { class: 'sf-h', text: 'Second Faculty Assignment Preview' }));
    body.appendChild(h('div', { class: 'sf-two' }, [
      h('div', { class: 'sf-box' }, [h('div', { class: 'sf-k', text: 'Main Faculty' }), h('strong', { text: p.main.name }), h('div', { class: 'sub', text: 'ID ' + p.main.emp_code })]),
      h('div', { class: 'sf-box sf-box-photo' }, [avatar(p.second, 48), h('div', {}, [h('div', { class: 'sf-k', text: 'Second Faculty' }), h('strong', { text: p.second.name }), h('div', { class: 'sub', text: 'ID ' + p.second.emp_code })])])]));
    body.appendChild(h('dl', { class: 'sf-facts sf-facts-sm' }, [
      fact('Subject', p.subject.name), fact('Subject code', p.subject.code),
      fact('Academic year', a.academic_year || '—'), fact('Semester', a.semester),
      fact('Branch', a.branch), fact('Section', a.section), fact('Room', a.room || 'Not set'),
      fact('Total eligible students', st.total)]));
    if (p.replacing) body.appendChild(banner('info', 'This will replace ' + p.replacing.name + ' (ID ' + p.replacing.emp_code + ').'));
    p.problems.forEach(function (x) { body.appendChild(banner('error', x.message)); });
    p.warnings.forEach(function (x) { body.appendChild(banner('warn', x.message)); });

    if (st.total > 0) {
      var tbody = h('tbody', {}, st.rows.map(function (r) { return h('tr', {}, [h('td', { text: r.roll }), h('td', { text: r.name }), h('td', { text: r.section })]); }));
      body.appendChild(h('div', { class: 'sf-tablewrap', tabindex: 0, role: 'region', 'aria-label': 'Eligible students' },
        [h('table', { class: 'data sf-table' }, [h('thead', {}, [h('tr', {}, [h('th', { text: 'Roll number' }), h('th', { text: 'Student name' }), h('th', { text: 'Section' })])]), tbody])]));
      body.appendChild(h('div', { class: 'sf-pager' }, [
        h('button', { type: 'button', class: 'btn btn-ghost btn-sm', disabled: st.page <= 1, onclick: function () { state.page--; viewPreview(); } }, '‹ Prev'),
        h('span', { class: 'sub', text: 'Page ' + st.page + ' of ' + st.pages + ' · ' + st.total + ' students' }),
        h('button', { type: 'button', class: 'btn btn-ghost btn-sm', disabled: st.page >= st.pages, onclick: function () { state.page++; viewPreview(); } }, 'Next ›')]));
    }
    body.appendChild(footer([
      h('button', { type: 'button', class: 'btn btn-ghost', onclick: closeModal }, 'Cancel'),
      h('button', { type: 'button', class: 'btn btn-ghost', onclick: viewConfirmEmployee }, 'Go back'),
      h('button', { type: 'button', class: 'btn', disabled: !p.can_confirm, onclick: function () { viewFinal(p); } }, 'Continue')]));
  }

  /* ---------- step 4: final confirmation ---------- */
  function viewFinal(p) {
    setStep(4, 'Confirm Second Faculty Assignment');
    clear(body);
    var a = p.assignment;
    body.appendChild(h('dl', { class: 'sf-facts sf-facts-sm' }, [
      fact('Main Faculty', p.main.name + ' (' + p.main.emp_code + ')'),
      fact('Second Faculty', p.second.name + ' (' + p.second.emp_code + ')'),
      fact('Subject', p.subject.name), fact('Section', a.section),
      fact('Academic year', a.academic_year || '—'), fact('Semester', a.semester),
      fact('Eligible students', p.students.total)]));
    body.appendChild(h('p', { class: 'sf-ask', text: 'Are you sure you want to assign this employee as Second Faculty for the selected subject and section?' }));
    var confirm = h('button', { type: 'button', class: 'btn btn-ok', id: 'sf-confirm' }, 'Confirm Assignment');
    var back = h('button', { type: 'button', class: 'btn btn-ghost' }, 'Go back');
    var cancel = h('button', { type: 'button', class: 'btn btn-ghost' }, 'Cancel');
    back.addEventListener('click', function () { if (!saving) viewPreview(); });
    cancel.addEventListener('click', closeModal);
    confirm.addEventListener('click', function () { save(confirm, [back, cancel]); });
    body.appendChild(footer([cancel, back, confirm]));
    confirm.focus();
  }

  function save(btn, others) {
    if (saving) return;                                    // double-click guard
    saving = true; btn.disabled = true; btn.textContent = 'Saving…';
    others.forEach(function (b) { b.disabled = true; });
    var path = state.replace ? '/api/assignments/' + state.replace + '/replace' : '/api/assign';
    api(path, { method: 'POST', body: { session_id: state.card.session_id, emp_code: state.emp.emp_code } })
      .then(function (j) {
        saving = false; replaceCard(j.card); closeModal(); toast(j.message || 'Saved.', 'success');
      }).catch(function (e) {
        saving = false; btn.disabled = false; btn.textContent = 'Confirm Assignment';
        others.forEach(function (b) { b.disabled = false; });
        var old = body.querySelector('.sf-banner-error'); if (old) old.remove();
        body.insertBefore(banner('error', e.message), body.firstChild);
        if (e.code === 'duplicate') { btn.disabled = true; refreshCard(state.card.session_id); }
      });
  }

  function refreshCard(sessionId) {
    api('/api/cards/' + sessionId).then(function (j) { replaceCard(j.card); }).catch(function () {});
  }

  /* ---------- remove ---------- */
  function confirmRemove(c, s) {
    state = { card: c, replace: null, emp: s, page: 1 };
    openModal(); setStep(0, 'Remove Second Faculty');
    clear(body);
    body.appendChild(contextBar());
    body.appendChild(h('div', { class: 'sf-profile' }, [avatar(s, 64), h('div', {}, [h('h3', { text: s.name }), h('p', { class: 'sub', text: 'ID ' + s.emp_code })])]));
    body.appendChild(banner('warn', 'Students will no longer be asked for feedback on this Second Faculty. Existing feedback and history are kept.'));
    var yes = h('button', { type: 'button', class: 'btn btn-danger' }, 'Remove Second Faculty');
    var no = h('button', { type: 'button', class: 'btn btn-ghost', onclick: closeModal }, 'Keep');
    yes.addEventListener('click', function () {
      if (saving) return;
      saving = true; yes.disabled = true; no.disabled = true; yes.textContent = 'Removing…';
      api('/api/assignments/' + s.assignment_id + '/remove', { method: 'POST', body: {} }).then(function (j) {
        saving = false; replaceCard(j.card); closeModal(); toast(j.message, 'success');
      }).catch(function (e) {
        saving = false; yes.disabled = false; no.disabled = false; yes.textContent = 'Remove Second Faculty';
        body.insertBefore(banner('error', e.message), body.firstChild);
      });
    });
    body.appendChild(footer([no, yes]));
    no.focus();
  }

  /* ---------- wiring ---------- */
  document.getElementById('sf-close').addEventListener('click', closeModal);
  modal.addEventListener('mousedown', function (ev) { if (ev.target === modal) closeModal(); });
  document.addEventListener('keydown', function (ev) {
    if (modal.hidden) return;
    if (ev.key === 'Escape') closeModal();
    if (ev.key === 'Tab') {                                // keep focus inside the dialog
      var f = [].filter.call(modal.querySelectorAll('button, input, [tabindex="0"]'), function (x) { return !x.disabled && x.offsetParent !== null; });
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (ev.shiftKey && document.activeElement === first) { last.focus(); ev.preventDefault(); }
      else if (!ev.shiftKey && document.activeElement === last) { first.focus(); ev.preventDefault(); }
    }
  });
  render();
})();
