(function () {
  'use strict';
  var root = document.getElementById('hfCreate');
  if (!root) return;
  var A = function (n) { return root.getAttribute(n); };
  var apiSessions = A('data-api-sessions'), apiPreview = A('data-api-preview'), apiCreate = A('data-api-create');
  var qBase = A('data-api-qsets');                       // /hod/api/question-sets
  var qList = A('data-api-qsets-list');
  var overviewUrl = A('data-overview-url') || '/hod/', formsUrl = A('data-forms-url') || '/hod/forms';
  var preselect = parseInt(A('data-preselect'), 10) || null;
  var qsets = [], presets = {};
  try { qsets = JSON.parse(A('data-question-sets') || '[]'); } catch (e) { qsets = []; }
  try { presets = JSON.parse(A('data-presets') || '{}'); } catch (e) { presets = {}; }
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

  var rows = [];            // flat list of sections
  var selected = {};        // session_id -> row
  var mode = preselect ? 'single' : 'all';
  var activeSubject = null;
  var previews = [];
  var excluded = {};        // question index -> true (this form only)
  var extras = [];          // [{text, qtype}] (this form only)

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  }
  function alertBox(msg, kind) {
    var el = $('hfAlert'); if (!el) return;
    if (!msg) { el.hidden = true; el.textContent = ''; return; }
    el.hidden = false; el.className = 'hf-alert' + (kind ? ' is-' + kind : ''); el.textContent = msg;
  }
  function goStep(n) {
    root.querySelectorAll('.hf-step').forEach(function (b) {
      b.classList.toggle('is-active', String(b.getAttribute('data-step')) === String(n)); });
    root.querySelectorAll('.hf-panel').forEach(function (p) {
      var on = String(p.getAttribute('data-panel')) === String(n); p.hidden = !on; p.classList.toggle('is-active', on); });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  function post(url, body) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json',
                 'X-CSRF-Token': csrf, 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok && d.ok !== false, status: r.status, data: d }; }); });
  }
  function shortName(n) {
    var parts = String(n || '').trim().split(/\s+/).filter(function (w) { return !/^dr\.?$/i.test(w); });
    return (parts[0] || n || '').toUpperCase();
  }

  /* ------------------------------------------------------------------ Step 1 */
  function selectedIds() { return Object.keys(selected).map(function (k) { return parseInt(k, 10); }); }
  function subjectsOf(list) {
    var m = {}, order = [];
    list.forEach(function (r) {
      if (!m[r.subject_code]) { m[r.subject_code] = { code: r.subject_code, name: r.subject_name, rows: [] }; order.push(r.subject_code); }
      m[r.subject_code].rows.push(r);
    });
    return order.map(function (c) { return m[c]; });
  }
  function baseRows() {
    if (mode === 'single') return rows.filter(function (r) { return r.sid === preselect; });
    return rows;
  }
  function visibleRows() {
    var q = ($('hfRowFilter').value || '').trim().toLowerCase();
    return baseRows().filter(function (r) {
      if (r.subject_code !== activeSubject) return false;
      if (!q) return true;
      return (r.section + ' ' + r.teacher_name + ' ' + r.emp).toLowerCase().indexOf(q) !== -1;
    });
  }
  function renderSubjects() {
    var q = ($('hfSubjectSearch').value || '').trim().toLowerCase();
    var html = '';
    subjectsOf(baseRows()).forEach(function (s) {
      if (q && (s.code + ' ' + s.name).toLowerCase().indexOf(q) === -1) return;
      var n = s.rows.filter(function (r) { return selected[r.sid]; }).length;
      html += '<button type="button" class="hfc-subj' + (s.code === activeSubject ? ' is-active' : '') + '" data-code="' + esc(s.code) + '">' +
        '<span><strong>' + esc(s.code) + '</strong><small>' + esc(s.name) + '</small></span>' +
        (n ? '<span class="hfc-badge">' + n + '</span>' : '') + '</button>';
    });
    $('hfSubjectList').innerHTML = html || '<p class="sub">No subjects.</p>';
    $('hfSubjectList').querySelectorAll('.hfc-subj').forEach(function (b) {
      b.addEventListener('click', function () { activeSubject = b.getAttribute('data-code'); renderAll(); });
    });
  }
  function renderRows() {
    var vis = visibleRows(), tb = $('hfRows');
    var elig = vis.reduce(function (a, r) { return a + (r.eligible || 0); }, 0);
    $('hfCount').textContent = vis.length + ' section' + (vis.length === 1 ? '' : 's') + ' · ' + elig + ' eligible';
    var allOn = vis.length && vis.every(function (r) { return selected[r.sid]; });
    $('hfToggleAll').textContent = allOn ? 'Unselect all' : 'Select all';
    $('hfToggleAll').disabled = !vis.length;
    if (!vis.length) { tb.innerHTML = '<tr><td colspan="5" class="sub">No sections match.</td></tr>'; return; }
    tb.innerHTML = vis.map(function (r) {
      var on = !!selected[r.sid];
      return '<tr class="' + (on ? 'is-on' : '') + '" data-sid="' + r.sid + '"><td><input type="checkbox" data-sid="' + r.sid + '"' + (on ? ' checked' : '') + ' aria-label="Select ' + esc(r.teacher_name) + ' ' + esc(r.section) + '"></td>' +
        '<td><strong>' + esc(r.teacher_name) + '</strong> <small>(' + esc(r.emp) + ')</small>' + (r.live ? ' <span class="hfc-live">' + r.live + ' live</span>' : '') + '</td>' +
        '<td>' + esc(r.section) + '</td><td>' + esc(r.branch) + '</td><td><strong>' + (r.eligible || 0) + '</strong></td></tr>';
    }).join('');
    tb.querySelectorAll('tr[data-sid]').forEach(function (tr) {
      tr.addEventListener('click', function (e) {
        var sid = parseInt(tr.getAttribute('data-sid'), 10);
        var cb = tr.querySelector('input'); if (e.target !== cb) cb.checked = !cb.checked;
        toggle(sid, cb.checked);
      });
    });
  }
  function toggle(sid, on) {
    var r = rows.filter(function (x) { return x.sid === sid; })[0];
    if (!r) return;
    if (on) selected[sid] = r; else delete selected[sid];
    renderAll();
  }
  function renderSelection() {
    var ids = selectedIds();
    var students = ids.reduce(function (a, id) { return a + (selected[id].eligible || 0); }, 0);
    $('hfSelCount').textContent = ids.length;
    $('hfSelMeta').textContent = (ids.length === 1 ? 'section' : 'sections') + ' · ' + students + ' students';
    var box = $('hfChips');
    if (!ids.length) { box.innerHTML = '<span class="sub">Nothing selected yet</span>'; }
    else {
      box.innerHTML = ids.map(function (id) {
        var r = selected[id];
        return '<span class="hfc-chip">' + esc(r.section) + ' · ' + esc(shortName(r.teacher_name)) +
          ' <button type="button" data-rm="' + id + '" aria-label="Remove">×</button></span>';
      }).join('');
      box.querySelectorAll('[data-rm]').forEach(function (b) {
        b.addEventListener('click', function () { toggle(parseInt(b.getAttribute('data-rm'), 10), false); });
      });
    }
    $('hfToPreview').disabled = !ids.length;
  }
  function renderBanner() {
    var b = $('hfBanner');
    if (mode !== 'single') { b.hidden = true; return; }
    var r = rows.filter(function (x) { return x.sid === preselect; })[0];
    if (!r) { b.hidden = true; return; }
    b.hidden = false;
    $('hfBannerText').innerHTML = 'Creating feedback for <strong>' + esc(r.teacher_name) + '</strong> only · ' +
      esc(r.subject_code) + ' · Section ' + esc(r.section) + '.';
  }
  function renderAll() { renderBanner(); renderSubjects(); renderRows(); renderSelection(); }

  function loadSessions() {
    alertBox('Loading sessions…', 'loading');
    fetch(apiSessions, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); }).then(function (data) {
        if (!data.ok) { alertBox(data.error || 'Could not load sessions.', 'error'); return; }
        alertBox('');
        rows = [];
        (data.subjects || []).forEach(function (sub) {
          (sub.teachers || []).forEach(function (t) {
            (t.sections || []).forEach(function (s) {
              rows.push({ sid: s.session_id, subject_code: sub.subject_code, subject_name: sub.subject_name,
                teacher_name: t.teacher_name, emp: t.teacher_emp_code || '', section: s.section_code,
                branch: s.branch_code, eligible: s.eligible_count || 0, live: s.live_main_forms || 0 });
            });
          });
        });
        var pre = preselect && rows.filter(function (r) { return r.sid === preselect; })[0];
        if (preselect && !pre) { mode = 'all'; preselect = null; }
        if (pre) { selected[pre.sid] = pre; activeSubject = pre.subject_code; }
        else if (rows.length) { activeSubject = rows[0].subject_code; }
        renderAll();
      }).catch(function () { alertBox('Network error while loading sessions.', 'error'); });
  }

  $('hfShowAll').addEventListener('click', function () {
    mode = 'all'; selected = {};
    renderAll();
  });
  $('hfToggleAll').addEventListener('click', function () {
    var vis = visibleRows();
    var allOn = vis.length && vis.every(function (r) { return selected[r.sid]; });
    vis.forEach(function (r) { if (allOn) delete selected[r.sid]; else selected[r.sid] = r; });
    renderAll();
  });
  $('hfClear').addEventListener('click', function () { selected = {}; renderAll(); });
  $('hfRowFilter').addEventListener('input', renderRows);
  $('hfSubjectSearch').addEventListener('input', renderSubjects);

  /* ------------------------------------------------------------------ Step 2 */
  function loadPreview() {
    var ids = selectedIds(); if (!ids.length) return;
    alertBox('Loading eligible students…', 'loading');
    var q = ids.map(function (id) { return 'session_id=' + encodeURIComponent(id); }).join('&');
    fetch(apiPreview + '?' + q, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { alertBox(d.error || 'Preview failed.', 'error'); return; }
        alertBox(''); previews = d.previews || [];
        $('hfPreview').innerHTML = previews.map(function (p) {
          return '<div class="hf-preview-block"><h3>' + esc(p.subject_code) + ' · Section ' + esc(p.section_code || '') + ' · ' + esc(p.teacher_name || '') + '</h3>' +
            '<p class="sub">' + (p.eligible_count || 0) + ' eligible student(s)</p><div class="hf-student-list">' +
            ((p.students || []).map(function (st) { return '<div><strong>' + esc(st.roll) + '</strong> — ' + esc(st.name) + '</div>'; }).join('') ||
              '<div class="sub">No eligible students.</div>') + '</div></div>';
        }).join('') || '<p class="sub">No data.</p>';
        goStep(2);
      }).catch(function () { alertBox('Network error while loading preview.', 'error'); });
  }
  $('hfToPreview').addEventListener('click', loadPreview);
  $('hfGoBottom').addEventListener('click', function () {
    $('hfPreviewActions').scrollIntoView({ behavior: 'smooth', block: 'end' });
  });
  $('hfToQuestions').addEventListener('click', function () { fillQuestionSets(); goStep(3); });

  /* ------------------------------------------------------------------ Step 3 */
  var curSet = null;
  function setLabel(s) { return s.name + (s.shared ? ' (shared)' : '') + (s.customised ? ' (customised)' : ''); }
  function fillQuestionSets() {
    var sel = $('hfQSet');
    if (!curSet || qsets.indexOf(curSet) === -1) {
      curSet = qsets.filter(function (s) { return /^phase 1/i.test(s.name); })[0] || qsets[0] || null;
    }
    sel.innerHTML = qsets.length ? qsets.map(function (s, i) {
      return '<option value="' + i + '"' + (s === curSet ? ' selected' : '') + '>' + esc(setLabel(s)) + '</option>';
    }).join('') : '<option value="">No question sets available</option>';
    showQPreview();
  }
  function showQPreview() {
    var box = $('hfQPreview');
    var qs = (curSet && curSet.questions) || [];
    if (!qs.length) { box.innerHTML = '<p class="sub">No questions in this set.</p>'; return; }
    var used = qs.filter(function (q, i) { return !excluded[i]; }).length;
    box.innerHTML = '<div class="hfc-qhead"><strong>' + used + ' of ' + qs.length + ' question(s) used in this form</strong>' +
      '<small>Use the trash icon to leave a question out of <em>this form only</em>. The question set itself is not changed and other forms are not affected.</small></div><ol>' +
      qs.map(function (q, i) {
        return '<li class="' + (excluded[i] ? 'is-out' : '') + '"><span>' + esc(q.text) + '</span>' +
          '<button type="button" class="hfc-trash" data-q="' + i + '" aria-label="' + (excluded[i] ? 'Add back' : 'Leave out') + '">' + (excluded[i] ? '↺' : '🗑') + '</button></li>';
      }).join('') + '</ol>';
    box.querySelectorAll('[data-q]').forEach(function (b) {
      b.addEventListener('click', function () {
        var i = parseInt(b.getAttribute('data-q'), 10);
        if (excluded[i]) delete excluded[i];
        else {
          var left = qs.filter(function (q, k) { return !excluded[k]; }).length + extras.length;
          if (left <= 1) { alertBox('A form needs at least one question.', 'error'); return; }
          excluded[i] = true;
        }
        alertBox(''); showQPreview();
      });
    });
  }
  $('hfQSet').addEventListener('change', function () {
    curSet = qsets[parseInt($('hfQSet').value, 10)] || null; excluded = {}; showQPreview();
  });
  function renderExtras() {
    $('hfExtraEmpty').hidden = extras.length > 0;
    $('hfExtraList').innerHTML = extras.map(function (x, i) {
      return '<div class="hfc-extrarow"><span class="hfc-tag">' + (x.qtype === 'text' ? 'Written' : 'Rating') + '</span>' +
        '<input type="text" maxlength="1000" data-x="' + i + '" value="' + esc(x.text) + '" placeholder="Type the question">' +
        '<button type="button" class="hfc-trash" data-xr="' + i + '" aria-label="Remove">🗑</button></div>';
    }).join('');
    $('hfExtraList').querySelectorAll('[data-x]').forEach(function (inp) {
      inp.addEventListener('input', function () { extras[parseInt(inp.getAttribute('data-x'), 10)].text = inp.value; });
    });
    $('hfExtraList').querySelectorAll('[data-xr]').forEach(function (b) {
      b.addEventListener('click', function () { extras.splice(parseInt(b.getAttribute('data-xr'), 10), 1); renderExtras(); });
    });
  }
  $('hfAddRating').addEventListener('click', function () { extras.push({ text: '', qtype: 'rating' }); renderExtras(); });
  $('hfAddText').addEventListener('click', function () { extras.push({ text: '', qtype: 'text' }); renderExtras(); });
  $('hfPhase').addEventListener('change', function () { $('hfTitle').hidden = $('hfPhase').value !== 'custom'; });

  function formName() {
    var p = $('hfPhase').value;
    if (p === 'custom') return ($('hfTitle').value || '').trim() || 'Default title (faculty and subject)';
    return p === 'phase2' ? 'Phase 2' : 'Phase 1';
  }
  function cleanExtras() { return extras.filter(function (x) { return (x.text || '').trim(); }); }
  $('hfToConfirm').addEventListener('click', function () {
    if (!curSet) { alertBox('Choose a question set.', 'error'); return; }
    var kept = ((curSet.questions || []).filter(function (q, i) { return !excluded[i]; }).length) + cleanExtras().length;
    if (!kept) { alertBox('A form needs at least one question.', 'error'); return; }
    alertBox('');
    var html = '<p><strong>' + selectedIds().length + '</strong> section(s) selected.</p><ul>';
    previews.forEach(function (p) {
      html += '<li>' + esc(p.teacher_name || '') + ' - ' + esc(p.subject_code) + ' / ' + esc(p.section_code || '') + ' — ' + (p.eligible_count || 0) + ' eligible</li>'; });
    html += '</ul><p>Question set: <strong>' + esc(setLabel(curSet)) + '</strong></p>' +
      '<p>Form name: <strong>' + esc(formName()) + '</strong></p>' +
      '<p>Deadline: <strong>' + esc(($('hfDeadline').value || '').replace('T', ' ') || 'None') + '</strong></p>' +
      '<p>Authenticator-app code: <strong>' + ($('hfAuth').checked ? 'Required before students open or submit' : 'Not required') + '</strong></p>' +
      '<p>Extra questions: <strong>' + (cleanExtras().length || 'None') + '</strong></p>';
    $('hfSummary').innerHTML = html; goStep(4);
  });
  root.querySelectorAll('[data-back]').forEach(function (b) {
    b.addEventListener('click', function () { goStep(b.getAttribute('data-back')); });
  });
  root.querySelectorAll('.hf-step').forEach(function (b) {
    b.addEventListener('click', function () {
      var n = parseInt(b.getAttribute('data-step'), 10);
      if (n === 1) goStep(1);
      else if (n === 2 && previews.length) goStep(2);
      else if (n === 3 && previews.length) { fillQuestionSets(); goStep(3); }
      else if (n === 4 && previews.length && curSet) $('hfToConfirm').click();
    });
  });

  /* ------------------------------------------------------------------ Step 4 */
  var busy = false;
  function submitCreate(publish) {
    if (busy) return;
    var ids = selectedIds(); if (!ids.length) { alertBox('Select at least one section.', 'error'); return; }
    if (!curSet) { alertBox('Choose a question set.', 'error'); return; }
    var deadline = ($('hfDeadline').value || '').trim();
    if (publish && !deadline) { alertBox('Set a deadline in the Questions step before publishing.', 'error'); return; }
    busy = true; alertBox(publish ? 'Publishing…' : 'Saving draft…', 'loading');
    var phase = $('hfPhase').value;
    post(apiCreate, {
      session_ids: ids, question_set_id: curSet.id,
      exclude_questions: Object.keys(excluded).map(Number),
      extra_questions: cleanExtras(),
      phase: phase === 'custom' ? '' : phase,
      title: phase === 'custom' ? ($('hfTitle').value || '').trim() : '',
      instructions: $('hfInstructions').value || '',
      deadline_at: deadline || null,
      auth_required: !!$('hfAuth').checked,
      publish: !!publish
    }).then(function (res) {
      busy = false; var d = res.data;
      if (!d.ok) { alertBox(d.error || ((d.errors && d.errors[0] && d.errors[0].error) || 'Create failed.'), 'error'); return; }
      var n = (d.created || []).length, errs = d.errors || [];
      if (errs.length) {
        alertBox(n + ' form(s) ' + (publish ? 'published' : 'saved') + '; ' + errs.length + ' failed: ' + errs[0].error, 'error');
        return;
      }
      alertBox(n + ' form(s) ' + (publish ? 'published' : 'saved as draft') + '.', 'ok');
      window.setTimeout(function () { window.location.href = publish ? overviewUrl : formsUrl; }, 700);
    }).catch(function () { busy = false; alertBox('Network error while creating forms.', 'error'); });
  }
  $('hfSaveDraft').addEventListener('click', function () { submitCreate(false); });
  $('hfPublish').addEventListener('click', function () { submitCreate(true); });

  /* ------------------------------------------------------------- Edit sets modal */
  var modal = $('hfModal'), activeSet = null, saveTimers = [];
  function isPhase(s) { return !!(s.shared || s.base_key); }
  function openModal() {
    activeSet = curSet || qsets[0] || null;
    modal.hidden = false; document.body.classList.add('hfc-lock'); renderModal();
  }
  function closeModal() {
    modal.hidden = true; document.body.classList.remove('hfc-lock');
    excluded = {}; fillQuestionSets();
  }
  $('hfEditSets').addEventListener('click', openModal);
  $('hfModalDone').addEventListener('click', closeModal);
  $('hfModalX').addEventListener('click', closeModal);
  modal.addEventListener('click', function (e) { if (e.target === modal) closeModal(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !modal.hidden) closeModal(); });

  function renderModal() {
    var phase = qsets.filter(isPhase), mine = qsets.filter(function (s) { return !isPhase(s); });
    function btn(s) {
      return '<button type="button" class="hfc-setbtn' + (s === activeSet ? ' is-active' : '') + '" data-i="' + qsets.indexOf(s) + '"><span>' + esc(s.name) +
        '</span><small>' + (s.questions || []).length + ' Q</small></button>';
    }
    $('hfSetNav').innerHTML = '<div class="hfc-cap">Phase sets (shared)</div>' + phase.map(btn).join('') +
      '<div class="hfc-cap">My sets</div>' + mine.map(btn).join('') +
      '<button type="button" class="btn btn-primary hfc-wide" id="hfNewSet">+ New set</button>';
    $('hfSetNav').querySelectorAll('.hfc-setbtn').forEach(function (b) {
      b.addEventListener('click', function () { activeSet = qsets[parseInt(b.getAttribute('data-i'), 10)]; renderModal(); });
    });
    $('hfNewSet').addEventListener('click', newSet);
    renderEditor();
  }
  function status(text, cls) {
    var el = $('hfSetStatus'); if (!el) return;
    el.textContent = text; el.className = 'hfc-status' + (cls ? ' is-' + cls : '');
  }
  function ensureEditable(s) {          // phase sets become the HOD's own copy on first edit
    if (s.shared && !s.customised) {
      return post(qBase.replace(/\/?$/, '') + '/customise-shared', { key: s.id }).then(function (res) {
        if (!res.ok) { status('⚠ ' + (((res.data.errors || [])[0]) || 'Could not edit this set'), 'err'); return false; }
        s.base_key = s.id; s.id = res.data.id; s.shared = false; s.customised = true; return true;
      });
    }
    return Promise.resolve(true);
  }
  function scheduleSave(s) {
    status('Saving…', '');
    clearTimeout(saveTimers[0]);
    saveTimers[0] = setTimeout(function () {
      ensureEditable(s).then(function (ok) {
        if (!ok) return;
        post(qBase + '/' + s.id + '/questions', { questions: s.questions }).then(function (res) {
          if (res.ok) status('✔ Saved automatically', 'ok'); else status('⚠ Not saved — ' + (((res.data.errors || [])[0]) || 'try again'), 'err');
        }).catch(function () { status('⚠ Not saved — check your connection', 'err'); });
      });
    }, 500);
  }
  function renderEditor() {
    var s = activeSet, host = $('hfSetEditor');
    if (!s) { host.innerHTML = '<p class="sub">No sets yet. Use “+ New set”.</p>'; return; }
    var scale = (presets.five_point && presets.five_point.label) || '';
    var head = isPhase(s)
      ? '<div class="hfc-sethead"><h3>' + esc(s.name) + '</h3><span class="hfc-tagpill">Shared phase set</span></div>' +
        '<p class="sub">Editing a shared phase set changes it for your department only. Other HODs\' sets are never changed, and you can Reset to default at any time. Forms already created keep their questions.</p>'
      : '<div class="hfc-sethead"><input type="text" id="hfSetName" maxlength="80" value="' + esc(s.name) + '" aria-label="Set name"><span class="hfc-tagpill">Created by you</span></div>' +
        '<p class="sub">This set is private to you. Add, edit, reorder or remove questions.</p>';
    var qs = s.questions || [];
    var list = qs.map(function (q, i) {
      var isText = q.qtype === 'text';
      return '<div class="hfc-qrow"><span class="hfc-qn">' + (i + 1) + '</span><div class="hfc-qbody">' +
        '<div class="hfc-qline"><input type="text" data-t="' + i + '" value="' + esc(q.text) + '" placeholder="Type the question">' +
        '<button type="button" class="hfc-ic" data-up="' + i + '"' + (i === 0 ? ' disabled' : '') + ' aria-label="Move up">↑</button>' +
        '<button type="button" class="hfc-ic" data-dn="' + i + '"' + (i === qs.length - 1 ? ' disabled' : '') + ' aria-label="Move down">↓</button>' +
        '<button type="button" class="hfc-ic hfc-del" data-del="' + i + '" aria-label="Delete question">🗑</button></div>' +
        '<div class="hfc-qline2"><select data-ty="' + i + '"><option value="rating"' + (isText ? '' : ' selected') + '>Rating (1 to 5)</option>' +
        '<option value="text"' + (isText ? ' selected' : '') + '>Written / suggestion</option></select>' +
        '<label><input type="checkbox" data-rq="' + i + '"' + (q.is_required ? ' checked' : '') + '> Required</label></div>' +
        '<div class="hfc-hint">' + (isText ? 'Free-text answer' : '5-point scale: ' + esc(scale)) + '</div></div></div>';
    }).join('');
    var foot = '<button type="button" class="btn hfc-outline" id="hfDup">Duplicate set</button>' +
      (isPhase(s) ? '<button type="button" class="btn hfc-outline" id="hfReset">Reset to default</button>'
                  : '<button type="button" class="btn hfc-danger" id="hfDelSet">Delete set</button>');
    host.innerHTML = head + '<div id="hfSetList">' + list + '</div>' +
      '<div class="hfc-addrow"><button type="button" class="btn hfc-outline" id="hfQAdd">+ Add question</button>' +
      '<button type="button" class="btn hfc-outline" id="hfQAddText">+ Add written/suggestion question</button>' +
      '<span id="hfSetStatus" class="hfc-status"></span></div><div class="hfc-foot">' + foot + '</div>';

    host.querySelectorAll('[data-t]').forEach(function (el) {
      el.addEventListener('input', function () { s.questions[parseInt(el.getAttribute('data-t'), 10)].text = el.value; scheduleSave(s); });
    });
    host.querySelectorAll('[data-ty]').forEach(function (el) {
      el.addEventListener('change', function () {
        var q = s.questions[parseInt(el.getAttribute('data-ty'), 10)];
        q.qtype = el.value;
        q.options = el.value === 'rating' ? ((presets.five_point || {}).options || []).map(function (o) { return { label: o.label, value: o.value }; }) : [];
        if (el.value === 'text') q.is_required = false;
        scheduleSave(s); renderEditor();
      });
    });
    host.querySelectorAll('[data-rq]').forEach(function (el) {
      el.addEventListener('change', function () { s.questions[parseInt(el.getAttribute('data-rq'), 10)].is_required = el.checked; scheduleSave(s); });
    });
    function move(i, d) { var a = s.questions, j = i + d; if (j < 0 || j >= a.length) return; var t = a[i]; a[i] = a[j]; a[j] = t; scheduleSave(s); renderEditor(); }
    host.querySelectorAll('[data-up]').forEach(function (el) { el.addEventListener('click', function () { move(parseInt(el.getAttribute('data-up'), 10), -1); }); });
    host.querySelectorAll('[data-dn]').forEach(function (el) { el.addEventListener('click', function () { move(parseInt(el.getAttribute('data-dn'), 10), 1); }); });
    host.querySelectorAll('[data-del]').forEach(function (el) {
      el.addEventListener('click', function () { s.questions.splice(parseInt(el.getAttribute('data-del'), 10), 1); scheduleSave(s); renderModalKeep(); });
    });
    function add(type) {
      s.questions.push({ text: '', qtype: type, is_required: type === 'rating',
        options: type === 'rating' ? ((presets.five_point || {}).options || []).map(function (o) { return { label: o.label, value: o.value }; }) : [] });
      renderModalKeep();
      var inputs = host.querySelectorAll('[data-t]'); if (inputs.length) inputs[inputs.length - 1].focus();
    }
    $('hfQAdd').addEventListener('click', function () { add('rating'); });
    $('hfQAddText').addEventListener('click', function () { add('text'); });
    $('hfDup').addEventListener('click', function () { duplicate(s); });
    var nm = $('hfSetName');
    if (nm) {
      nm.addEventListener('change', function () {
        post(qBase + '/' + s.id + '/rename', { name: nm.value }).then(function (res) {
          if (res.ok) { s.name = nm.value.replace(/\s+/g, ' ').trim(); renderModal(); status('✔ Saved automatically', 'ok'); }
          else { status('⚠ ' + (((res.data.errors || [])[0]) || 'Could not rename'), 'err'); nm.value = s.name; }
        });
      });
    }
    if ($('hfDelSet')) $('hfDelSet').addEventListener('click', function () { removeSet(s); });
    if ($('hfReset')) $('hfReset').addEventListener('click', function () { resetSet(s); });
    if (s._focusName && nm) { nm.focus(); nm.select(); s._focusName = false; }
  }
  function renderModalKeep() { renderModal(); }
  function uniqueName(base) {
    var names = qsets.map(function (x) { return x.name.toLowerCase(); }), n = base, i = 2;
    while (names.indexOf(n.toLowerCase()) !== -1) { n = base + ' ' + i; i++; }
    return n;
  }
  function blankQ() {
    return { text: '', qtype: 'rating', is_required: true,
      options: ((presets.five_point || {}).options || []).map(function (o) { return { label: o.label, value: o.value }; }) };
  }
  function createSet(name, questions, focusName) {
    return post(qBase, { name: name }).then(function (res) {
      if (!res.ok) { alertBox(((res.data.errors || [])[0]) || 'Could not create the set.', 'error'); return; }
      var s = { id: res.data.id, name: name, is_common: false, base_key: null, shared: false, customised: false, questions: questions, _focusName: !!focusName };
      qsets.push(s); activeSet = s; renderModal();
      post(qBase + '/' + s.id + '/questions', { questions: questions });
    });
  }
  function newSet() { createSet(uniqueName('New question set'), [blankQ()], true); }
  function duplicate(s) {
    var copy = JSON.parse(JSON.stringify(s.questions || []));
    createSet(uniqueName('Copy of ' + s.name), copy, true);
  }
  function removeSet(s) {
    if (!window.confirm('Delete the set “' + s.name + '”? Forms already created keep their questions.')) return;
    post(qBase + '/' + s.id + '/delete', {}).then(function (res) {
      if (!res.ok) { status('⚠ ' + (((res.data.errors || [])[0]) || 'Could not delete'), 'err'); return; }
      qsets.splice(qsets.indexOf(s), 1);
      if (curSet === s) curSet = null;
      activeSet = qsets[0] || null; renderModal();
    });
  }
  function resetSet(s) {
    if (!s.customised) { status('This phase set is already the default.', ''); return; }
    if (!window.confirm('Reset “' + s.name + '” to the default questions? Your edits to this set will be discarded.')) return;
    post(qBase + '/reset-shared', { key: s.base_key }).then(function (res) {
      if (!res.ok) { status('⚠ ' + (((res.data.errors || [])[0]) || 'Could not reset'), 'err'); return; }
      var key = s.base_key, wasCur = curSet === s;
      fetch(qList, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.json(); }).then(function (d) {
          if (!d.ok) return;
          qsets.length = 0; (d.sets || []).forEach(function (x) { qsets.push(x); });
          activeSet = qsets.filter(function (x) { return x.id === key; })[0] || qsets[0] || null;
          if (wasCur) curSet = activeSet; else curSet = qsets.indexOf(curSet) === -1 ? null : curSet;
          renderModal();
        });
    });
  }

  renderExtras();
  loadSessions();
})();
