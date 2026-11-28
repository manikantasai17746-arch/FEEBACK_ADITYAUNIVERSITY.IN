"""Per-teacher feedback report (Main + Second + Lab in one PDF).

The HOD prints it and hands it over.  The teacher can also download it from a
public page with Emp ID + access code (faculty sign-in does not exist).

Only closed / archived forms are included.  Everything is aggregated from the
anonymous answers; no student identity is ever read.
"""
import base64
import hashlib
import hmac
import os
import re
from io import BytesIO
from itertools import combinations
from xml.sax.saxutils import escape

from flask import (Blueprint, Response, current_app, flash, render_template,
                   request)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from . import db, scoring, services
from .security import (audit, clear_login_failures, login_locked,
                       register_login_failure)

bp = Blueprint('teacher_report', __name__)

_CATEGORY = {'main': (0, 'Main Faculty Feedback'),
             'second': (1, 'Second Faculty Feedback'),
             'lab': (2, 'Lab Faculty Feedback')}
_ROMAN = ('', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X')
_LOGO = os.path.join(os.path.dirname(__file__), 'static', 'img', 'aditya-logo.png')


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def report_forms(teacher_id, branch_ids=None):
    """Closed / archived forms about this teacher.  ``branch_ids`` (an HOD's
    departments, or the branches an access code covers) limits them."""
    sql = f"""SELECT DISTINCT f.id, f.category, f.subject_code, f.subject_name,
                     f.snapshot_subject_code, f.title, f.phase, f.branch_id
                FROM feedback_forms f
                JOIN form_sessions fs ON fs.form_id=f.id
                JOIN class_sessions cs ON cs.id=fs.session_id
                {services.SFA_JOIN_SQL}
               WHERE {services.FACULTY_OF_FORM_SQL}=? AND f.is_deleted=0
                 AND f.status IN ('closed','archived')"""
    args = [teacher_id]
    if branch_ids is not None:
        ids = [int(b) for b in branch_ids]
        if not ids:
            return []
        sql += f" AND f.branch_id IN ({','.join('?' * len(ids))})"
        args += ids
    forms = [dict(r) for r in db.query(sql, tuple(args))]
    forms.sort(key=lambda f: (_CATEGORY.get(f['category'] or 'main', (9, ''))[0],
                              f['subject_name'] or '', f['id']))
    return forms


def teachers_with_reports(branch_ids):
    """[{id, name, emp_code, forms, branches}] for faculty with a closed or
    archived form in these departments (one query)."""
    ids = [int(b) for b in branch_ids]
    if not ids:
        return []
    rows = db.query(
        f"""SELECT DISTINCT u.id, u.name, u.emp_code, f.id AS form_id, f.branch_id
              FROM feedback_forms f
              JOIN form_sessions fs ON fs.form_id=f.id
              JOIN class_sessions cs ON cs.id=fs.session_id
              {services.SFA_JOIN_SQL}
              JOIN users u ON u.id={services.FACULTY_OF_FORM_SQL}
             WHERE u.role='teacher' AND f.is_deleted=0
               AND f.status IN ('closed','archived')
               AND f.branch_id IN ({','.join('?' * len(ids))})
             ORDER BY u.name, u.id""", tuple(ids))
    teachers = {}
    for r in rows:
        t = teachers.setdefault(r['id'], {
            'id': r['id'], 'name': r['name'], 'emp_code': r['emp_code'],
            'form_ids': set(), 'branches': set()})
        t['form_ids'].add(r['form_id'])
        t['branches'].add(r['branch_id'])
    for t in teachers.values():
        t['forms'] = len(t.pop('form_ids'))
    return list(teachers.values())


def _section_label(session_id, phase):
    row = db.query("""SELECT t.code, s.number FROM class_sessions cs
        JOIN timetables t ON t.id=cs.timetable_id
        JOIN semesters s ON s.id=t.semester_id WHERE cs.id=?""", (session_id,), one=True)
    number = row['number'] or 0
    semester = _ROMAN[number] if 0 < number < len(_ROMAN) else str(number)
    phase_text = {'phase1': 'Phase-I', 'phase2': 'Phase-II'}.get(phase)
    return '_'.join(p for p in (f'{semester} SEM', phase_text, row['code']) if p)


def _form_entry(form, section, session_ids):
    """One page: a form for one section."""
    scores = scoring.compute_form_scores(form['id'], min_responses=1,
                                         session_ids=session_ids)
    percent = {c['question_id']: c['score'] for c in scores['criteria']}
    tables, comments = {}, []
    for q in services.question_stats(form['id'], session_ids=session_ids):
        if q['qtype'] == 'text':
            comments += q['suggestions']
        elif q['options']:
            # Questions sharing a rating scale share one table.
            options = sorted(q['options'], key=lambda o: scoring.rating_rank(o['label']))
            columns = tuple(o['label'] for o in options)
            tables.setdefault(columns, []).append(
                {'text': q['text'], 'counts': [o['count'] for o in options],
                 'percent': percent.get(q['id'])})
    students = services.response_count(form['id'], session_ids=session_ids)
    marks = scoring.form_marks(form['id'], 0, session_ids=session_ids)
    return {
        'category': _CATEGORY.get(form['category'] or 'main', (0, 'Feedback'))[1],
        'subject': (form['subject_name'] or form['subject_code']
                    or form['snapshot_subject_code'] or form['title'] or ''),
        'section': section,
        'tables': list(tables.items()),
        'students': students,
        'average': marks['score_pct'] if students else None,
        'comments': comments,
    }


def report_entries(teacher_id, branch_ids=None):
    entries = []
    for form in report_forms(teacher_id, branch_ids):
        session_ids = services.teacher_form_session_ids(teacher_id, form['id'])
        for sec in services.sessions_by_section(session_ids):
            entries.append(_form_entry(
                form, _section_label(sec['session_ids'][0], form['phase']),
                sec['session_ids']))
    return entries


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def _style(name, bold=False, **kw):
    kw.setdefault('fontSize', 10.5)
    kw.setdefault('leading', kw['fontSize'] * 1.3)
    return ParagraphStyle(name, fontName='Times-Bold' if bold else 'Times-Roman', **kw)


def build_pdf(reports):
    """``reports``: [{'name', 'emp_code', 'entries', 'access_code'?}].  Each form
    and section starts on its own page."""
    cell, bold = _style('c'), _style('b', bold=True)
    mid, midb = _style('m', alignment=1), _style('mb', bold=True, alignment=1)
    head_style = _style('h', bold=True, alignment=1, fontSize=9.5)
    small = _style('s', fontSize=9, textColor=colors.HexColor('#444444'))
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=12 * mm, bottomMargin=14 * mm,
                            title='Faculty Feedback Report', author='Faculty Feedback System')
    width = doc.width
    grid = [('GRID', (0, 0), (-1, -1), 0.6, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]

    def p(text, style=cell):
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), style)

    def pct(value):
        return 'No responses' if value is None else f'{value:.2f}%'

    story = []
    for report in reports:
        for number, entry in enumerate(report['entries']):
            if story:
                story.append(PageBreak())
            story += [Image(_LOGO, width=62 * mm, height=62 * mm * _logo_ratio()),
                      Spacer(1, 3 * mm)]
            head = Table([
                [p(f"Faculty Name: {report['name']} ({report['emp_code']})", midb), ''],
                [p('Subject', bold), p(entry['subject'], bold)],
                [p('Branch-Sem-Section', bold), p(entry['section'], bold)],
                [p('Feedback', bold), p(entry['category'], bold)],
            ], colWidths=[width * 0.4, width * 0.6])
            head.setStyle(TableStyle(grid + [('SPAN', (0, 0), (-1, 0))]))
            story.append(head)
            for columns, rows in entry['tables']:
                first, last = width * 0.33, width * 0.15
                each = (width - first - last) / len(columns)
                data = [[''] + [p(c, head_style) for c in columns] + [p('Percentage', head_style)]]
                for row in rows:
                    data.append([p(row['text'])] + [p(n, mid) for n in row['counts']]
                                + [p(pct(row['percent']), mid)])
                table = Table(data, colWidths=[first] + [each] * len(columns) + [last],
                              repeatRows=1)
                table.setStyle(TableStyle(grid))
                story.append(table)
            total = Table([[p('No. of Students'), p(entry['students'], mid),
                            p('Average', mid), p(pct(entry['average']), mid)]],
                          colWidths=[width * 0.33, width * 0.12, width * 0.40, width * 0.15])
            total.setStyle(TableStyle(grid))
            story += [total, Spacer(1, 3 * mm), p('Suggestions:', bold)]
            if entry['comments']:
                story += [p(comment) for comment in entry['comments']]
            else:
                story.append(p('No written comments.'))
            if number == 0 and report.get('access_code'):
                story += [Spacer(1, 6 * mm), p(
                    f"To download your report online: {report['url']}\n"
                    f"Emp ID: {report['emp_code']}    Access code: {report['access_code']}\n"
                    'Keep this code private.', small)]
    if not story:
        story.append(p('No closed feedback forms to report.', bold))
    doc.build(story)
    return buffer.getvalue()


