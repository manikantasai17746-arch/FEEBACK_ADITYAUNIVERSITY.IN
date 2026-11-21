"""Shared employee photo system (teachers, HODs, deans and other employees).

* A photo is stored in the database (``employee_photos``) so it survives
  read-only / serverless hosting.  Its logical name follows the requested
  convention ``employee_photos/<emp_id>.<ext>``.
* Only JPG/PNG are accepted.  Every upload is decoded with Pillow, size and
  pixel limited, down-scaled and re-encoded, which also strips EXIF metadata and
  rejects files that merely *claim* to be images.
* Who may change a photo: an admin (anyone, singly or in bulk from the college
  Excel), an HOD/Dean for their own photo, or an HOD for employees that belong to
  the HOD's own departments.  Teachers can NOT choose their own photo: the college
  supplies it and the admin loads it.
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


def delete_photo(emp_code):
    """Remove the stored photo (the initials avatar / legacy file shows again)."""
    code = normalize_emp(emp_code)
    if not code:
        return False
    db.execute('DELETE FROM employee_photos WHERE emp_code=?', (code,))
    return True


# --------------------------------------------------------------------------- #
# College Excel (photo sheet) -> photos
# --------------------------------------------------------------------------- #
_CODE_HEADERS = {'empcode', 'employeecode', 'empid', 'employeeid'}
_NAME_HEADERS = {'empname', 'employeename', 'facultyname', 'name'}


def _norm_header(value):
    return re.sub(r'[^a-z]', '', str(value or '').lower())


def _cell_code(value):
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return normalize_emp(str(value))


def _incell_images(raw):
    """Pictures inserted with Excel's "Place in Cell" / IMAGE().

    openpyxl cannot see these (they live in ``xl/richData`` and the cell only
    carries a ``vm`` pointer), so without this such rows were reported as
    "no picture".  Returns ``{sheet title: {row number: image bytes}}``.
    Best effort: any structural surprise yields an empty result.
    """
    import posixpath
    import zipfile
    from xml.etree import ElementTree as ET

    def local(tag):
        return tag.rsplit('}', 1)[-1]

    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        names = set(z.namelist())
        if 'xl/richData/richValueRel.xml' not in names or 'xl/metadata.xml' not in names:
            return {}

        def parse(name):
            return ET.fromstring(z.read(name))

        # richValueRel index -> media path
        rels = {}
        relf = 'xl/richData/_rels/richValueRel.xml.rels'
        if relf in names:
            for r in parse(relf):
                rels[r.get('Id')] = posixpath.normpath(
                    posixpath.join('xl/richData', r.get('Target', '')))
        rel_ids = []
        for el in parse('xl/richData/richValueRel.xml'):
            rel_ids.append(next((v for k, v in el.attrib.items()
                                 if local(k) == 'id'), None))

        # which <v> of a rich value holds the image index
        img_pos = 0
        if 'xl/richData/rdrichvaluestructure.xml' in names:
            for s in parse('xl/richData/rdrichvaluestructure.xml'):
                keys = [k.get('n') for k in s if local(k.tag) == 'k']
                if any(k and k.endswith('LocalImageIdentifier') for k in keys):
                    img_pos = next(i for i, k in enumerate(keys)
                                   if k and k.endswith('LocalImageIdentifier'))
                    break
        rich = []                                   # rich value index -> rel index
        if 'xl/richData/rdrichvalue.xml' in names:
            for rv in parse('xl/richData/rdrichvalue.xml'):
                vals = [v.text for v in rv if local(v.tag) == 'v']
                rich.append(int(vals[img_pos]) if len(vals) > img_pos and vals[img_pos] is not None
                            and str(vals[img_pos]).isdigit() else None)

        # metadata: vm (1-based) -> futureMetadata block -> rich value index
        meta = parse('xl/metadata.xml')
        future = []
        for fm in meta:
            if local(fm.tag) == 'futureMetadata':
                for bk in fm:
                    i = next((e.get('i') for e in bk.iter() if local(e.tag) == 'rvb'), None)
                    future.append(int(i) if i is not None else None)
        vmeta = []
        for vm in meta:
            if local(vm.tag) == 'valueMetadata':
                for bk in vm:
                    rc = next((e for e in bk if local(e.tag) == 'rc'), None)
                    vmeta.append(int(rc.get('v')) if rc is not None else None)

        def vm_to_media(vm):
            if vm < 1 or vm > len(vmeta) or vmeta[vm - 1] is None:
                return None
            fi = vmeta[vm - 1]
            rv = future[fi] if fi < len(future) and future[fi] is not None else fi
            ri = rich[rv] if rv < len(rich) else None
            rid = rel_ids[ri] if ri is not None and ri < len(rel_ids) else None
            path = rels.get(rid)
            return z.read(path) if path in names else None

        # sheet title -> worksheet part
        wb_rels = {r.get('Id'): r.get('Target') for r in
                   parse('xl/_rels/workbook.xml.rels')}
        out = {}
        for sh in parse('xl/workbook.xml').iter():
            if local(sh.tag) != 'sheet':
                continue
            rid = next((v for k, v in sh.attrib.items() if local(k) == 'id'), None)
            target = (wb_rels.get(rid) or '').lstrip('/')
            part = target if target.startswith('xl/') else posixpath.join('xl', target)
            if part not in names:
                continue
            rows = {}
            for c in parse(part).iter():
                if local(c.tag) == 'c' and c.get('vm'):
                    m = re.match(r'[A-Z]+(\d+)$', c.get('r', ''))
                    if m and int(m.group(1)) not in rows:
                        data = vm_to_media(int(c.get('vm')))
                        if data:
                            rows[int(m.group(1))] = data
            if rows:
                out[sh.get('name')] = rows
        return out
    except Exception:
        return {}


def read_photo_workbook(raw):
    """Read the college sheet. Returns (entries, no_image, problems).

    ``entries``  - [{'emp_code','name','data','sheet','row'}] one per employee code
                   (the first photo found wins when a code repeats across sheets)
    ``no_image`` - [{'emp_code','name','sheet','row'}] listed rows with no picture
    ``problems`` - blocking messages (empty list when the file is usable)
    """
    if not raw or not raw.startswith(b'PK\x03\x04'):
        return [], [], ['The file is not a valid .xlsx workbook. In Excel use '
                        'File → Save As → Excel Workbook (.xlsx).']
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(raw))      # not read-only: images are loaded
    except Exception:
        return [], [], ['That workbook could not be read. Is it a damaged or '
                        'password-protected file?']
    entries, no_image, seen = [], [], set()
    found_header = False
    incell = _incell_images(raw)
    for ws in wb.worksheets:
        head = None
        for row in ws.iter_rows(min_row=1, max_row=40):
            cols = {_norm_header(c.value): c.column for c in row if c.value is not None}
            code_col = next((cols[h] for h in cols if h in _CODE_HEADERS), None)
            if code_col:
                name_col = next((cols[h] for h in cols if h in _NAME_HEADERS), None)
                head = (row[0].row, code_col, name_col)
                break
        if not head:
            continue
        found_header = True
        hrow, code_col, name_col = head
        listed = {}
        for r in range(hrow + 1, ws.max_row + 1):
            code = _cell_code(ws.cell(r, code_col).value)
            if code:
                name = ws.cell(r, name_col).value if name_col else None
                listed[r] = (code, str(name or '').strip())
        by_code = {}
        for img in getattr(ws, '_images', []):
            anchor = getattr(img, 'anchor', None)
            start = getattr(anchor, '_from', None)
            if start is None:
                continue
            first = start.row + 1
            end = getattr(getattr(anchor, 'to', None), 'row', None)
            last = max(first, (end + 1) if end is not None else first)
            row_no = next((x for x in range(first, last + 1) if x in listed), None)
            if row_no is None:
                continue
            try:
                data = img._data()
            except Exception:
                continue
            by_code.setdefault(listed[row_no][0], (row_no, data))
        for row_no, data in incell.get(ws.title, {}).items():
            if row_no in listed:
                by_code.setdefault(listed[row_no][0], (row_no, data))
        for row_no, (code, name) in listed.items():
            if code in seen:
                continue
            if code in by_code:
                seen.add(code)
                entries.append({'emp_code': code, 'name': name, 'sheet': ws.title,
                                'row': by_code[code][0], 'data': by_code[code][1]})
            else:
                no_image.append({'emp_code': code, 'name': name, 'sheet': ws.title,
                                 'row': row_no})
    # a code that gets a photo on another sheet is not "missing"
    no_image = [n for n in no_image if n['emp_code'] not in seen]
    if not found_header:
        return [], [], ['No "Emp.Code" column was found. Use the college photo sheet '
                        '(columns: S.No, Time Table, Emp.Code, Emp.Name, Photo).']
    if not entries and not no_image:
        return [], [], ['The sheet has no employee rows.']
    return entries, no_image, []


def import_photo_workbook(raw, actor_id, overwrite=True):
    """Apply the college sheet. Returns a result dict for the admin page."""
    entries, no_image, problems = read_photo_workbook(raw)
    result = {'problems': problems, 'saved': [], 'replaced': [], 'skipped': [],
              'unmatched': [], 'invalid': [], 'no_image': no_image, 'warnings': []}
    if no_image and not problems:
        try:
            import zipfile
            media = [n for n in zipfile.ZipFile(io.BytesIO(raw)).namelist()
                     if n.startswith('xl/media/')]
        except Exception:
            media = []
        if len(media) > len(entries):
            result['warnings'].append(
                f'The file contains {len(media)} pictures but only {len(entries)} could be '
                f'matched to an Emp.Code row. Pictures must sit on the row of their teacher '
                f'(Insert → Pictures → Place Over Cells, or Place in Cell). Pictures that '
                f'float freely on the sheet, or are grouped, cannot be matched.')
    for e in entries:
        emp = find_employee(e['emp_code'])
        if not emp:
            result['unmatched'].append(e)
            continue
        row = {'emp_code': emp['emp_code'], 'name': emp['name'], 'sheet_name': e['name']}
        had = bool(photo_meta(e['emp_code']))
        if had and not overwrite:
            result['skipped'].append(row)
            continue
        ok, msg = save_photo(e['emp_code'], e['data'], actor_id)
        if not ok:
            result['invalid'].append({**row, 'message': msg})
        elif had:
            result['replaced'].append(row)
        else:
            result['saved'].append(row)
    return result


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
        return role != 'teacher'              # teachers' photos come from the admin
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
    # ``no-cache`` = the browser may keep a copy but must ask the server (cheap
    # 304 via the ETag) before every reuse.  A fixed ``max-age`` made a browser
    # that had already shown the initials avatar keep showing it for minutes after
    # a photo was imported / replaced, even though the photo was saved.
    resp.headers['Cache-Control'] = 'private, no-cache'
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
    meta = photo_meta(code)
    if meta:
        # ETag from metadata so an unchanged photo is answered with a 304
        # without loading the image bytes from the database.
        etag = hashlib.md5(
            f"{meta['filename']}|{meta['size_bytes']}|{meta['updated_at']}".encode()
        ).hexdigest()
        if request.headers.get('If-None-Match', '').strip('"') == etag:
            return _cache_headers(Response(status=304), etag)
        photo = get_photo(code)
        if photo:
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
    if user['role'] == 'teacher':
        return redirect(url_for('photos.profile'))   # photo is set by the admin
    if not code:
        flash('Your account has no employee code, so a photo cannot be attached.', 'error')
        return redirect(url_for('auth.landing'))
    return redirect(url_for('photos.manage', emp_code=code))


def _department_of(user):
    """Best-effort department/branch label for the profile header."""
    if user['role'] == 'teacher':
        r = db.query(
            """SELECT b.name FROM branches b
               JOIN timetables t ON t.branch_id=b.id
               JOIN class_sessions cs ON cs.timetable_id=t.id
               WHERE cs.teacher_user_id=? AND cs.is_deleted=0
               ORDER BY cs.id LIMIT 1""", (user['id'],), one=True)
        return r['name'] if r else None
    return None


@bp.route('/profile')
@role_required('teacher', 'hod', 'dean')
def profile():
    """One page for the signed-in employee's photo and password."""
    user = current_user()
    code = normalize_emp(user.get('emp_code'))
    meta = photo_meta(code) if code else None
    return render_template(
        'profile.html', emp_code=code, department=_department_of(user),
        has_photo=bool(code and (meta or _legacy_static_path(code))),
        can_upload=bool(code and can_manage(user, code)),
        max_kb=MAX_UPLOAD_BYTES // 1024,
        forced_password_change=bool(user['role'] == 'teacher'
                                    and user.get('must_change_password')))


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
        if request.form.get('from') == 'profile':
            return redirect(url_for('photos.profile'))
    meta = photo_meta(code)
    return render_template(
        'photo_manage.html', emp=emp, emp_code=code, meta=meta,
        has_photo=bool(meta or _legacy_static_path(code)),
        max_kb=MAX_UPLOAD_BYTES // 1024, on_behalf=normalize_emp(user.get('emp_code')) != code,
        next_url=request.args.get('next', ''))


