(function () {
  'use strict';
  var root = document.querySelector('#hfCreate, #hfLabCreate');
  if (!root) return;
  var apiSessions = root.getAttribute('data-api-sessions');
  var apiPreview = root.getAttribute('data-api-preview');
  var apiCreate = root.getAttribute('data-api-create');
  var formsUrl = root.getAttribute('data-forms-url');
  var activeSubject = '';
  var sessionRequest = 0;
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
    root.querySelectorAll('.hf-step').forEach(function (step) {
      var on = step.getAttribute('data-step') === String(n);
      step.classList.toggle('is-active', on);
      if (on) step.setAttribute('aria-current', 'step');
      else step.removeAttribute('aria-current');
    });
    root.querySelectorAll('.hf-panel').forEach(function (p) {
      var on = String(p.getAttribute('data-panel')) === String(n); p.hidden = !on; p.classList.toggle('is-active', on);
    });
  }
  function selectedIds() { return Object.keys(selected).map(function (k) { return parseInt(k, 10); }); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function updateMainSelection() {
    var ids = selectedIds();
    $('hfSelectionCount').textContent = ids.length;
    $('hfSelection').hidden = !ids.length;
    $('hfSelection').innerHTML = ids.map(function (id) {
      var row = selected[id];
      return '<li>' + esc(row.subject_code ? row.subject_code + ' · ' + row.section_code : 'Section #' + id) + '</li>';
    }).join('');
    $('hfToPreview').disabled = !ids.length;
    $('hfClearSelection').disabled = !ids.length;
  }

  function renderMainSelector(subjects) {
    var subjectBox = $('hfSubjects');
    var table = $('hfSessionTree');
    var selectVisible = $('hfSelectVisible');
    var clear = $('hfClearSelection');
    var rows = [];

    subjects.forEach(function (subject) {
      (subject.teachers || []).forEach(function (teacher) {
        (teacher.sections || []).forEach(function (section) {
          var row = {
            session_id: section.session_id,
            subject_code: subject.subject_code,
            section_code: section.section_code,
            teacher_name: teacher.teacher_name,
            room: section.room,
            eligible_count: section.eligible_count || 0,
            live_forms: section.live_main_forms || 0
          };
          rows.push(row);
          if (selected[row.session_id]) selected[row.session_id] = row;
        });
      });
    });

    if (!subjects.some(function (subject) { return subject.subject_code === activeSubject; })) {
      activeSubject = '';
    }

    function renderRows() {
      var visible = rows.filter(function (row) { return !activeSubject || row.subject_code === activeSubject; });
      selectVisible.disabled = !visible.length;
      selectVisible.onclick = function () {
        visible.forEach(function (row) { selected[row.session_id] = row; });
        table.querySelectorAll('input').forEach(function (input) { input.checked = true; });
        updateMainSelection();
      };
      if (!visible.length) {
        table.innerHTML = '<p class="sub">No sessions match the filters.</p>';
        return;
      }
      table.innerHTML = '<table class="hf-table"><thead><tr><th scope="col">Section</th>' +
        '<th scope="col">Faculty</th><th scope="col">Room</th><th scope="col">Eligible</th>' +
        '<th scope="col">Live forms</th></tr></thead><tbody>' +
        visible.map(function (row) {
          return '<tr><td><label class="hf-picker-section"><input type="checkbox"' +
            (selected[row.session_id] ? ' checked' : '') + '> <span>' + esc(row.section_code) +
            '<small>' + esc(row.subject_code) + '</small></span></label></td><td>' + esc(row.teacher_name) +
            '</td><td>' + esc(row.room || '—') + '</td><td>' + row.eligible_count + '</td><td>' +
            row.live_forms + '</td></tr>';
        }).join('') + '</tbody></table>';
      table.querySelectorAll('input').forEach(function (input, index) {
        input.addEventListener('change', function () {
          var row = visible[index];
          if (input.checked) selected[row.session_id] = row;
          else delete selected[row.session_id];
          updateMainSelection();
        });
      });
    }

    subjectBox.replaceChildren();
    [{subject_code: '', subject_name: 'All subjects'}].concat(subjects).forEach(function (subject) {
      var button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(activeSubject === subject.subject_code));
      button.innerHTML = subject.subject_code
        ? '<strong>' + esc(subject.subject_code) + '</strong><small>' + esc(subject.subject_name) + '</small>'
        : 'All subjects';
      button.addEventListener('click', function () {
        var previous = subjectBox.querySelector('[aria-pressed=true]');
        if (previous) previous.setAttribute('aria-pressed', 'false');
        activeSubject = subject.subject_code;
        button.setAttribute('aria-pressed', 'true');
        renderRows();
      });
      subjectBox.appendChild(button);
    });

    clear.onclick = function () {
      selected = {};
      table.querySelectorAll('input').forEach(function (input) { input.checked = false; });
      updateMainSelection();
    };
    renderRows();
    updateMainSelection();
  }

  function renderLabTree(subjects) {
    var box = $('hfSessionTree');
    if (!subjects.length) { box.innerHTML = '<p class="sub">No sessions match the filters.</p>'; return; }
    box.innerHTML = subjects.map(function (subject) {
      var markup = '<div class="hf-sub-block"><strong>' + esc(subject.subject_code) +
        '</strong> <span class="hf-muted">' + esc(subject.subject_name) + '</span>';
      (subject.sessions || []).forEach(function (session) {
        var id = session.session_id;
        var faculty = session.display_faculty_name || session.teacher_name || '';
        var live = session.live_lab_forms ? ' · ' + session.live_lab_forms + ' live form(s)' : '';
        markup += '<label class="hf-check"><input type="checkbox" data-sid="' + id + '"' +
          (selected[id] ? ' checked' : '') + '><span>Section ' + esc(session.section_code) + ' · ' + esc(faculty) +
          ' <span class="hf-sec-meta">Room ' + esc(session.room || '—') + ' · ' +
          (session.eligible_count || 0) + ' eligible' + live + '</span></span></label>';
      });
      return markup + '</div>';
    }).join('');
    box.querySelectorAll('input[type=checkbox][data-sid]').forEach(function (check) {
      check.addEventListener('change', function () {
        var id = parseInt(check.dataset.sid, 10);
        if (check.checked) selected[id] = { session_id: id };
        else delete selected[id];
        $('hfToPreview').disabled = !selectedIds().length;
      });
    });
    $('hfToPreview').disabled = !selectedIds().length;
  }
  function loadSessions() {
    var requestNumber = ++sessionRequest;
    alertBox('Loading sessions…', 'loading');
    var q = new URLSearchParams();
    var s = ($('hfSubject') || {}).value; if (s) q.set('subject', s);
    var sec = ($('hfSection') || {}).value; if (sec) q.set('section', sec);
    var f = ($('hfFaculty') || {}).value; if (f) q.set('faculty', f);
    fetch(apiSessions + (q.toString() ? ('?' + q.toString()) : ''), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); }).then(function (data) {
        if (requestNumber !== sessionRequest) return;
        alertBox(''); if (!data.ok) { alertBox(data.error || 'Could not load sessions.', 'error'); return; }
        if ($('hfSubjects')) renderMainSelector(data.subjects || []);
        else renderLabTree(data.subjects || []);
      }).catch(function () {
        if (requestNumber === sessionRequest) alertBox('Network error while loading sessions.', 'error');
      });
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
        if (n && !errn) window.setTimeout(function () { window.location.href = formsUrl; }, 900);
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
    var pre = parseInt(new URLSearchParams(window.location.search).get('session_id'), 10);
    if (pre > 0) selected[pre] = { session_id: pre };
  } catch (e) {}
  loadSessions();
})();
