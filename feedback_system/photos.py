"""Shared employee photo system (teachers, HODs, deans and other employees).

* A photo is stored in the database (``employee_photos``) so it survives
  read-only / serverless hosting.  Its logical name follows the requested
  convention ``employee_photos/<emp_id>.<ext>``.
* Only JPG/PNG are accepted.  Every upload is decoded with Pillow, size and
  pixel limited, down-scaled and re-encoded, which also strips EXIF metadata and
  rejects files that merely *claim* to be images.
* Who may change a photo: the employee themself, an admin (anyone), or an HOD
  for employees that belong to the HOD's own departments.
* ``/employee-photo/<emp>`` never 404s for a valid code: it serves the stored
  photo, then any legacy ``static/img/teachers/<emp>.jpg``, then a generated
  initials avatar, so no JavaScript fallback is needed (works under a strict CSP).
"""
import hashlib
import io
import os
import re
from xml.sax.saxutils import escape

from flask import (Blueprint, Response, abort, current_app, flash, redirect,
                   render_template, request, url_for)

from . import db
from .security import (audit, current_user, is_safe_next, role_required,
                       scoped_branch_ids_or_none)

bp = Blueprint('photos', __name__)

ALLOWED_FORMATS = {'JPEG': ('image/jpeg', 'jpg'), 'PNG': ('image/png', 'png')}
MAX_UPLOAD_BYTES = int(os.environ.get('PHOTO_MAX_KB', '2048')) * 1024
MAX_PIXELS = 25_000_000          # decompression-bomb guard
MAX_SIDE = 512                   # stored photos are scaled down to this
MIN_SIDE = 64
EMP_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')
EMPLOYEE_ROLES = ('teacher', 'hod', 'dean')


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def normalize_emp(emp_code):
    """Return the canonical (upper-case) employee code, or None if invalid."""
    code = str(emp_code or '').strip()
    return code.upper() if EMP_RE.match(code) else None


def initials(name):
    parts = [p for p in re.split(r'[\s.]+', (name or '').strip()) if p]
    parts = [p for p in parts if p.upper() not in ('DR', 'MR', 'MRS', 'MS', 'PROF')] or parts
    if not parts:
        return '?'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def find_employee(emp_code):
    code = normalize_emp(emp_code)
    if not code:
        return None
    rows = db.query(
        "SELECT id, role, name, emp_code, designation FROM users "
        "WHERE UPPER(emp_code)=? AND role IN ('teacher','hod','dean') "
        "AND is_active=1 ORDER BY id", (code,))
    if not rows:
        return None
    # Prefer the role that carries a designation (HOD/Dean) for display.
    best = sorted(rows, key=lambda r: (r['role'] == 'teacher', r['id']))[0]
    return dict(best)


def get_photo(emp_code):
    """Return {'data': bytes, 'mime', 'filename', 'updated_at'} or None."""
    code = normalize_emp(emp_code)
    if not code:
        return None
    row = db.query(
        'SELECT data, mime, filename, updated_at FROM employee_photos WHERE emp_code=?',
        (code,), one=True)
    if not row:
        return None
    return {'data': bytes(row['data']), 'mime': row['mime'],
            'filename': row['filename'], 'updated_at': row['updated_at']}


def photo_meta(emp_code):
    """Cheap existence check (does not load the image bytes)."""
    code = normalize_emp(emp_code)
    if not code:
        return None
    row = db.query(
        'SELECT filename, size_bytes, updated_at FROM employee_photos WHERE emp_code=?',
        (code,), one=True)
    return dict(row) if row else None


def _legacy_static_path(code):
    root = current_app.static_folder
    for cand in (code, code.lower()):
        path = os.path.join(root, 'img', 'teachers', cand + '.jpg')
        if os.path.isfile(path):
            return path
    return None


def has_any_photo(emp_code):
    code = normalize_emp(emp_code)
    return bool(code and (photo_meta(code) or _legacy_static_path(code)))


def validate_and_process(raw):
    """Validate an uploaded image and return (ok, message, data, mime, ext)."""
    if not raw:
        return False, 'Please choose a photo to upload.', None, None, None
    if len(raw) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES / (1024 * 1024)
        return False, f'The photo is too large. Maximum size is {mb:g} MB.', None, None, None
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - Pillow is a declared dependency
        return False, 'Photo uploads are not available on this server.', None, None, None
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        probe = Image.open(io.BytesIO(raw))
        fmt = probe.format
        probe.verify()                       # structural check
        img = Image.open(io.BytesIO(raw))    # verify() invalidates the handle
        img.load()
    except Exception:
        return False, 'That file is not a valid JPG or PNG image.', None, None, None
    if fmt not in ALLOWED_FORMATS:
        return False, 'Only JPG and PNG photos are allowed.', None, None, None
    if min(img.size) < MIN_SIDE:
        return False, f'The photo is too small (at least {MIN_SIDE}×{MIN_SIDE} pixels).', \
            None, None, None
    img = ImageOps.exif_transpose(img)
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    mime, ext = ALLOWED_FORMATS[fmt]
    out = io.BytesIO()
    if fmt == 'JPEG':
        img.convert('RGB').save(out, 'JPEG', quality=88, optimize=True)
    else:
        img.save(out, 'PNG', optimize=True)
    return True, '', out.getvalue(), mime, ext


