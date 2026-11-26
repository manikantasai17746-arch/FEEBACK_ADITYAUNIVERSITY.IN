/* Score-band PDF download: ask for an optional notice to the teachers first.
   Any link with class "js-band-pdf" opens the dialog; "Skip" downloads without
   a notice, "Download PDF" adds the typed notice to the file. Without
   JavaScript the link still downloads the PDF directly (no notice). */
(function () {
  'use strict';
  var MAX = 1000, dlg, ta, count, pending;

  function build() {
    dlg = document.createElement('dialog');
    dlg.setAttribute('aria-labelledby', 'bpnTitle');
    dlg.style.cssText = 'border:0;border-radius:14px;padding:1.25rem 1.4rem;width:min(520px,92vw);' +
      'box-shadow:0 20px 60px rgba(0,0,0,.35);color:inherit;background:var(--card,#fff)';
    dlg.innerHTML =
      '<h3 id="bpnTitle" style="margin:0 0 .35rem">Notice to the teachers</h3>' +
      '<p style="margin:0 0 .7rem;font-size:.9rem;opacity:.75">Optional. Whatever you write is printed at the top of ' +
      'the PDF. Choose <strong>Skip</strong> to download without a notice.</p>' +
      '<textarea rows="6" maxlength="' + MAX + '" style="width:100%;box-sizing:border-box;padding:.6rem;' +
      'border:1px solid #cbd5e1;border-radius:10px;font:inherit" ' +
      'placeholder="e.g. Teachers in this list should meet the HOD on Monday to discuss improvement plans."></textarea>' +
      '<div style="display:flex;justify-content:space-between;align-items:center;gap:.5rem;margin-top:.7rem;flex-wrap:wrap">' +
      '<span class="bpn-count" style="font-size:.8rem;opacity:.7"></span>' +
      '<span style="display:flex;gap:.5rem">' +
      '<button type="button" class="btn btn-ghost bpn-cancel">Cancel</button>' +
      '<button type="button" class="btn btn-ghost bpn-skip">Skip</button>' +
      '<button type="button" class="btn btn-accent bpn-ok">Download PDF</button></span></div>';
    document.body.appendChild(dlg);
    ta = dlg.querySelector('textarea');
    count = dlg.querySelector('.bpn-count');
    ta.addEventListener('input', sync);
    dlg.querySelector('.bpn-cancel').addEventListener('click', function () { dlg.close(); });
    dlg.querySelector('.bpn-skip').addEventListener('click', function () { go(''); });
    dlg.querySelector('.bpn-ok').addEventListener('click', function () { go(ta.value); });
  }

  function sync() { count.textContent = ta.value.length + ' / ' + MAX; }

  function go(text) {
    var href = pending;
    text = (text || '').trim();
    dlg.close();
    if (text) href += (href.indexOf('?') < 0 ? '?' : '&') + 'notice=' + encodeURIComponent(text);
    window.location.href = href;      // response is an attachment, so the page stays put
  }

  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a.js-band-pdf');
    if (!a || e.defaultPrevented || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (typeof HTMLDialogElement === 'undefined') return;   // old browser: plain download
    e.preventDefault();
    if (!dlg) build();
    pending = a.getAttribute('href');
    ta.value = '';
    sync();
    dlg.showModal();
    ta.focus();
  });
})();
