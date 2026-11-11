/* Reusable multi-select dropdown: search box, "Select all", tick marks.
 * Same look and behaviour as the HOD Performance filters.
 * Usage:  var ms = MultiSelect.create(document.getElementById('x'), {allLabel:'All', noun:'items'});
 *         ms.setItems([{value:'1', label:'One'}]);  ms.values();  ms.onChange(fn);
 */
(function () {
  'use strict';
  var instances = [];

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function create(root, cfg) {
    cfg = cfg || {};
    var allLabel = cfg.allLabel || 'All';
    var noun = cfg.noun || 'items';
    var emptyText = cfg.emptyText || 'Nothing to select for these filters.';
    var items = [];
    var listeners = [];
    var timer = null;

    root.classList.add('ms');
    root.innerHTML = '';
    var summary = el('summary', 'ms-summary');
    var label = el('span', 'ms-label', allLabel);
    summary.appendChild(label);
    summary.appendChild(el('span', null, '▾'));
    summary.firstChild.nextSibling.setAttribute('aria-hidden', 'true');
    var panel = el('div', 'ms-panel');
    var search = el('input', 'ms-search');
    search.type = 'search'; search.placeholder = 'Search…'; search.autocomplete = 'off';
    var allRow = el('label', 'ms-row ms-all');
    var allCb = el('input', 'ms-all-cb'); allCb.type = 'checkbox';
    allRow.appendChild(allCb); allRow.appendChild(document.createTextNode(' Select all'));
    var list = el('div', 'ms-list');
    var empty = el('div', 'ms-row sub', emptyText);
    panel.appendChild(search); panel.appendChild(allRow); panel.appendChild(list);
    root.appendChild(summary); root.appendChild(panel);

    function cbs() { return Array.prototype.slice.call(list.querySelectorAll('.ms-cb')); }
    function shown() {
      return cbs().filter(function (c) { return c.closest('.ms-row').style.display !== 'none'; });
    }
    function values() { return cbs().filter(function (c) { return c.checked; }).map(function (c) { return c.value; }); }

    function refresh() {
      var all = cbs(), on = all.filter(function (c) { return c.checked; }), n = on.length;
      if (n === 0) label.textContent = allLabel;
      else if (n === all.length && n > 1) label.textContent = 'All ' + noun + ' (' + n + ')';
      else if (n === 1) label.textContent = on[0].getAttribute('data-label');
      else label.textContent = n + ' ' + noun + ' selected';
      var vis = shown();
      allCb.checked = vis.length > 0 && vis.every(function (c) { return c.checked; });
      allRow.style.display = all.length ? '' : 'none';
      search.style.display = all.length ? '' : 'none';
    }
    function fire() {
      clearTimeout(timer);
      timer = setTimeout(function () { listeners.forEach(function (fn) { fn(values()); }); }, 300);
    }

    function setItems(next, selected) {
      var keep = selected ? selected.map(String) : values();
      items = next || [];
      list.innerHTML = '';
      items.forEach(function (it) {
        var row = el('label', 'ms-row');
        row.setAttribute('data-text', String(it.label).toLowerCase());
        var cb = el('input', 'ms-cb'); cb.type = 'checkbox';
        cb.value = String(it.value); cb.setAttribute('data-label', it.label);
        cb.checked = keep.indexOf(String(it.value)) !== -1;
        row.appendChild(cb); row.appendChild(document.createTextNode(' ' + it.label));
        list.appendChild(row);
      });
      empty.textContent = emptyText;
      if (!items.length) list.appendChild(empty);
      search.value = '';
      refresh();
    }

    list.addEventListener('change', function () { refresh(); fire(); });
    allCb.addEventListener('change', function () {
      shown().forEach(function (c) { c.checked = allCb.checked; });
      refresh(); fire();
    });
    search.addEventListener('input', function () {
      var q = search.value.trim().toLowerCase(), any = false;
      cbs().forEach(function (c) {
        var row = c.closest('.ms-row');
        var ok = !q || (row.getAttribute('data-text') || '').indexOf(q) !== -1;
        row.style.display = ok ? '' : 'none';
        if (ok) any = true;
      });
      empty.textContent = q ? 'No matches.' : emptyText;
      if (!any && items.length) { if (!empty.parentNode) list.appendChild(empty); }
      else if (items.length && empty.parentNode) list.removeChild(empty);
      refresh();
    });
    search.addEventListener('keydown', function (e) { if (e.key === 'Enter') e.preventDefault(); });

    var api = {
      root: root,
      setItems: setItems,
      values: values,
      setSelected: function (arr) { setItems(items, arr || []); },
      setLabels: function (a, n) { allLabel = a; if (n) noun = n; refresh(); },
      setEmptyText: function (t) { emptyText = t; if (!items.length) empty.textContent = t; },
      onChange: function (fn) { listeners.push(fn); },
      setDisabled: function (d) { root.style.opacity = d ? '.6' : ''; root.style.pointerEvents = d ? 'none' : ''; },
      count: function () { return items.length; }
    };
    instances.push(root);
    refresh();
    return api;
  }

  document.addEventListener('click', function (e) {
    instances.forEach(function (b) { if (!b.contains(e.target)) b.removeAttribute('open'); });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') instances.forEach(function (b) { b.removeAttribute('open'); });
  });

  window.MultiSelect = { create: create };
})();