def save_photo(emp_code, raw, actor_id):
    """Validate + store (insert or replace). Returns (ok, message)."""
    code = normalize_emp(emp_code)
    if not code:
        return False, 'Invalid employee code.'
    ok, msg, data, mime, ext = validate_and_process(raw)
    if not ok:
        return False, msg
    filename = f'employee_photos/{code}.{ext}'
    existing = db.query('SELECT 1 FROM employee_photos WHERE emp_code=?', (code,), one=True)
    if existing:
        db.execute(
            "UPDATE employee_photos SET filename=?, mime=?, data=?, size_bytes=?, "
            "uploaded_by=?, updated_at=CURRENT_TIMESTAMP WHERE emp_code=?",
            (filename, mime, data, len(data), actor_id, code))
    else:
        db.execute(
            "INSERT INTO employee_photos(emp_code, filename, mime, data, size_bytes, uploaded_by) "
            "VALUES(?,?,?,?,?,?)", (code, filename, mime, data, len(data), actor_id))
    return True, 'Photo saved.'


def can_manage(actor, emp_code):
    """May ``actor`` upload/replace the photo of ``emp_code``?"""
    if not actor:
        return False
    code = normalize_emp(emp_code)
    if not code:
        return False
    role = actor['role']
    if role == 'admin':
        return True
    if role not in EMPLOYEE_ROLES:
        return False                          # students can never upload
    own = normalize_emp(actor.get('emp_code'))
    if own and own == code:
        return True
    if role != 'hod':
        return False
    # HOD on behalf of someone in the HOD's own departments.
    branch_ids = scoped_branch_ids_or_none(actor) or []
    if not branch_ids:
        return False
    ph = ','.join('?' * len(branch_ids))
    row = db.query(
        f"""SELECT 1 FROM class_sessions cs
            JOIN users u ON u.id = cs.teacher_user_id
            JOIN timetables t ON t.id = cs.timetable_id
            WHERE UPPER(u.emp_code)=? AND t.branch_id IN ({ph}) LIMIT 1""",
        (code, *branch_ids), one=True)
    return bool(row)


def _avatar_svg(label):
    # Deterministic, colour-blind friendly palette picked from the label.
    palette = ['#1f3a8a', '#0f766e', '#7c2d12', '#6d28d9', '#065f46', '#9a3412']
    colour = palette[int(hashlib.md5(label.encode()).hexdigest(), 16) % len(palette)]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">'
        f'<rect width="128" height="128" fill="{colour}"/>'
        '<text x="64" y="64" dy=".35em" text-anchor="middle" fill="#ffffff" '
        'font-family="Arial,Helvetica,sans-serif" font-size="52" font-weight="bold">'
        f'{escape(label)}</text></svg>')


def _cache_headers(resp, etag):
    resp.headers['Cache-Control'] = 'private, max-age=300, must-revalidate'
    resp.headers['ETag'] = f'"{etag}"'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@bp.route('/employee-photo/<emp_code>')
@role_required('teacher', 'hod', 'dean', 'admin', 'student')
def serve(emp_code):
    code = normalize_emp(emp_code)
    if not code:
        abort(404)
    photo = get_photo(code)
    if photo:
        etag = hashlib.md5(photo['data']).hexdigest()
        if request.headers.get('If-None-Match', '').strip('"') == etag:
            return _cache_headers(Response(status=304), etag)
        return _cache_headers(Response(photo['data'], mimetype=photo['mime']), etag)
    legacy = _legacy_static_path(code)
    if legacy:
        with open(legacy, 'rb') as fh:
            data = fh.read()
        etag = hashlib.md5(data).hexdigest()
        return _cache_headers(Response(data, mimetype='image/jpeg'), etag)
    emp = find_employee(code)
    label = initials(emp['name']) if emp else '?'
    svg = _avatar_svg(label)
    return _cache_headers(Response(svg, mimetype='image/svg+xml'),
                          hashlib.md5(svg.encode()).hexdigest())


@bp.route('/photos/me')
@role_required('teacher', 'hod', 'dean')
def mine():
    user = current_user()
    code = normalize_emp(user.get('emp_code'))
    if not code:
        flash('Your account has no employee code, so a photo cannot be attached.', 'error')
        return redirect(url_for('auth.landing'))
    return redirect(url_for('photos.manage', emp_code=code))


@bp.route('/photos/manage/<emp_code>', methods=['GET', 'POST'])
@role_required('teacher', 'hod', 'dean', 'admin')
def manage(emp_code):
    user = current_user()
    code = normalize_emp(emp_code)
    if not code or not can_manage(user, code):
        abort(403)
    emp = find_employee(code)
    if not emp:
        abort(404)
    if request.method == 'POST':
        upload = request.files.get('photo')
        raw = upload.read(MAX_UPLOAD_BYTES + 1) if upload and upload.filename else b''
        ok, msg = save_photo(code, raw, user['id'])
        if ok:
            audit('photo.upload', 'employee', code, f'by user {user["id"]}')
            flash('Photo saved.', 'success')
            nxt = request.form.get('next') or ''
            if nxt and is_safe_next(nxt):
                return redirect(nxt)
            return redirect(url_for('photos.manage', emp_code=code))
        flash(msg, 'error')
    meta = photo_meta(code)
    return render_template(
        'photo_manage.html', emp=emp, emp_code=code, meta=meta,
        has_photo=bool(meta or _legacy_static_path(code)),
        max_kb=MAX_UPLOAD_BYTES // 1024, on_behalf=normalize_emp(user.get('emp_code')) != code,
        next_url=request.args.get('next', ''))
