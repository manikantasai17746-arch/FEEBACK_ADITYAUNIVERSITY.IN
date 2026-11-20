(() => {
  const toggle = document.getElementById('hod-nav-toggle');
  const nav = document.getElementById('hod-topnav');

  if (toggle && nav) {
    toggle.hidden = false;
    toggle.addEventListener('click', () => {
      const expanded = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', String(!expanded));
    });

    toggle.closest('header').addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true'
          && toggle.getClientRects().length) {
        toggle.setAttribute('aria-expanded', 'false');
        toggle.focus();
      }
    });
  }

  document.querySelectorAll('[data-hod-auto-submit]').forEach((control) => {
    control.addEventListener('change', () => control.form.requestSubmit());
  });
  document.querySelector('[data-hod-print]')?.addEventListener('click', () => window.print());
  document.querySelector('[data-hod-back]')?.addEventListener('click', (event) => {
    if (document.referrer && history.length > 1) {
      event.preventDefault();
      history.back();
    }
  });

  document.addEventListener('submit', (event) => {
    const message = event.submitter?.dataset.hodConfirm || event.target.dataset.hodConfirm;
    if (message && !window.confirm(message)) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);

  const selectActions = document.getElementById('ha-all');
  if (selectActions) {
    selectActions.addEventListener('change', () => {
      document.querySelectorAll('.ha-pick').forEach((check) => { check.checked = selectActions.checked; });
    });
  }

  const publishForm = document.getElementById('hrForm');
  if (publishForm) {
    const publish = document.getElementById('hrBulk');
    const update = () => {
      const count = publishForm.querySelectorAll('.hr-chk:checked').length;
      publish.textContent = 'Publish selected (' + count + ')';
      publish.disabled = count === 0;
    };
    publishForm.addEventListener('change', (event) => {
      if (event.target.matches('.hr-chk')) update();
    });
    update();
  }

  const logo = document.querySelector('[data-hod-logo]');
  if (!logo) return;

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const updateMotion = () => {
    logo.disabled = reducedMotion.matches;
    logo.classList.toggle('is-animating', !reducedMotion.matches);
  };
  reducedMotion.addEventListener('change', updateMotion);
  logo.addEventListener('click', () => {
    if (!reducedMotion.matches) {
      const artwork = logo.querySelector('svg');
      artwork.replaceWith(artwork.cloneNode(true));
    }
  });
  updateMotion();
})();
