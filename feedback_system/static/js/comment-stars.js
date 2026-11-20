/* Comment star rating: the stars appear (and are required) only once the student has
   typed a comment. The server enforces the same rule, so this is a convenience only. */
(function () {
  Array.prototype.forEach.call(document.querySelectorAll('.comment-stars[data-stars-for]'), function (box) {
    var ta = document.querySelector('textarea[name="' + box.getAttribute('data-stars-for') + '"]');
    if (!ta) return;
    var radios = box.querySelectorAll('input[type=radio]');
    function sync() {
      var has = ta.value.trim().length > 0;
      box.hidden = !has;
      Array.prototype.forEach.call(radios, function (r) { r.required = has; });
    }
    ta.addEventListener('input', sync);
    sync();
  });
})();
