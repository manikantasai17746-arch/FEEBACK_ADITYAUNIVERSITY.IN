/* Loading screen: shows the university logo while the browser navigates or submits a
   form. It never blocks anything that does not leave the page (new tabs, downloads, '#'
   links, forms that a script cancels), and it always hides itself again. */
(function () {
  var template = document.getElementById('ffs-loader-template');
  if (!template) return;
  var feedbackArrival = template.hasAttribute('data-feedback-arrival');
  var feedbackPending = false;
  var el = null, showTimer = null, hideTimer = null;

  function build() {
    if (el) return el;
    el = template.content.firstElementChild.cloneNode(true);
    // On arrival the loader starts before the portal theme script has run.
    if (feedbackArrival || document.body.classList.contains('landing-page')) {
      try { el.classList.toggle('is-dark', localStorage.getItem('ffs_dark') === '1'); } catch (e) {}
    }
    document.body.appendChild(el);
    return el;
  }
  function show(untilNavigation) {
    if (feedbackPending) return;
    feedbackPending = Boolean(untilNavigation);
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    showTimer = setTimeout(function () {
      build().classList.add('is-on');
      if (!feedbackPending) hideTimer = setTimeout(hide, 12000);
    }, feedbackPending ? 0 : 120);
  }
  function hide() {
    clearTimeout(showTimer); clearTimeout(hideTimer);
    feedbackPending = false;
    if (el) el.classList.remove('is-on');
  }

  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || a.hasAttribute('download') || a.hasAttribute('data-no-loader')) return;
    var t = a.getAttribute('target');
    if (t && t !== '_self') return;
    var href = a.getAttribute('href') || '';
    if (!href || href.charAt(0) === '#' || /^(javascript|mailto|tel):/i.test(href)) return;
    var url;
    try { url = new URL(a.href, location.href); } catch (err) { return; }
    if (url.origin !== location.origin) return;
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
    setTimeout(function () { if (!e.defaultPrevented) show(); }, 0);
  });

  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (!f || f.hasAttribute('data-no-loader')) return;
    var t = f.getAttribute('target');
    if (t && t !== '_self') return;
    setTimeout(function () {
      if (!e.defaultPrevented) show(f.hasAttribute('data-feedback-submit'));
    }, 0);
  });

  if (feedbackArrival) build().classList.add('is-on');
  window.addEventListener('pageshow', function (e) {
    if (e.persisted || !feedbackPending) hide();
  });
  window.addEventListener('pagehide', function () {
    if (!feedbackPending) hide();
  });
})();
