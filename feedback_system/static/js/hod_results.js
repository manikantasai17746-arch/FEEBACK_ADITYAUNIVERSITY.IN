/* HOD Feedback Results: "Select all", "Also publish forms that are still open",
   and live counts. Uses document-level delegation, so it also works when the
   results page is loaded as a fragment inside the HOD dashboard. */
(function () {
  if (window.__hrResultsBound) return;
  window.__hrResultsBound = true;

  function form() { return document.getElementById('hrForm'); }
  function boxes(f) { return [].slice.call(f.querySelectorAll('.hr-chk')); }

  function sync() {
    var f = form(); if (!f) return;
    var all = document.getElementById('hrAll');
    var cnt = document.getElementById('hrCount');
    var bulk = document.getElementById('hrBulk');
    var bs = boxes(f);
    var enabled = bs.filter(function (c) { return !c.disabled; });
    var n = enabled.filter(function (c) { return c.checked; }).length;
    if (cnt) cnt.textContent = n + ' of ' + bs.length + ' selected';
    if (bulk) { bulk.textContent = 'Publish selected (' + n + ')'; bulk.disabled = (n === 0); }
    if (all) {
      all.checked = enabled.length > 0 && n === enabled.length;
      all.indeterminate = n > 0 && n < enabled.length;
    }
  }

  document.addEventListener('change', function (e) {
    var t = e.target, f = form();
    if (!f) return;
    if (t.id === 'hrAll') {
      boxes(f).forEach(function (c) { if (!c.disabled) c.checked = t.checked; });
      sync();
    } else if (t.id === 'hrOpen') {
      boxes(f).forEach(function (c) {
        if (c.getAttribute('data-status') === 'open') {
          c.disabled = !t.checked;
          if (!t.checked) c.checked = false;
        }
      });
      sync();
    } else if (t.classList && t.classList.contains('hr-chk')) {
      sync();
    }
  });
})();
