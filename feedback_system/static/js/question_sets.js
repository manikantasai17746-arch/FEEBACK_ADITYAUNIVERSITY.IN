/* HOD question sets: create sets, edit their questions, auto-save every change. */
(function () {
  var app = document.getElementById('qs-app');
  if (!app) return;
  var csrf = (document.querySelector('meta[name=csrf-token]') || {}).content || '';
  var baseUrl = app.getAttribute('data-base-url');          // …/api/question-sets
  var sets = JSON.parse(document.getElementById('qs-sets-data').textContent);
  var presets = JSON.parse(document.getElementById('qs-presets-data').textContent);
  var activeId = null;
  var timers = {};

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  }
  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf,
                 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok && d.ok, data: d }; }); });
  }
  function byId(id) { return sets.filter(function (s) { return s.id === id; })[0]; }
  function common() { return sets.filter(function (s) { return s.is_common; })[0]; }
  function named() { return sets.filter(function (s) { return !s.is_common; }); }
  function presetKey(q) {
    var labels = (q.options || []).map(function (o) { return o.label; }).join('|');
    for (var k in presets) {
      if (presets[k].options.map(function (o) { return o.label; }).join('|') === labels) return k;
    }
    return 'five_point';
  }

  /* ---- auto-save ---- */
  function setStatus(set, text, cls) {
    var el = document.getElementById(set.is_common ? 'status-common' : 'status-active');
    if (!el) return;
    el.textContent = text;
    el.style.color = cls === 'err' ? '#b42318' : (cls === 'ok' ? '#17803d' : '#6b7590');
  }
  function scheduleSave(set) {
    setStatus(set, 'Saving…', '');
    clearTimeout(timers[set.id]);
    timers[set.id] = setTimeout(function () {
      post(baseUrl + '/' + set.id + '/questions', { questions: set.questions })
        .then(function (res) {
          if (res.ok) setStatus(set, '✔ Saved automatically', 'ok');
          else setStatus(set, '⚠ Not saved — ' + ((res.data.errors || [])[0] || 'try again'), 'err');
        }).catch(function () { setStatus(set, '⚠ Not saved — check your connection', 'err'); });
    }, 600);
  }

  /* ---- question editor for one set ---- */
  function renderQuestions(set, host) {
    host.innerHTML = '';
    set.questions.forEach(function (q, qi) {
      var row = document.createElement('div');
      row.className = 'q-item';
      var isRating = q.qtype === 'rating';
      var presetSel = '';
      if (isRating) {
        var cur = presetKey(q);
        presetSel = '<div class="field"><label>Answer options</label><select data-preset>' +
          Object.keys(presets).map(function (k) {
            return '<option value="' + k + '"' + (k === cur ? ' selected' : '') + '>' +
                   esc(presets[k].label) + '</option>'; }).join('') + '</select></div>';
      }
      row.innerHTML =
        '<div class="q-head"><span class="q-num">' + (qi + 1) + '</span>' +
        '<div class="btn-row" style="margin-left:auto">' +
          '<button type="button" class="icon-btn" data-up title="Move up">↑</button>' +
          '<button type="button" class="icon-btn" data-down title="Move down">↓</button>' +
          '<button type="button" class="icon-btn" data-del title="Delete question">🗑</button></div></div>' +
        '<div class="field"><label>Question text</label>' +
          '<input type="text" data-text maxlength="1000" value="' + esc(q.text) + '" ' +
          'placeholder="e.g. How well does the teacher explain the subject?"></div>' +
        '<div class="grid grid-2">' +
          '<div class="field"><label>Type</label><select data-type>' +
            '<option value="rating"' + (isRating ? ' selected' : '') + '>Rating</option>' +
            '<option value="text"' + (!isRating ? ' selected' : '') + '>Written answer</option></select></div>' +
          '<div class="field"><label>Required</label><select data-req>' +
            '<option value="1"' + (q.is_required ? ' selected' : '') + '>Required</option>' +
            '<option value="0"' + (!q.is_required ? ' selected' : '') + '>Optional</option></select></div></div>' +
        presetSel;
      row.querySelector('[data-text]').addEventListener('input', function (e) {
        q.text = e.target.value; scheduleSave(set); });
      row.querySelector('[data-req]').addEventListener('change', function (e) {
        q.is_required = e.target.value === '1'; scheduleSave(set); });
      row.querySelector('[data-type]').addEventListener('change', function (e) {
        q.qtype = e.target.value;
        q.options = q.qtype === 'rating' ? presets.five_point.options.map(function (o) { return { label: o.label, value: o.value }; }) : [];
        scheduleSave(set); renderQuestions(set, host);
      });
      var ps = row.querySelector('[data-preset]');
      if (ps) ps.addEventListener('change', function (e) {
        q.options = presets[e.target.value].options.map(function (o) { return { label: o.label, value: o.value }; });
        scheduleSave(set);
      });
      row.querySelector('[data-up]').addEventListener('click', function () {
        if (qi > 0) { set.questions.splice(qi - 1, 0, set.questions.splice(qi, 1)[0]); scheduleSave(set); renderQuestions(set, host); } });
      row.querySelector('[data-down]').addEventListener('click', function () {
        if (qi < set.questions.length - 1) { set.questions.splice(qi + 1, 0, set.questions.splice(qi, 1)[0]); scheduleSave(set); renderQuestions(set, host); } });
      row.querySelector('[data-del]').addEventListener('click', function () {
        set.questions.splice(qi, 1); scheduleSave(set); renderQuestions(set, host); });
      host.appendChild(row);
    });
    if (!set.questions.length) {
      host.insertAdjacentHTML('beforeend', '<div class="empty"><p>No questions yet.</p></div>');
    }
    var bar = document.createElement('div');
    bar.className = 'btn-row mt';
    bar.innerHTML = '<button type="button" class="btn btn-ghost btn-sm" data-add-r>＋ Add a question</button>' +
                    '<button type="button" class="btn btn-ghost btn-sm" data-add-t>＋ Add written question</button>';
    bar.querySelector('[data-add-r]').addEventListener('click', function () {
      set.questions.push({ text: '', qtype: 'rating', is_required: true,
        options: presets.five_point.options.map(function (o) { return { label: o.label, value: o.value }; }) });
      scheduleSave(set); renderQuestions(set, host); });
    bar.querySelector('[data-add-t]').addEventListener('click', function () {
      set.questions.push({ text: '', qtype: 'text', is_required: false, options: [] });
      scheduleSave(set); renderQuestions(set, host); });
    host.appendChild(bar);
  }

  /* ---- page ---- */
  function renderButtons() {
    var box = document.getElementById('qs-buttons');
    box.innerHTML = '';
    named().forEach(function (s) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn btn-sm' + (s.id === activeId ? '' : ' btn-ghost');
      b.textContent = s.name;
      b.addEventListener('click', function () { activeId = s.id; renderAll(); });
      box.appendChild(b);
    });
    var add = document.createElement('button');
    add.type = 'button'; add.className = 'btn btn-ghost btn-sm'; add.textContent = '＋ New set';
    add.addEventListener('click', function () {
      var name = window.prompt('Name of the new set (e.g. Phase 1 Questions):');
      if (!name) return;
      post(baseUrl, { name: name }).then(function (res) {
        if (!res.ok) { window.alert((res.data.errors || ['Could not create the set.'])[0]); return; }
        sets.push({ id: res.data.id, name: name.trim().replace(/\s+/g, ' '), is_common: false, questions: [] });
        activeId = res.data.id; renderAll();
      });
    });
    box.appendChild(add);
  }
  function renderAll() {
    renderButtons();
    renderQuestions(common(), document.getElementById('qs-common'));
    var card = document.getElementById('qs-active-card');
    var s = byId(activeId);
    if (!s) {
      card.style.display = 'none';
      return;
    }
    card.style.display = '';
    document.getElementById('qs-active-title').textContent = s.name;
    var actions = document.getElementById('qs-set-actions');
    actions.innerHTML = '<button type="button" class="btn btn-ghost btn-sm" data-rename>Rename set</button>' +
                        '<button type="button" class="btn btn-ghost btn-sm" data-delete>Delete set</button>';
    actions.querySelector('[data-rename]').addEventListener('click', function () {
      var name = window.prompt('New name for this set:', s.name);
      if (!name) return;
      post(baseUrl + '/' + s.id + '/rename', { name: name }).then(function (res) {
        if (!res.ok) { window.alert((res.data.errors || ['Could not rename.'])[0]); return; }
        s.name = name.trim().replace(/\s+/g, ' '); renderAll();
      });
    });
    actions.querySelector('[data-delete]').addEventListener('click', function () {
      if (!window.confirm('Delete the set "' + s.name + '"? Forms already created keep their questions.')) return;
      post(baseUrl + '/' + s.id + '/delete').then(function (res) {
        if (!res.ok) { window.alert('Could not delete the set.'); return; }
        sets = sets.filter(function (x) { return x.id !== s.id; });
        activeId = named().length ? named()[0].id : null; renderAll();
      });
    });
    renderQuestions(s, document.getElementById('qs-active'));
  }
  activeId = named().length ? named()[0].id : null;
  renderAll();
})();