def _logo_ratio():
    from reportlab.lib.utils import ImageReader
    w, h = ImageReader(_LOGO).getSize()
    return h / w


def pdf_response(data, name):
    safe = re.sub(r'[^A-Za-z0-9_-]+', '_', name) or 'teacher'
    return Response(data, mimetype='application/pdf', headers={
        'Content-Disposition': f'attachment; filename=feedback_{safe}.pdf',
        'Cache-Control': 'no-store'})


# --------------------------------------------------------------------------- #
# Access codes
# --------------------------------------------------------------------------- #
# Derived from SECRET_KEY, so nothing is stored.  A code is bound to a teacher
# AND the departments it was issued for: an HOD's code never opens feedback from
# a department outside that HOD's scope.  Changing SECRET_KEY changes all codes.
def access_code(teacher_id, emp_code, branch_ids):
    message = 'teacher-report|v1|%d|%s|%s' % (
        teacher_id, (emp_code or '').upper(),
        ','.join(str(b) for b in sorted(set(branch_ids))))
    digest = hmac.new(str(current_app.config['SECRET_KEY']).encode(),
                      message.encode(), hashlib.sha256).digest()
    text = base64.b32encode(digest).decode()[:10]
    return f'{text[:5]}-{text[5:]}'


def _branches_for_code(teacher_id, emp_code, code):
    """The departments ``code`` was issued for, or None if it is wrong."""
    given = re.sub(r'[^A-Z0-9]', '', (code or '').upper())
    branches = sorted({f['branch_id'] for f in report_forms(teacher_id)
                       if f['branch_id'] is not None})[:10]
    for size in range(len(branches), 0, -1):
        for subset in combinations(branches, size):
            expected = access_code(teacher_id, emp_code, subset).replace('-', '')
            if hmac.compare_digest(given.encode(), expected.encode()):
                return list(subset)
    return None


