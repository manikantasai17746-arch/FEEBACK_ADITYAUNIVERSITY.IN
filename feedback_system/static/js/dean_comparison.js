/* Dean Multi-round Comparison: switch between score-band panels. */
(function () {
  'use strict';
  var tabs = [].slice.call(document.querySelectorAll('.band-tab'));
  var panels = [].slice.call(document.querySelectorAll('[data-band-panel]'));
  if (!tabs.length) return;
  function show(key) {
    tabs.forEach(function (t) { t.classList.toggle('active', t.getAttribute('data-band') === key); });
    panels.forEach(function (p) { p.hidden = p.getAttribute('data-band-panel') !== key; });
  }
  tabs.forEach(function (t) {
    t.addEventListener('click', function (e) { e.preventDefault(); show(t.getAttribute('data-band')); });
  });
})();
