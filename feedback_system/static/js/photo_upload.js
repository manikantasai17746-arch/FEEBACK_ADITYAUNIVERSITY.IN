/* Live preview + client-side checks for the profile photo upload.
   The server re-validates everything; this only gives instant feedback. */
(function () {
  var form = document.getElementById('photoForm');
  if (!form) return;
  var input = document.getElementById('photoInput');
  var box = document.getElementById('photoPreviewBox');
  var img = document.getElementById('photoPreview');
  var err = document.getElementById('photoError');
  var btn = document.getElementById('photoSubmit');
  var maxBytes = parseInt(form.getAttribute('data-max-kb') || '2048', 10) * 1024;
  var current = null;

  function fail(msg) {
    err.textContent = msg; err.hidden = false;
    box.hidden = true; btn.disabled = true;
    if (current) { URL.revokeObjectURL(current); current = null; }
  }

  input.addEventListener('change', function () {
    err.hidden = true;
    var f = input.files && input.files[0];
    if (!f) { fail('Please choose a photo.'); return; }
    if (['image/jpeg', 'image/png'].indexOf(f.type) === -1) {
      fail('Only JPG and PNG photos are allowed.'); input.value = ''; return;
    }
    if (f.size > maxBytes) {
      fail('That photo is too large (maximum ' + Math.round(maxBytes / 1024) + ' KB).');
      input.value = ''; return;
    }
    if (current) URL.revokeObjectURL(current);
    current = URL.createObjectURL(f);
    img.src = current; box.hidden = false; btn.disabled = false;
  });
})();
