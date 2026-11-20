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

  function setupSelection(form, selector, master, update) {
    const boxes = [...form.elements].filter((element) => element.matches(selector));
    const refresh = () => {
      const active = boxes.filter((box) => !box.disabled);
      const count = active.filter((box) => box.checked).length;
      if (master) {
        master.disabled = active.length === 0;
        master.checked = count > 0 && count === active.length;
        master.indeterminate = count > 0 && count < active.length;
      }
      update(count, active.length);
    };
    master?.addEventListener('change', () => {
      boxes.forEach((box) => { if (!box.disabled) box.checked = master.checked; });
      refresh();
    });
    boxes.forEach((box) => box.addEventListener('change', refresh));
    refresh();
    return refresh;
  }

  const recent = document.getElementById('recent-publish');
  if (recent) {
    const bulk = document.getElementById('recent-bulk');
    setupSelection(recent, '.recent-pick', document.getElementById('recent-all'), (count) => {
      bulk.disabled = count === 0;
      bulk.textContent = `Publish selected (${count})`;
    });
  }

  const publishForm = document.getElementById('hrForm');
  if (publishForm) {
    const publish = document.getElementById('hrBulk');
    const selected = document.getElementById('hrCount');
    const allowOpen = document.getElementById('hrAllowOpen');
    const refresh = setupSelection(publishForm, '.hr-chk', document.getElementById('hrAll'), (count, total) => {
      publish.textContent = `Publish selected (${count})`;
      publish.disabled = count === 0;
      if (selected) selected.textContent = `${count} of ${total} selected`;
    });
    if (allowOpen) {
      const toggleOpen = () => {
        publishForm.querySelectorAll('[data-hr-open]').forEach((control) => {
          control.disabled = !allowOpen.checked;
          if (control.disabled && control.type === 'checkbox') control.checked = false;
        });
        refresh();
      };
      allowOpen.addEventListener('change', toggleOpen);
      toggleOpen();
    }
  }

  const bandForm = document.getElementById('band-mail-form');
  if (bandForm) {
    const send = document.getElementById('band-send');
    const selected = document.getElementById('band-selected');
    const subject = document.getElementById('band-subject');
    const message = document.getElementById('band-message');
    setupSelection(bandForm, '.band-cb', document.getElementById('band-all'), () => {
      const boxes = [...bandForm.querySelectorAll('.band-cb')];
      const total = new Set(boxes.map((box) => box.value)).size;
      const count = new Set(boxes.filter((box) => box.checked).map((box) => box.value)).size;
      send.disabled = count === 0;
      selected.textContent = `${count} of ${total} with email selected`;
    });
    document.getElementById('band-reset').addEventListener('click', () => {
      subject.value = subject.defaultValue;
      message.value = message.defaultValue;
    });
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