# --------------------------------------------------------------------------- #
# Admin: add / edit / remove employee photos (single and from the college Excel)
# --------------------------------------------------------------------------- #
def _admin_list_query(q, status):
    where, args = [], []
    if q:
        like = f'%{q.upper()}%'
        where.append('(UPPER(name) LIKE ? OR emp_code LIKE ?)')
        args += [like, like]
    if status == 'with':
        where.append('has_photo=1')
    elif status == 'without':
        where.append('has_photo=0')
    base = ("""SELECT UPPER(u.emp_code) AS emp_code, MIN(u.name) AS name, MIN(u.role) AS role,
                  MAX(CASE WHEN p.emp_code IS NULL THEN 0 ELSE 1 END) AS has_photo,
                  MAX(p.updated_at) AS updated_at
           FROM users u LEFT JOIN employee_photos p ON p.emp_code = UPPER(u.emp_code)
           WHERE u.role IN ('teacher','hod','dean') AND u.is_active=1
                 AND u.emp_code IS NOT NULL AND u.emp_code<>''
           GROUP BY UPPER(u.emp_code)""")
    clause = (' WHERE ' + ' AND '.join(where)) if where else ''
    return base, clause, tuple(args)


@bp.route('/admin/photos')
@role_required('admin')
def admin_list():
    q = (request.args.get('q') or '').strip()
    status = request.args.get('status') or ''
    if status not in ('with', 'without'):
        status = ''
    base, clause, args = _admin_list_query(q, status)
    total = db.query(f'SELECT COUNT(*) AS n FROM ({base}) x{clause}', args, one=True)['n']
    per_page = 25
    pages = max(1, -(-total // per_page))
    page = min(max(1, request.args.get('page', 1, type=int)), pages)
    rows = db.query(f'SELECT * FROM ({base}) x{clause} ORDER BY name LIMIT ? OFFSET ?',
                    args + (per_page, (page - 1) * per_page))
    counts = db.query(f'SELECT COUNT(*) AS n, COALESCE(SUM(has_photo),0) AS w FROM ({base}) x',
                      (), one=True)
    pg = {'page': page, 'pages': pages, 'per_page': per_page, 'total': total}
    return render_template('admin_photos.html', rows=rows, q=q, status=status, pg=pg,
                           total_all=counts['n'], with_photo=int(counts['w']))


@bp.route('/admin/photos/import', methods=['GET', 'POST'])
@role_required('admin')
def admin_import():
    user = current_user()
    if request.method == 'GET':
        return render_template('admin_photos_import.html', result=None, overwrite=True,
                               max_mb=current_app.config.get('MAX_CONTENT_LENGTH', 0)
                               // (1024 * 1024))
    upload = request.files.get('workbook')
    overwrite = request.form.get('overwrite') == '1'
    max_mb = current_app.config.get('MAX_CONTENT_LENGTH', 0) // (1024 * 1024)
    if not upload or not upload.filename:
        result = {'problems': ['Choose the college Excel (.xlsx) file first.']}
    elif not upload.filename.lower().endswith('.xlsx'):
        result = {'problems': ['Only .xlsx files are accepted. In Excel use '
                               'File → Save As → Excel Workbook (.xlsx).']}
    else:
        result = import_photo_workbook(upload.read(), user['id'], overwrite=overwrite)
        if not result['problems']:
            audit('photo.import', 'employee', 'bulk',
                  f"saved={len(result['saved'])} replaced={len(result['replaced'])} "
                  f"skipped={len(result['skipped'])} unmatched={len(result['unmatched'])} "
                  f"invalid={len(result['invalid'])}")
    status = 400 if result['problems'] else 200
    return render_template('admin_photos_import.html', result=result, overwrite=overwrite,
                           max_mb=max_mb), status


@bp.route('/admin/photos/<emp_code>/delete', methods=['POST'])
@role_required('admin')
def admin_delete(emp_code):
    code = normalize_emp(emp_code)
    if not code or not find_employee(code):
        abort(404)
    if photo_meta(code):
        delete_photo(code)
        audit('photo.delete', 'employee', code, f'by admin {current_user()["id"]}')
        flash('Photo removed.', 'success')
    nxt = request.form.get('next') or ''
    return redirect(nxt if nxt and is_safe_next(nxt) else url_for('photos.admin_list'))
