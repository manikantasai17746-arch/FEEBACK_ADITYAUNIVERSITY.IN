// Switch the arrow cursor to gold when the pointer is over a dark background.
// Works site-wide: it checks the background behind whatever is under the pointer.
(function () {
  var DARK_SECTIONS = '.lp-footer, .numbers';
  var lastDark = null, queued = false, lastX = 0, lastY = 0;

  function luminance(rgb) {
    var c = rgb.map(function (v) { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }

  function hasOwnText(el) {
    for (var n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3 && n.textContent.trim()) return true;
    }
    return false;
  }

  function isDarkAt(el) {
    if (!el) return false;
    if (el.closest && el.closest(DARK_SECTIONS)) return true;
    // Dark text (such as the navy headings) is the same colour as the navy arrow, so the arrow
    // blends in. Switch to gold over dark text too.
    if (hasOwnText(el)) {
      var t = getComputedStyle(el).color.match(/[\d.]+/g);
      if (t && (t.length < 4 || parseFloat(t[3]) > 0) && luminance([+t[0], +t[1], +t[2]]) < 0.2) return true;
    }
    for (var node = el; node && node.nodeType === 1; node = node.parentElement) {
      var m = getComputedStyle(node).backgroundColor.match(/[\d.]+/g);
      if (!m) continue;
      var alpha = m.length > 3 ? parseFloat(m[3]) : 1;
      if (alpha === 0) continue;
      return luminance([+m[0], +m[1], +m[2]]) < 0.25;
    }
    return false;
  }

  function update() {
    queued = false;
    var el = document.elementFromPoint(lastX, lastY);
    var dark = isDarkAt(el);
    if (dark !== lastDark) {
      lastDark = dark;
      document.documentElement.classList.toggle('cursor-gold', dark);
    }
  }

  document.addEventListener('mousemove', function (e) {
    lastX = e.clientX; lastY = e.clientY;
    if (!queued) { queued = true; requestAnimationFrame(update); }
  }, { passive: true });
  document.addEventListener('scroll', function () {
    if (!queued) { queued = true; requestAnimationFrame(update); }
  }, { passive: true });
})();