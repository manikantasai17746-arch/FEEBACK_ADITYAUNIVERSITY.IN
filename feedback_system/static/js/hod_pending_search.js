/* HOD Pending submissions: live search over the teacher rows.
   Delegated on document because the dashboard loads the panel lazily (fetch + innerHTML).
   Matches employee name, emp ID and subject name (data-q on each row).
   View-only: nothing is submitted or changed. */
(function () {
  'use strict';
  function esc(t) { return t.replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function mark(el, term) {
    if (!el) return;
    var t = el.getAttribute('data-orig');
    if (t === null) { t = el.textContent; el.setAttribute('data-orig', t); }
    var i = term ? t.toLowerCase().indexOf(term) : -1;
    el.innerHTML = i < 0 ? esc(t) :
      esc(t.slice(0, i)) + '<mark>' + esc(t.slice(i, i + term.length)) + '</mark>' + esc(t.slice(i + term.length));
  }
  function apply(box) {
    var card = box.closest('#pdSearchCard');
    if (!card) return;
    var term = box.value.trim().toLowerCase();
    var rows = [].slice.call(card.querySelectorAll('tr.pd-row'));
    var shown = 0;
    rows.forEach(function (r) {
      var ok = !term || (r.getAttribute('data-q') || '').indexOf(term) > -1;
      r.hidden = !ok;
      if (ok) shown++;
      mark(r.querySelector('td:nth-child(1) > strong'), term);
      mark(r.querySelector('td:nth-child(2)'), term);
    });
    var cnt = card.querySelector('#pdCount'), empty = card.querySelector('#pdEmpty'), clr = card.querySelector('#pdQx');
    if (cnt) cnt.textContent = term ? shown + ' of ' + rows.length + ' teacher(s)' : '';
    if (empty) {
      empty.hidden = shown > 0;
      empty.textContent = shown > 0 ? '' : 'No teachers match “' + box.value.trim() +
        '”. Try an employee ID, name or subject.';
    }
    if (clr) clr.hidden = !term;
  }
  document.addEventListener('input', function (e) {
    if (e.target && e.target.id === 'pdQ') apply(e.target);
  });
  document.addEventListener('keydown', function (e) {
    if (e.target && e.target.id === 'pdQ' && e.key === 'Enter') e.preventDefault();
    if (e.target && e.target.id === 'pdQ' && e.key === 'Escape') { e.target.value = ''; apply(e.target); }
  });
  document.addEventListener('click', function (e) {
    var summary = e.target.closest('.pd-rolls > summary');
    var dialog = document.getElementById('pdRollDialog');
    if (summary && dialog && typeof dialog.showModal === 'function') {
      e.preventDefault();
      var details = summary.parentElement;
      var rolls = details.querySelector('.pd-roll-list');
      var listBody = dialog.querySelector('.pd-roll-body');
      dialog.querySelector('[data-roll-count]').textContent = rolls.children.length;
      dialog.querySelector('#pdRollContext').textContent = details.dataset.context;
      listBody.replaceChildren(rolls.cloneNode(true));
      dialog.addEventListener('close', function () {
        summary.focus({ preventScroll: true });
      }, { once: true });
      dialog.showModal();
      listBody.scrollTop = 0;
      return;
    }
    if (dialog && e.target === dialog) { dialog.close(); return; }
    var x = e.target && e.target.closest ? e.target.closest('#pdQx') : null;
    if (!x) return;
    var box = document.getElementById('pdQ');
    if (box) { box.value = ''; apply(box); box.focus(); }
  });
})();
