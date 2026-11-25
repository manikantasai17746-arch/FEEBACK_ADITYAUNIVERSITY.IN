const inbox = document.querySelector('.inbox');
if (inbox) {
  inbox.addEventListener('click', (event) => {
    const toggle = event.target.closest('.inbox-toggle');
    if (toggle) {
      toggle.setAttribute('aria-expanded', String(toggle.getAttribute('aria-expanded') !== 'true'));
    }
  });
  inbox.classList.add('inbox-ready');
}
