(function () {
  'use strict';
  var root = document.getElementById('hfCreate');
  if (!root) return;
  var apiSessions = root.getAttribute('data-api-sessions');
  var apiPreview = root.getAttribute('data-api-preview');
  var apiCreate = root.getAttribute('data-api-create');
  var qsets = [];
  try { qsets = JSON.parse(root.getAttribute('data-question-sets') || '[]'); } catch (e) { qsets = []; }
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  var selected = {};
  var previews = [];
  function $(id) { return document.getElementById(id); }
  function alertBox(msg, kind) {
    var el = $('hfAlert'); if (!el) return;
    if (!msg) { el.hidden = true; el.textContent = ''; return; }
    el.hidden = false; el.className = 'hf-alert' + (kind ? ' is-' + kind : ''); el.textContent = msg;
  }
  function goStep(n) {
    root.querySelectorAll('.hf-step').forEach(function (b) { b.classList.toggle('is-active', String(b.getAttribute('data-step')) === String(n)); });
    root.querySelectorAll('.hf-panel').forEach(function (p) {
      var on = String(p.getAttribute('data-panel')) === String(n); p.hidden = !on; p.classList.toggle('is-active', on);
    });
  }
  function selectedIds() { return Object.keys(selected).map(function (k) { return parseInt(k, 10); }); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function renderTree(subjects) {
    var box = $('hfSessionTree');
    if (!subjects || !subjects.length) { box.innerHTML = '<p class="sub">No sessions match the filters.</p>'; return; }
    var html = '';
    subjects.forEach(function (sub) {
      html += '<div class="hf-sub-block"><strong>' + esc(sub.subject_code) + '</strong> <span class="hf-muted">' + esc(sub.subject_name || '') + '</span>';
      (sub.teachers || []).forEach(function (t) {
        html += '<div class="hf-teacher-name" style="margin:.4rem 0 .2rem">' + esc(t.teacher_name) + ' <span class="hf-muted">' + esc(t.teacher_emp_code || '') + '</span></div>';
        (t.sections || []).forEach(function (s) {
          var id = s.session_id;
          var checked = selected[id] ? ' checked' : '';
          var live = s.live_main_forms ? (' · ' + s.live_main_forms + ' live form(s)') : '';
          html += '<label class="hf-check"><input type="checkbox" data-sid="' + id + '"' + checked + '><span>Section ' + esc(s.section_code) + ' <span class="hf-sec-meta">Room ' + esc(s.room || '—') + ' · ' + (s.eligible_count || 0) + ' eligible' + live + '</span></span></label>';
          if (selected[id]) selected[id] = { session_id: id, subject_code: sub.subject_code, subject_name: sub.subject_name, teacher_name: t.teacher_name, section_code: s.section_code, eligible_count: s.eligible_count || 0 };
        });
      });
      html += '</div>';
    });
    box.innerHTML = html;
    box.querySelectorAll('input[type=checkbox][data-sid]').forEach(function (cb) {
      cb.addEventListener('change', function () {
        var id = parseInt(cb.getAttribute('data-sid'), 10);
        if (cb.checked) selected[id] = selected[id] || { session_id: id }; else delete selected[id];
        $('hfToPreview').disabled = selectedIds().length === 0;
      });
    });
    $('hfToPreview').disabled = selectedIds().length === 0;
  }
  function loadSessions() {
    alertBox('Loading sessions…', 'loading');
    var q = new URLSearchParams();
    var s = ($('hfSubject') || {}).value; if (s) q.set('subject', s);
    var sec = ($('hfSection') || {}).value; if (sec) q.set('section', sec);
    var f = ($('hfFaculty') || {}).value; if (f) q.set('faculty', f);
    fetch(apiSessions + (q.toString() ? ('?' + q.toString()) : ''), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); }).then(function (data) {
        alertBox(''); if (!data.ok) { alertBox(data.error || 'Could not load sessions.', 'error'); return; }
        renderTree(data.subjects || []);
      }).catch(function () { alertBox('Network error while loading sessions.', 'error'); });
  }
  function loadPreview() {
    var ids = selectedIds(); if (!ids.length) return;
    alertBox('Loading eligible students…', 'loading');
    var q = ids.map(function (id) { return 'session_id=' + encodeURIComponent(id); }).join('&');
    fetch(apiPreview + '?' + q, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json().then(function (d) { return { status: r.status, data: d }; }); })
      .then(function (res) {
        if (!res.data.ok) { alertBox(res.data.error || 'Preview failed.', 'error'); return; }
        alertBox(''); previews = res.data.previews || [];
        var html = '';
        previews.forEach(function (p) {
          html += '<div class="hf-preview-block"><h3>' + esc(p.subject_code) + ' · Section ' + esc(p.section_code || '') + ' · ' + esc(p.teacher_name || '') + '</h3>';
          html += '<p class="sub">' + (p.eligible_count || 0) + ' eligible student(s)</p><div class="hf-student-list">';
          (p.students || []).forEach(function (st) { html += '<div><strong>' + esc(st.roll) + '</strong> — ' + esc(st.name) + '</div>'; });
          if (!(p.students || []).length) html += '<div class="sub">No eligible students.</div>';
          html += '</div></div>';
        });
        $('hfPreview').innerHTML = html || '<p class="sub">No data.</p>'; goStep(2);
      }).catch(function () { alertBox('Network error while loading preview.', 'error'); });
  }
  function fillQuestionSets() {
    var sel = $('hfQSet'); sel.innerHTML = '';
    if (!qsets.length) { sel.innerHTML = '<option value="">No question sets available</option>'; return; }
    qsets.forEach(function (s) {
      var o = document.createElement('option'); o.value = s.id;
      o.textContent = s.name + (s.shared ? ' (shared)' : '') + (s.customised ? ' (customised)' : '');
      sel.appendChild(o);
    });
    showQPreview();
  }
  function showQPreview() {
    var id = parseInt(($('hfQSet') || {}).value, 10); var set = null;
    qsets.forEach(function (s) { if (parseInt(s.id, 10) === id) set = s; });
    var box = $('hfQPreview');
    if (!set || !(set.questions || []).length) { box.textContent = 'No questions in this set.'; return; }
    box.innerHTML = '<strong>' + set.questions.length + ' question(s)</strong><ol style="margin:.35rem 0 0 1.1rem">' +
      set.questions.map(function (q) { return '<li>' + esc(q.text) + '</li>'; }).join('') + '</ol>';
  }
  function buildSummary() {
    var html = '<p><strong>' + selectedIds().length + '</strong> section(s) selected.</p><ul>';
    previews.forEach(function (p) { html += '<li>' + esc(p.subject_code) + ' / ' + esc(p.section_code || '') + ' — ' + (p.eligible_count || 0) + ' eligible</li>'; });
    html += '</ul>';
    var setName = ($('hfQSet').selectedOptions[0] || {}).textContent || '—';
    html += '<p>Question set: <strong>' + esc(setName) + '</strong></p>';
    var title = ($('hfTitle').value || '').trim(); if (title) html += '<p>Title: <strong>' + esc(title) + '</strong></p>';
    var dl = ($('hfDeadline').value || '').trim(); html += '<p>Deadline: <strong>' + esc(dl || 'None') + '</strong></p>';
    $('hfSummary').innerHTML = html;
  }
  function submitCreate(publish) {
    var ids = selectedIds(); if (!ids.length) { alertBox('Select at least one section.', 'error'); return; }
    var qsid = parseInt(($('hfQSet') || {}).value, 10); if (!qsid) { alertBox('Choose a question set.', 'error'); return; }
    alertBox(publish ? 'Publishing…' : 'Saving draft…', 'loading');
    fetch(apiCreate, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify({
        session_ids: ids, question_set_id: qsid,
        title: ($('hfTitle').value || '').trim(),
        instructions: ($('hfInstructions').value || ''),
        deadline_at: ($('hfDeadline').value || '').trim() || null,
        publish: !!publish
      })
    }).then(function (r) { return r.json().then(function (d) { return { status: r.status, data: d }; }); })
      .then(function (res) {
        var d = res.data;
        if (!d.ok) { alertBox(d.error || ((d.errors && d.errors[0] && d.errors[0].error) || 'Create failed.'), 'error'); return; }
        var n = (d.created || []).length; var errn = (d.errors || []).length;
        alertBox(n + ' form(s) ' + (publish ? 'published' : 'saved as draft') + (errn ? ('; ' + errn + ' failed') : '') + '.', errn ? 'error' : 'ok');
        if (n && !errn) window.setTimeout(function () { window.location.href = '/hod/forms'; }, 900);
      }).catch(function () { alertBox('Network error while creating forms.', 'error'); });
  }
  ($('hfReload') || {}).addEventListener('click', loadSessions);
  ['hfSubject', 'hfSection', 'hfFaculty'].forEach(function (id) {
    var el = $(id); if (!el) return; var t;
    el.addEventListener('input', function () { window.clearTimeout(t); t = window.setTimeout(loadSessions, 300); });
  });
  ($('hfToPreview') || {}).addEventListener('click', loadPreview);
  ($('hfToQuestions') || {}).addEventListener('click', function () { fillQuestionSets(); goStep(3); });
  ($('hfQSet') || {}).addEventListener('change', showQPreview);
  ($('hfToConfirm') || {}).addEventListener('click', function () {
    if (!(parseInt(($('hfQSet') || {}).value, 10))) { alertBox('Choose a question set.', 'error'); return; }
    alertBox(''); buildSummary(); goStep(4);
  });
  ($('hfSaveDraft') || {}).addEventListener('click', function () { submitCreate(false); });
  ($('hfPublish') || {}).addEventListener('click', function () { submitCreate(true); });
  root.querySelectorAll('[data-back]').forEach(function (b) { b.addEventListener('click', function () { goStep(b.getAttribute('data-back')); }); });
  try {
    var pre = new URLSearchParams(window.location.search).get('session_id');
    if (pre) selected[parseInt(pre, 10)] = { session_id: parseInt(pre, 10) };
  } catch (e) {}
  loadSessions();
})();
