/* Cross-fades the campus photos behind the "numbers" banner every few seconds. */
(function () {
  var box = document.querySelector('.numbers-slides');
  if (!box) return;
  var slides = box.querySelectorAll('img');
  if (slides.length < 2) return;
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  var interval = parseInt(box.getAttribute('data-interval'), 10) || 3000;
  var current = 0, timer = null;

  function next() {
    slides[current].classList.remove('is-active');
    current = (current + 1) % slides.length;
    slides[current].classList.add('is-active');
  }
  function start() { if (!timer) timer = setInterval(next, interval); }
  function stop() { clearInterval(timer); timer = null; }

  // Pause when the tab is hidden or the banner is off-screen.
  document.addEventListener('visibilitychange', function () { document.hidden ? stop() : start(); });
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (e) { e[0].isIntersecting ? start() : stop(); }).observe(box.parentNode);
  } else { start(); }
})();