# --------------------------------------------------------------------------- #
# Public download page (teachers only know their own code)
# --------------------------------------------------------------------------- #
@bp.route('/teacher-report', methods=['GET', 'POST'])
def download():
    if request.method == 'POST':
        emp = (request.form.get('emp_code') or '').strip().upper()[:40]
        code = (request.form.get('access_code') or '')[:40]
        config = current_app.config
        limits = ((f'report:{emp}:{request.remote_addr}', config['LOGIN_MAX_ATTEMPTS']),
                  (f'report-ip:{request.remote_addr}', config['LOGIN_MAX_ATTEMPTS'] * 4))
        wait = max(login_locked(key) for key, _ in limits)
        if wait:
            flash(f'Too many failed attempts. Please try again in {wait} seconds.', 'error')
            return _page(429)
        user = db.query("""SELECT id, name, emp_code FROM users
            WHERE role='teacher' AND is_active=1 AND UPPER(emp_code)=?""", (emp,), one=True)
        branches = _branches_for_code(user['id'], user['emp_code'], code) if user else None
        if branches:
            clear_login_failures(limits[0][0])
            data = build_pdf([{'name': user['name'], 'emp_code': user['emp_code'],
                               'entries': report_entries(user['id'], branches)}])
            audit('teacher_report.download', 'users', user['id'],
                  'Report downloaded with access code')
            return pdf_response(data, user['emp_code'])
        for key, maximum in limits:
            register_login_failure(key, maximum, config['LOGIN_LOCKOUT_SECONDS'])
        flash('Emp ID or access code is not correct. Ask your HOD for the code '
              'printed on your report.', 'error')
        return _page(403)
    return _page(200)


def _page(status):
    response = current_app.make_response(
        (render_template('teacher_report.html'), status))
    response.headers['Cache-Control'] = 'no-store'
    return response
