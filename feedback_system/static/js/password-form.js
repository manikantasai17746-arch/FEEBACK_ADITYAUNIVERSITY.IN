/* Student change-password page: show/hide eyes, strength meter, rules, live match check.
   The server still decides; "required" here mirrors it (8+ characters, both entries equal). */
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var np = $('new_password'), cp = $('confirm_password'), save = $('pw-save');
  if (!np || !cp || !save) return;
  var EYE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  var OFF = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4M6.5 6.6A17 17 0 0 0 2 12s3.6 7 10 7a10 10 0 0 0 4.2-.9"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';
  Array.prototype.forEach.call(document.querySelectorAll('.pw-eye'), function (b) {
    b.innerHTML = EYE;
    b.addEventListener('click', function () {
      var i = $(b.getAttribute('data-eye')), show = i.type === 'password';
      i.type = show ? 'text' : 'password';
      b.innerHTML = show ? OFF : EYE;
      b.setAttribute('aria-pressed', show ? 'true' : 'false');
      b.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    });
  });
  var bars = document.querySelectorAll('#pw-meter i'), rules = document.querySelectorAll('#pw-rules li');
  var colours = ['var(--st-bad)', 'var(--st-r3)', 'var(--st-r2)', 'var(--st-ok)'];
  function update() {
    var v = np.value, r = { len: v.length >= 8, num: /\d/.test(v), mix: /[a-z]/.test(v) && /[A-Z]/.test(v) };
    var score = (r.len ? 1 : 0) + (r.num ? 1 : 0) + (r.mix ? 1 : 0) + (v.length >= 12 ? 1 : 0);
    Array.prototype.forEach.call(rules, function (li) { li.classList.toggle('ok', !!r[li.getAttribute('data-rule')]); });
    Array.prototype.forEach.call(bars, function (b, i) { b.style.background = v && i < score ? colours[Math.max(0, score - 1)] : ''; });
    var m = $('pw-match'), same = cp.value && cp.value === v;
    m.textContent = cp.value ? (same ? 'Passwords match.' : 'Passwords do not match yet.') : '';
    m.className = 'hint' + (cp.value ? (same ? ' ok' : ' bad') : '');
    save.disabled = !(r.len && same && $('current_password').value);
  }
  ['current_password', 'new_password', 'confirm_password'].forEach(function (id) { $(id).addEventListener('input', update); });
  update();
})();
