/* Faculty Feedback System - front-end behaviour */
(function () {
  'use strict';
  var csrf = (document.querySelector('meta[name=csrf-token]') || {}).content || '';

  /* ---------- generic back button ---------- */
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-back]');
    if (b) {
      var href = b.getAttribute('href');
      if (!href || href === '#' || href === '') {
        e.preventDefault();
        if (history.length > 1) history.back();
        else window.location.href = '/';
      }
    }
  });

  /* ---------- auto-dismiss success flashes ---------- */
  document.querySelectorAll('.flash-success').forEach(function (el) {
    setTimeout(function () { el.style.transition = 'opacity .5s'; el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 500); }, 4000);
  });

  /* ---------- client-side table filter + empty state ---------- */
  document.querySelectorAll('[data-table-filter]').forEach(function (input) {
    var table = document.querySelector(input.getAttribute('data-table-filter'));
    if (!table) return;
    input.addEventListener('input', function () {
      var q = input.value.toLowerCase().trim();
      var shown = 0;
      table.querySelectorAll('tbody tr').forEach(function (tr) {
        var match = tr.textContent.toLowerCase().indexOf(q) !== -1;
        tr.style.display = match ? '' : 'none';
        if (match) shown++;
      });
      var note = table.querySelector('.no-match');
      if (note) note.style.display = shown ? 'none' : '';
    });
  });

  /* ---------- tracking: submitted / not submitted roll lists ---------- */
  var rollPanel = document.getElementById('roll-panel');
  if (rollPanel) {
    document.querySelectorAll('[data-rolls]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        var which = btn.getAttribute('data-rolls');
        var formId = btn.getAttribute('data-form-id');
        var url = rollPanel.getAttribute('data-url') + '?form_id=' + encodeURIComponent(formId) +
                  '&which=' + encodeURIComponent(which);
        rollPanel.innerHTML = '<p class="sub">Loading…</p>';
        fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (!data.ok) { rollPanel.innerHTML = '<p class="sub">Could not load list.</p>'; return; }
            var title = which === 'not_submitted' ? 'Not submitted' : 'Submitted';
            var html = '<h3>' + title + ' — ' + data.count + ' student(s)</h3>';
            if (!data.rolls.length) {
              html += '<div class="empty"><p>No students in this list.</p></div>';
            } else {
              html += '<div class="table-wrap" style="max-height:340px"><table class="data"><thead><tr>' +
                      '<th>#</th><th>Roll Number</th></tr></thead><tbody>';
              data.rolls.forEach(function (roll, i) {
                html += '<tr><td>' + (i + 1) + '</td><td>' + roll + '</td></tr>';
              });
              html += '</tbody></table></div>';
            }
            rollPanel.innerHTML = html;
          })
          .catch(function () { rollPanel.innerHTML = '<p class="sub">Network error.</p>'; });
      });
    });
  }

  /* ==================== FORM BUILDER (single + bulk) ==================== */
  var builder = document.getElementById('builder');
  if (!builder) return;

  var mode = builder.getAttribute('data-mode') || 'single';
  var questionsEl = document.getElementById('questions');
  var errorBox = document.getElementById('builder-errors');

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function opt(value, label, current) {
    return '<option value="' + value + '"' + (String(current) === value ? ' selected' : '') +
           '>' + label + '</option>';
  }
  function newOption(label, value) { return { label: label, value: value }; }
  function defaultOptions() {
    return [newOption('Excellent', 5), newOption('Very Good', 4), newOption('Good', 3),
            newOption('Average', 2), newOption('Poor', 1)];
  }
  function showErrors(errors) {
    errorBox.innerHTML = errors.map(function (e) {
      return '<div class="flash flash-error">' + esc(e) + '</div>'; }).join('');
    errorBox.style.display = errors.length ? '' : 'none';
    if (errors.length) errorBox.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  /* ---- shared question editor ---- */
  var model = [];
  try {
    var seed = document.getElementById('initial-questions');
    if (seed && seed.textContent.trim()) model = JSON.parse(seed.textContent);
  } catch (err) { model = []; }

  function render() {
    questionsEl.innerHTML = '';
    model.forEach(function (q, qi) {
      var wrap = document.createElement('div');
      wrap.className = 'q-item';
      var needsOptions = q.qtype === 'rating' || q.qtype === 'single_choice' ||
                         q.qtype === 'multiple_choice';
      var optsHtml = '';
      if (needsOptions) {
        (q.options || []).forEach(function (o, oi) {
          optsHtml += '<div class="opt-row">' +
            '<input type="text" value="' + esc(o.label) + '" data-opt="' + oi + '" placeholder="Option label">' +
            '<button type="button" class="icon-btn" data-remove-opt="' + oi + '">✕</button></div>';
        });
        optsHtml += '<button type="button" class="btn btn-ghost btn-sm" data-add-opt>+ Add option</button>';
      }
      wrap.innerHTML =
        '<div class="q-head"><span class="q-num">' + (qi + 1) + '</span>' +
          '<div class="btn-row" style="margin-left:auto">' +
            '<button type="button" class="icon-btn" data-up title="Move up">↑</button>' +
            '<button type="button" class="icon-btn" data-down title="Move down">↓</button>' +
            '<button type="button" class="icon-btn" data-del title="Delete question">🗑</button>' +
          '</div></div>' +
        '<div class="field"><label>Question text</label>' +
          '<input type="text" data-q="text" value="' + esc(q.text) + '" placeholder="e.g. How well does the teacher explain the subject?"></div>' +
        '<div class="grid grid-2">' +
          '<div class="field"><label>Question type</label><select data-q="qtype">' +
            opt('rating', 'Rating (single choice)', q.qtype) +
            opt('single_choice', 'Single choice', q.qtype) +
            opt('multiple_choice', 'Multiple choice', q.qtype) +
            opt('text', 'Written answer / suggestion', q.qtype) +
          '</select></div>' +
          '<div class="field"><label>Required</label><select data-q="is_required">' +
            opt('1', 'Required', q.is_required ? '1' : '0') +
            opt('0', 'Optional', q.is_required ? '1' : '0') +
          '</select></div></div>' +
        (needsOptions ? '<div class="field"><label>Answer options</label>' + optsHtml + '</div>' : '') +
        '<input type="hidden" data-q="id" value="' + esc(q.id || '') + '">';

      wrap.querySelector('[data-q="text"]').addEventListener('input', function (e) { q.text = e.target.value; });
      wrap.querySelector('[data-q="qtype"]').addEventListener('change', function (e) {
        q.qtype = e.target.value;
        if (q.qtype === 'text') { q.options = []; }
        else if (!q.options || !q.options.length) { q.options = defaultOptions(); }
        render();
      });
      wrap.querySelector('[data-q="is_required"]').addEventListener('change', function (e) {
        q.is_required = e.target.value === '1';
      });
      wrap.querySelectorAll('[data-opt]').forEach(function (inp) {
        inp.addEventListener('input', function () {
          q.options[parseInt(inp.getAttribute('data-opt'), 10)].label = inp.value;
        });
      });
      wrap.querySelectorAll('[data-remove-opt]').forEach(function (b) {
        b.addEventListener('click', function () {
          q.options.splice(parseInt(b.getAttribute('data-remove-opt'), 10), 1); render();
        });
      });
      var addOpt = wrap.querySelector('[data-add-opt]');
      if (addOpt) addOpt.addEventListener('click', function () {
        q.options.push(newOption('', q.options.length + 1)); render();
      });
      wrap.querySelector('[data-up]').addEventListener('click', function () {
        if (qi > 0) { model.splice(qi - 1, 0, model.splice(qi, 1)[0]); render(); }
      });
      wrap.querySelector('[data-down]').addEventListener('click', function () {
        if (qi < model.length - 1) { model.splice(qi + 1, 0, model.splice(qi, 1)[0]); render(); }
      });
      wrap.querySelector('[data-del]').addEventListener('click', function () {
        model.splice(qi, 1); render();
      });
      questionsEl.appendChild(wrap);
    });
    if (!model.length) {
      questionsEl.innerHTML = '<div class="empty"><p>No questions yet. Use the + button below to add one.</p></div>';
    }
  }
  function addQuestion(type) {
    if (type === 'text') { model.push({ text: '', qtype: 'text', is_required: false, options: [] }); }
    else { model.push({ text: '', qtype: 'rating', is_required: true, options: defaultOptions() }); }
    render();
  }
  var addBtn = document.getElementById('add-question');
  if (addBtn) addBtn.addEventListener('click', function () { addQuestion('rating'); });
  var addTextBtn = document.getElementById('add-text');
  if (addTextBtn) addTextBtn.addEventListener('click', function () { addQuestion('text'); });
  render();

  /* ==================== BULK MODE ==================== */
  if (mode === 'bulk') {
    var bulkUrl = builder.getAttribute('data-bulk-url');
    var previewUrl = builder.getAttribute('data-preview-url');

    function val(id) { var el = document.getElementById(id); return el ? el.value : ''; }
    function collectFilters() {
      return {
        academic_year_id: val('f_academic_year_id'), semester_id: val('f_semester_id'),
        branch_id: val('f_branch_id'), timetable_id: val('f_timetable_id'),
        teacher_id: val('f_teacher_id'), subject_code: val('f_subject_code')
      };
    }
    function queryString(f) {
      return Object.keys(f).filter(function (k) { return f[k]; })
        .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(f[k]); }).join('&');
    }

    var prevBtn = document.getElementById('bulk-preview');
    var prevOut = document.getElementById('bulk-preview-result');
    if (prevBtn) prevBtn.addEventListener('click', function () {
      prevOut.textContent = 'Checking…';
      fetch(previewUrl + '?' + queryString(Object.assign(collectFilters(), { title_pattern: val('title_pattern') })),
            { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          prevOut.innerHTML = '<strong>' + d.matched + '</strong> session(s) match, ' +
            '<strong>' + d.to_create + '</strong> will get a new form (' +
            d.existing + ' already exist).';
        })
        .catch(function () { prevOut.textContent = 'Could not preview.'; });
    });

    var genBtn = document.getElementById('bulk-generate');
    var result = document.getElementById('bulk-result');
    if (genBtn) genBtn.addEventListener('click', function () {
      if (!model.length) { showErrors(['Add at least one question to the template.']); return; }
      var status = (document.querySelector('input[name=bulk_status]:checked') || {}).value || 'draft';
      genBtn.disabled = true;
      fetch(bulkUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf,
                   'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ filters: collectFilters(), title_pattern: val('title_pattern'),
                               status: status, questions: model })
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          genBtn.disabled = false;
          if (!res.ok || !res.d.ok) {
            showErrors(res.d.errors || ['Could not generate forms.']); return;
          }
          showErrors([]);
          var d = res.d;
          var html = '<div class="flash flash-success">Generated <strong>' + d.created +
            '</strong> form(s)' + (d.opened ? ' (' + d.opened + ' published/open)' : '') +
            '. Skipped ' + d.skipped + ' session(s) that already had a form.</div>';
          if (d.errors && d.errors.length) {
            html += d.errors.map(function (e) { return '<div class="flash flash-error">' + esc(e) + '</div>'; }).join('');
          }
          html += '<div class="btn-row"><a class="btn" href="/admin/forms">View forms</a>' +
                  '<a class="btn btn-ghost" href="/admin/forms/bulk">Generate more</a></div>';
          result.innerHTML = html;
          result.style.display = '';
          result.scrollIntoView({ behavior: 'smooth', block: 'center' });
        })
        .catch(function () { genBtn.disabled = false; showErrors(['Network error while generating.']); });
    });
    return;
  }

  /* ==================== SINGLE MODE ==================== */
  var saveUrl = builder.getAttribute('data-save-url');
  var statusUrlTpl = builder.getAttribute('data-status-url');
  var previewUrl = builder.getAttribute('data-preview-url');
  var apiTimetables = builder.getAttribute('data-api-timetables') || '/admin/api/timetables';
  var apiSessions = builder.getAttribute('data-api-sessions') || '/admin/api/sessions';
  var apiSubjects = builder.getAttribute('data-api-subjects') || '/admin/api/subjects';
  var editUrlTpl = builder.getAttribute('data-edit-url') || '/admin/forms/{id}/edit';
  var formsListUrl = builder.getAttribute('data-forms-list-url') || '/admin/forms';
  var formId = builder.getAttribute('data-form-id') || '';
  var selTimetable = builder.getAttribute('data-selected-timetable') || '';
  var selSession = builder.getAttribute('data-selected-session') || '';
  var selSubject = builder.getAttribute('data-selected-subject') || '';
  var scope = builder.getAttribute('data-scope') || 'subject';
  if (scope !== 'session' && scope !== 'subject') scope = 'subject';

  var ttSel = document.getElementById('timetable_id');   // admin builder (single select)
  // HOD builder: multi-select with search + tick marks
  var ttMS = (document.getElementById('timetable_ms') && window.MultiSelect)
    ? MultiSelect.create(document.getElementById('timetable_ms'), {
        allLabel: 'No Preference (All Timetables)', noun: 'timetables',
        emptyText: 'Select year, semester & department first.' }) : null;
  var sSel = document.getElementById('session_id');
  var subjSel = document.getElementById('subject_code');
  var sessionField = document.getElementById('session-field');
  var subjectField = document.getElementById('subject-field');
  var previewPanel = document.getElementById('subject-preview');
  var modeHint = document.getElementById('mode-hint');
  var ttHint = document.getElementById('timetable-hint');

  function modeBtn(m) { return builder.querySelector('.mode-btn[data-mode="' + m + '"]'); }
  function val(id) { var el = document.getElementById(id); return el ? el.value : ''; }
  function qs(obj) {
    var parts = [];
    Object.keys(obj).forEach(function (k) {
      var v = obj[k];
      if (Array.isArray(v)) { v.forEach(function (x) { parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(x)); }); }
      else if (v) { parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v)); }
    });
    return parts.join('&');
  }
  /* ticked timetables (array of id strings) */
  function ttValues() {
    if (ttMS) return ttMS.values();
    return ttSel && ttSel.value ? [ttSel.value] : [];
  }
  /* Subject-only mode: ticking every timetable is the same as "No Preference". */
  function ttEffective() {
    var v = ttValues();
    if (ttMS && scope === 'subject' && ttMS.count() > 1 && v.length === ttMS.count()) return [];
    return v;
  }
  function currentFilters() {
    return {
      academic_year_id: val('academic_year_id'), semester_id: val('semester_id'),
      branch_id: val('branch_id'), timetable_id: ttEffective(),
      subject_code: subjSel ? subjSel.value : ''
    };
  }

  function applyScope() {
    builder.setAttribute('data-scope', scope);
    if (sessionField) sessionField.style.display = scope === 'session' ? '' : 'none';
    if (subjectField) subjectField.style.display = scope === 'subject' ? '' : 'none';
    ['session', 'subject'].forEach(function (m) {
      var b = modeBtn(m);
      if (!b) return;
      var active = m === scope;
      b.classList.toggle('is-active', active);
      b.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    if (modeHint) modeHint.innerHTML = scope === 'subject'
      ? 'Subject-only mode is recommended: it creates <strong>one</strong> form for every ' +
        'eligible student of the subject instead of one form per timetable.'
      : 'Normal mode targets a single subject / teacher / period (one teaching session).';
    if (ttHint) {
      if (ttMS) {
        ttHint.innerHTML = scope === 'subject'
          ? 'Tick the timetables you need, or leave all unticked for <strong>No Preference (All Timetables)</strong>.'
          : 'Tick the timetable(s) that contain the class period.';
      } else {
        ttHint.innerHTML = scope === 'subject'
          ? 'Choose <strong>No Preference (All Timetables)</strong> to include every matching timetable.'
          : 'Pick the timetable that contains the class period.';
      }
    }
    if (ttMS) ttMS.setLabels(scope === 'subject' ? 'No Preference (All Timetables)' : 'Select timetable(s)', 'timetables');
    if (previewPanel && scope !== 'subject') previewPanel.style.display = 'none';
  }

  function loadTimetables() {
    var params = new URLSearchParams({
      year_id: val('academic_year_id'), semester_id: val('semester_id'), branch_id: val('branch_id')
    });
    if (ttSel) ttSel.innerHTML = '<option value="">Loading…</option>';
    fetch(apiTimetables + '?' + params.toString(),
          { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (ttMS) {
          ttMS.setItems(d.timetables.map(function (t) {
            return { value: String(t.id), label: t.branch + ' · ' + t.code };
          }), selTimetable ? [selTimetable] : null);
          ttMS.setEmptyText('No timetables found for these filters.');
          selTimetable = '';
        } else {
          var html = scope === 'subject'
            ? '<option value="">No Preference (All Timetables)</option>'
            : '<option value="">— Select timetable —</option>';
          d.timetables.forEach(function (t) {
            html += '<option value="' + t.id + '">' + esc(t.branch + ' · ' + t.code) + '</option>';
          });
          ttSel.innerHTML = html;
          if (selTimetable) { ttSel.value = selTimetable; }
        }
        afterTimetableChange();
      });
  }

  function loadSessions() {
    var ttIds = ttValues();
    if (!ttIds.length) { sSel.innerHTML = '<option value="">— Select a timetable first —</option>'; return; }
    sSel.innerHTML = '<option value="">Loading…</option>';
    fetch(apiSessions + '?' + qs({ timetable_id: ttIds }),
          { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        sSel.innerHTML = '<option value="">— Select subject / teacher / period —</option>';
        d.sessions.forEach(function (s) {
          sSel.innerHTML += '<option value="' + s.id + '">' + esc(s.label) + '</option>';
        });
        if (selSession) { sSel.value = selSession; }
      });
  }

  function loadSubjects() {
    if (!subjSel) return;
    subjSel.innerHTML = '<option value="">Loading…</option>';
    fetch(apiSubjects + '?' + qs(currentFilters()),
          { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var html = '<option value="">— Select subject —</option>';
        d.subjects.forEach(function (s) {
          html += '<option value="' + esc(s.code) + '">' + esc(s.name + ' (' + s.code + ')') + '</option>';
        });
        subjSel.innerHTML = html;
        if (selSubject) { subjSel.value = selSubject; }
        else if (d.subjects.length === 1) { subjSel.value = d.subjects[0].code; }
        loadSubjectPreview();
      });
  }

  function loadSubjectPreview() {
    if (!previewPanel || scope !== 'subject') return;
    if (!subjSel.value) { previewPanel.style.display = 'none'; return; }
    previewPanel.style.display = '';
    previewPanel.innerHTML = '<p class="sub">Checking eligible students…</p>';
    fetch(previewUrl + '?' + qs(currentFilters()),
          { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var html = '<div class="btn-row" style="justify-content:space-between">' +
          '<strong>Preview · ' + esc(d.subject_name || d.subject_code || 'Subject') + '</strong></div>' +
          '<div class="tiles" style="margin-top:10px">' +
          '<div class="tile ok"><div class="num">' + d.eligible + '</div><div class="lbl">Eligible students</div></div>' +
          '<div class="tile"><div class="num">' + d.sessions + '</div><div class="lbl">Matching sessions</div></div>' +
          '<div class="tile"><div class="num">' + d.timetables + '</div><div class="lbl">Timetables</div></div>' +
          '<div class="tile"><div class="num">' + d.teachers + '</div><div class="lbl">Teachers</div></div>' +
          '</div>';
        if (!d.eligible) {
          html += '<div class="flash flash-warning" style="margin-top:10px">No eligible students ' +
            'found for this configuration. Publishing will be blocked until at least one student ' +
            'is eligible.</div>';
        }
        if (d.existing_form_id) {
          html += '<div class="flash flash-warning" style="margin-top:10px">A form for this exact ' +
            'configuration already exists (<a href="/admin/forms/' + d.existing_form_id + '/edit">#' +
            d.existing_form_id + ' — ' + esc(d.existing_form_title || '') + '</a>). Publish that one ' +
            'instead of creating a duplicate.</div>';
        }
        previewPanel.innerHTML = html;
      })
      .catch(function () { previewPanel.innerHTML = '<p class="sub">Could not load the preview.</p>'; });
  }

  function afterTimetableChange() {
    if (scope === 'subject') { loadSubjects(); }
    else { loadSessions(); }
  }

  ['academic_year_id', 'semester_id', 'branch_id'].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('change', loadTimetables);
  });
  if (ttSel) ttSel.addEventListener('change', afterTimetableChange);
  if (ttMS) ttMS.onChange(afterTimetableChange);
  if (subjSel) subjSel.addEventListener('change', loadSubjectPreview);
  ['session', 'subject'].forEach(function (m) {
    var b = modeBtn(m);
    if (b) b.addEventListener('click', function () {
      if (scope === m) return;
      scope = m;
      applyScope();
      if (scope === 'subject') { loadSubjects(); }
      else if (ttValues().length) { loadSessions(); }
    });
  });

  function collect() {
    var catEl = document.getElementById('category');
    var tts = ttEffective();
    var body = {
      form_id: formId || null,
      scope: scope,
      category: catEl ? catEl.value : 'main',
      title: document.getElementById('title').value,
      instructions: document.getElementById('instructions').value,
      academic_year_id: val('academic_year_id'),
      semester_id: val('semester_id'),
      branch_id: val('branch_id'),
      timetable_id: tts.length === 1 ? tts[0] : '',
      subject_code: subjSel ? subjSel.value : '',
      session_id: scope === 'session' ? sSel.value : '',
      questions: model
    };
    if (ttMS) body.timetable_ids = tts;
    return body;
  }

  function save() {
    showErrors([]);
    return fetch(saveUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf,
                 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify(collect())
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok || !res.data.ok) {
          showErrors(res.data.errors || ['Could not save the form. Please check the fields.']);
          return null;
        }
        formId = res.data.form_id;
        builder.setAttribute('data-form-id', formId);
        if (history.replaceState) { history.replaceState(null, '', editUrlTpl.replace('{id}', formId)); }
        return formId;
      })
      .catch(function () { showErrors(['Network error while saving.']); return null; });
  }

  var saveBtn = document.getElementById('save-draft');
  if (saveBtn) saveBtn.addEventListener('click', function () {
    save().then(function (id) {
      if (id) {
        showErrors([]);
        var t = document.getElementById('save-toast');
        if (t) { t.style.display = ''; t.textContent = 'Draft saved (form #' + id + ').';
          setTimeout(function () { t.style.display = 'none'; }, 3500); }
      }
    });
  });

  function validateSelection() {
    var errors = [];
    if (!document.getElementById('title').value.trim()) errors.push('A form title is required.');
    if (!val('academic_year_id')) errors.push('Select an academic year.');
    if (!val('semester_id')) errors.push('Select a semester.');
    if (!val('branch_id')) errors.push('Select a branch.');
    if (scope === 'subject') {
      if (!subjSel || !subjSel.value) errors.push('Select a subject.');
    } else if (!sSel.value) {
      errors.push('Select a subject, teacher and period.');
    }
    if (!model.length) errors.push('Add at least one question before publishing.');
    return errors;
  }

  var publishBtn = document.getElementById('publish-form');
  if (publishBtn) publishBtn.addEventListener('click', function () {
    var errors = validateSelection();
    if (errors.length) { showErrors(errors); return; }
    save().then(function (id) {
      if (!id) return;
      var f = document.createElement('form');
      f.method = 'POST';
      f.action = statusUrlTpl.replace('/0/status', '/' + id + '/status');
      f.innerHTML = '<input type="hidden" name="_csrf" value="' + csrf + '">' +
                    '<input type="hidden" name="status" value="open">' +
                    '<input type="hidden" name="next" value="' + formsListUrl + '">';
      document.body.appendChild(f);
      f.submit();
    });
  });

  applyScope();
  if (document.getElementById('branch_id').value) { loadTimetables(); }
})();
