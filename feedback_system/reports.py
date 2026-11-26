"""PDF report generation for the admin dashboard.

Uses ReportLab (added to ``requirements.txt`` because the project had no PDF
library yet).  Two reports are produced from **real** SQLite records:

* students who *have submitted* a feedback form, and
* eligible students who *have not submitted* it.

Only completion data is ever rendered.  Anonymous response content and student
identity are never joined, so these reports cannot reveal who gave which answer.
"""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

NAVY = colors.HexColor('#1f3a68')
NAVY_SOFT = colors.HexColor('#eef2f9')
LINE = colors.HexColor('#dfe5ef')
INK = colors.HexColor('#1f2733')
MUTED = colors.HexColor('#66738a')
OK = colors.HexColor('#1b7f3b')
WARN = colors.HexColor('#b7791f')


def _styles():
    ss = getSampleStyleSheet()
    return {
        'title': ParagraphStyle('RTitle', parent=ss['Title'], fontName='Helvetica-Bold',
                                fontSize=17, leading=21, textColor=NAVY, spaceAfter=2,
                                alignment=0),
        'subtitle': ParagraphStyle('RSub', parent=ss['Normal'], fontName='Helvetica',
                                   fontSize=10, leading=13, textColor=MUTED, spaceAfter=8),
        'meta': ParagraphStyle('RMeta', parent=ss['Normal'], fontName='Helvetica',
                               fontSize=8.5, leading=11.5, textColor=INK),
        'cell': ParagraphStyle('RCell', parent=ss['Normal'], fontName='Helvetica',
                               fontSize=7.6, leading=9.4, textColor=INK),
        'cellb': ParagraphStyle('RCellB', parent=ss['Normal'], fontName='Helvetica-Bold',
                                fontSize=7.6, leading=9.4, textColor=INK),
        'head': ParagraphStyle('RHead', parent=ss['Normal'], fontName='Helvetica-Bold',
                               fontSize=7.8, leading=9.6, textColor=colors.white),
        'empty': ParagraphStyle('REmpty', parent=ss['Normal'], fontName='Helvetica-Oblique',
                                fontSize=10, leading=14, textColor=MUTED, spaceBefore=10),
    }


def _numbered_canvas(footer_text):
    """Return a canvas class that stamps the footer and 'Page X of Y'."""
    class NumberedCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_states = []

        def showPage(self):
            self._saved_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_states) or 1
            for state in self._saved_states:
                self.__dict__.update(state)
                self._draw_footer(total)
                super().showPage()
            pdfcanvas.Canvas.save(self)

        def _draw_footer(self, total):
            self.setFont('Helvetica', 7.5)
            self.setFillColor(MUTED)
            self.drawString(12 * mm, 8 * mm, footer_text)
            self.drawRightString(self._pagesize[0] - 12 * mm, 8 * mm,
                                 f'Page {self._pageNumber} of {total}')
    return NumberedCanvas


def _cell(text, style):
    return Paragraph('' if text is None else str(text), style)


def build_student_report(rows, meta_lines, title, subtitle=None, generated_at='',
                         footer=''):
    """Render a professional, multi-page student report and return PDF bytes."""
    styles = _styles()
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=15 * mm,
        title=title, author='Faculty Feedback System')
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='main',
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='page', frames=[frame])])

    story = [Paragraph(title, styles['title'])]
    if subtitle:
        story.append(Paragraph(subtitle, styles['subtitle']))

    meta_html = ' &nbsp;&nbsp;|&nbsp;&nbsp; '.join(f'<b>{k}:</b> {v}' for k, v in meta_lines)
    if generated_at:
        meta_html += f' &nbsp;&nbsp;|&nbsp;&nbsp; <b>Generated:</b> {generated_at}'
    meta_html += f' &nbsp;&nbsp;|&nbsp;&nbsp; <b>Total students:</b> {len(rows)}'
    story.append(Paragraph(meta_html, styles['meta']))
    story.append(Spacer(1, 6))

    if not rows:
        story.append(Paragraph(
            'No students match the selected filters for this report. '
            'Adjust the academic configuration or feedback form and try again.',
            styles['empty']))
        doc.build(story, canvasmaker=_numbered_canvas(
            footer or 'Faculty Feedback System — administrator report'))
        return buffer.getvalue()

    is_submitted = rows[0]['status'] == 'Submitted'
    header = ['#', 'Roll Number', 'Student Name', 'Academic Year', 'Sem', 'Branch',
              'Timetable / Section', 'Subject', 'Feedback Form', 'Status']
    if is_submitted:
        header.append('Submitted On')

    data = [[_cell(h, styles['head']) for h in header]]
    for i, r in enumerate(rows, 1):
        subject = f"{r['subject_name'] or ''} ({r['subject_code'] or '—'})".strip()
        section = r['section'] or r['timetable'] or '—'
        row = [
            _cell(i, styles['cell']),
            _cell(r['roll'], styles['cellb']),
            _cell(r['name'] or '—', styles['cell']),
            _cell(r['academic_year'] or '—', styles['cell']),
            _cell(r['semester'] or '—', styles['cell']),
            _cell(r['branch'] or '—', styles['cell']),
            _cell(section, styles['cell']),
            _cell(subject or '—', styles['cell']),
            _cell(r['form_title'] or f"Form #{r['form_id']}", styles['cell']),
            _cell(r['status'], styles['cellb']),
        ]
        if is_submitted:
            row.append(_cell(r['submitted_at'] or '—', styles['cell']))
        data.append(row)

    weights = [8, 26, 44, 22, 12, 20, 40, 46, 44, 22]
    if is_submitted:
        weights.append(30)
    total = sum(weights)
    col_widths = [doc.width * (w / total) for w in weights]

    table = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.4, LINE),
        ('LINEBELOW', (0, 0), (-1, 0), 0.7, NAVY),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, NAVY_SOFT]),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 3.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
    ]
    status_col = header.index('Status')
    status_colour = OK if is_submitted else WARN
    for idx in range(1, len(data)):
        style.append(('TEXTCOLOR', (status_col, idx), (status_col, idx), status_colour))
    table.setStyle(TableStyle(style))
    story.append(table)

    doc.build(story, canvasmaker=_numbered_canvas(
        footer or 'Faculty Feedback System — administrator report'))
    return buffer.getvalue()


def build_pending_rolls_pdf(teacher_name, emp_code, form_title, subjects, sections,
                            deadline_text, rolls, generated_at='', hod_name=''):
    """One teacher's pending list as a PDF: roll numbers ONLY (no names).

    Portrait A4, roll numbers laid out in a grid so the page stays short.
    """
    styles = _styles()
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title='Pending feedback submissions', author='Faculty Feedback System')
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='main',
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='page', frames=[frame])])

    from html import escape
    story = [Paragraph('Pending feedback submissions', styles['title']),
             Paragraph(escape(form_title or 'Feedback form'), styles['subtitle'])]
    teacher = escape(teacher_name or 'Faculty') + (f' ({escape(str(emp_code))})' if emp_code else '')
    meta = [('Faculty', teacher),
            ('Subject', escape(', '.join(subjects)) if subjects else '—'),
            ('Section', escape(', '.join(sections)) if sections else '—'),
            ('Deadline', escape(deadline_text or 'No deadline')),
            ('Students pending', str(len(rolls)))]
    if hod_name:
        meta.append(('Sent by', escape(hod_name)))
    if generated_at:
        meta.append(('Generated', escape(generated_at)))
    for k, v in meta:
        story.append(Paragraph(f'<b>{k}:</b> {v}', styles['meta']))
    story.append(Spacer(1, 10))

    if not rolls:
        story.append(Paragraph('No pending students - everyone has submitted.', styles['empty']))
    else:
        story.append(Paragraph('Roll numbers who have not yet submitted:', styles['meta']))
        story.append(Spacer(1, 4))
        cols = 5
        cell = ParagraphStyle('RollCell', parent=styles['cell'], fontName='Helvetica-Bold',
                              fontSize=10, leading=13, alignment=1)
        grid = [[Paragraph(escape(str(r)), cell) for r in rolls[i:i + cols]]
                + [''] * (cols - len(rolls[i:i + cols]))
                for i in range(0, len(rolls), cols)]
        table = Table(grid, colWidths=[doc.width / cols] * cols)
        table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.4, LINE),
            ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, NAVY_SOFT]),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(table)
    doc.build(story, canvasmaker=_numbered_canvas(
        'Faculty Feedback System - roll numbers only; no answers or names are shown'))
    return buffer.getvalue()


def build_pending_rolls_pdf_combined(teacher_name, emp_code, blocks, generated_at='',
                                     hod_name=''):
    """One teacher's pending lists across SEVERAL feedback forms in ONE PDF.

    Used so a teacher who is Main, Lab and/or Second faculty gets a single
    e-mail instead of one per form.  ``blocks`` is a list of dicts with the keys
    ``form_title``, ``category_label``, ``subjects``, ``sections``,
    ``deadline_text`` and ``rolls``.  Each form keeps its own section (the same
    roll number can be pending in more than one form).  Roll numbers only - no
    names and no answers.
    """
    from html import escape
    styles = _styles()
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title='Pending feedback submissions', author='Faculty Feedback System')
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='main',
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='page', frames=[frame])])

    section_head = ParagraphStyle('RSection', parent=styles['meta'], fontName='Helvetica-Bold',
                                  fontSize=11.5, leading=15, textColor=NAVY,
                                  spaceBefore=12, spaceAfter=3)
    teacher = escape(teacher_name or 'Faculty') + (f' ({escape(str(emp_code))})' if emp_code else '')
    story = [Paragraph('Pending feedback submissions', styles['title']),
             Paragraph(f'Combined list for {len(blocks)} feedback form(s)', styles['subtitle'])]
    meta = [('Faculty', teacher),
            ('Forms with pending students', str(len(blocks)))]
    if hod_name:
        meta.append(('Sent by', escape(hod_name)))
    if generated_at:
        meta.append(('Generated', escape(generated_at)))
    for k, v in meta:
        story.append(Paragraph(f'<b>{k}:</b> {v}', styles['meta']))

    for i, b in enumerate(blocks, 1):
        rolls = b.get('rolls') or []
        story.append(CondPageBreak(55 * mm))
        story.append(Paragraph(
            f"{i}. {escape(b.get('category_label') or 'Feedback form')} - "
            f"{escape(b.get('form_title') or 'Feedback form')}", section_head))
        for k, v in (('Subject', escape(', '.join(b.get('subjects') or [])) or '—'),
                     ('Section', escape(', '.join(b.get('sections') or [])) or '—'),
                     ('Deadline', escape(b.get('deadline_text') or 'No deadline')),
                     ('Students pending', str(len(rolls)))):
            story.append(Paragraph(f'<b>{k}:</b> {v}', styles['meta']))
        story.append(Spacer(1, 5))
        if not rolls:
            story.append(Paragraph('No pending students - everyone has submitted.',
                                   styles['empty']))
            continue
        story.append(Paragraph('Roll numbers who have not yet submitted:', styles['meta']))
        story.append(Spacer(1, 4))
        cols = 5
        cell = ParagraphStyle('RollCellC', parent=styles['cell'], fontName='Helvetica-Bold',
                              fontSize=10, leading=13, alignment=1)
        grid = [[Paragraph(escape(str(r)), cell) for r in rolls[j:j + cols]]
                + [''] * (cols - len(rolls[j:j + cols]))
                for j in range(0, len(rolls), cols)]
        table = Table(grid, colWidths=[doc.width / cols] * cols)
        table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.4, LINE),
            ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, NAVY_SOFT]),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(table)
    doc.build(story, canvasmaker=_numbered_canvas(
        'Faculty Feedback System - roll numbers only; no answers or names are shown'))
    return buffer.getvalue()


def build_comparison_pdf(profiles, title, subtitle=None, generated_at='', footer=''):
    """Subject-wise multi-round comparison PDF (one table per teacher subject).

    ``profiles`` is the list produced by ``analytics.build_teacher_profiles``.
    Each subject keeps its own rounds and average; subjects are never combined.
    """
    from xml.sax.saxutils import escape

    styles = _styles()
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=15 * mm,
        title=title, author='Faculty Feedback System')
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='main',
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='page', frames=[frame])])

    story = [Paragraph(escape(title), styles['title'])]
    if subtitle:
        story.append(Paragraph(escape(subtitle), styles['subtitle']))
    if generated_at:
        story.append(Paragraph(f'<b>Generated:</b> {escape(generated_at)}', styles['meta']))
    story.append(Spacer(1, 6))

    def _signed(v):
        return '—' if v is None else f'{v:+.1f}'

    if not profiles:
        story.append(Paragraph('No closed feedback rounds with scores were found.',
                               styles['empty']))
    for p in profiles:
        name = escape(p.get('teacher_name') or 'Teacher')
        code = escape(p.get('teacher_emp_code') or '')
        story.append(Paragraph(f'{name} ({code})' if code else name, styles['cellb']))
        story.append(Spacer(1, 3))
        for s in p['subjects']:
            band = (s.get('band') or {}).get('label') or '—'
            story.append(Paragraph(
                f"<b>{escape(str(s['subject_code']))} — {escape(s['subject_name'] or '')}</b>"
                f" &nbsp;|&nbsp; {s['round_count']} round(s)"
                f" &nbsp;|&nbsp; average {s['average_score']:.1f}%"
                f" &nbsp;|&nbsp; net {_signed(s['net_change'])} ({escape(s['net_trend'])})"
                f" &nbsp;|&nbsp; band {escape(band)}", styles['meta']))
            data = [[_cell(h, styles['head'])
                     for h in ('#', 'Form', 'Date', 'Responses', 'Score', 'Step change',
                               'Trend')]]
            for i, r in enumerate(s['rounds'], 1):
                data.append([
                    _cell(i, styles['cell']),
                    _cell(escape(r['title']), styles['cell']),
                    _cell(r['round_date'] or '—', styles['cell']),
                    _cell(r['responses'], styles['cell']),
                    _cell(f"{r['score']:.1f}%", styles['cellb']),
                    _cell(_signed(r['step_change']), styles['cell']),
                    _cell(escape(r['step_trend']), styles['cell']),
                ])
            weights = [6, 70, 24, 18, 18, 22, 22]
            total = sum(weights)
            table = Table(data, colWidths=[doc.width * (w / total) for w in weights],
                          repeatRows=1)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), NAVY),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 0.4, LINE),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, NAVY_SOFT]),
                ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 3.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
            ]))
            story.append(Spacer(1, 2))
            story.append(table)
            story.append(Spacer(1, 8))
        story.append(Spacer(1, 6))

    doc.build(story, canvasmaker=_numbered_canvas(
        footer or 'Faculty Feedback System — subject-wise comparison'))
    return buffer.getvalue()


def build_category_results_pdf(entries, category, title=None, subtitle=None,
                               generated_at='', footer='', filter_lines=None):
    """PDF for one feedback category (main / second / lab).

    ``entries`` is a list of dicts with at least:
      faculty_name, faculty_emp, subject_code_display, subject_display,
      section_code, room, eligible, submitted, pending, completion_pct,
      obtained, maximum, answer_count, students_count, question_stats (optional list),
      rating_breakdown (optional), photo_bytes (optional JPEG/PNG bytes).

    Never includes student identity. Returns PDF bytes.
    """
    from xml.sax.saxutils import escape
    from reportlab.platypus import Image, KeepTogether, HRFlowable

    styles = _styles()
    styles['h2'] = ParagraphStyle(
        'RH2', parent=styles['subtitle'], fontName='Helvetica-Bold',
        fontSize=11, textColor=NAVY, spaceBefore=8, spaceAfter=3)
    styles['q'] = ParagraphStyle(
        'RQ', parent=styles['meta'], fontName='Helvetica-Bold', fontSize=9, spaceBefore=4)
    buffer = BytesIO()
    cat_label = {
        'main': 'Main Faculty Feedback',
        'second': 'Second Faculty Feedback',
        'lab': 'Lab Faculty Feedback',
    }.get(category, category or 'Feedback')
    title = title or f'{cat_label} — Results Report'
    doc = BaseDocTemplate(
        buffer, pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
        title=title, author='Faculty Feedback System')
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='main',
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='page', frames=[frame])])

    story = [Paragraph(escape(title), styles['title'])]
    if subtitle:
        story.append(Paragraph(escape(subtitle), styles['subtitle']))
    if generated_at:
        story.append(Paragraph(f'Generated: {escape(str(generated_at))}', styles['meta']))
    if filter_lines:
        for line in filter_lines:
            story.append(Paragraph(escape(str(line)), styles['meta']))
    story.append(Paragraph(
        f'Category: <b>{escape(cat_label)}</b> · {len(entries)} form(s). '
        'Student identities are never included.', styles['meta']))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width='100%', thickness=0.6, color=LINE, spaceAfter=8))

    if not entries:
        story.append(Paragraph('No forms match the current filters in this category.',
                               styles['empty']))
    else:
        for i, e in enumerate(entries, 1):
            block = []
            name = escape(str(e.get('faculty_name') or 'Faculty'))
            emp = escape(str(e.get('faculty_emp') or '—'))
            subj = escape(str(e.get('subject_code_display') or ''))
            subjn = escape(str(e.get('subject_display') or ''))
            sec = escape(str(e.get('section_code') or '—'))
            room = escape(str(e.get('room') or '—'))
            head = f'<b>{i}. {name}</b> ({emp}) · {subj} {subjn} · Section {sec} · Room {room}'
            block.append(Paragraph(head, styles['h2']))

            photo = e.get('photo_bytes')
            if photo:
                try:
                    img_buf = BytesIO(photo)
                    img = Image(img_buf, width=18 * mm, height=18 * mm)
                    block.append(img)
                    block.append(Spacer(1, 2))
                except Exception:
                    pass

            elig = int(e.get('eligible') or 0)
            sub = int(e.get('submitted') or 0)
            pending = int(e.get('pending') or max(0, elig - sub))
            pct = e.get('completion_pct')
            if pct is None:
                pct = round(100.0 * sub / elig, 2) if elig else 0.0
            obtained = e.get('obtained', 0)
            maximum = e.get('maximum', 0)
            answers = e.get('answer_count', 0)
            students = e.get('students_count', sub)
            block.append(Paragraph(
                f'Eligible: {elig} · Submitted: {sub} · Pending: {pending} · '
                f'Completion: {pct}% · Marks: {obtained} / {maximum} · '
                f'{answers} answers from {students} students',
                styles['meta']))

            breakdown = e.get('rating_breakdown') or []
            if breakdown:
                bits = [f"{escape(str(b.get('label') or ''))}: {b.get('count', 0)}"
                        for b in breakdown]
                block.append(Paragraph('Ratings — ' + ' · '.join(bits), styles['meta']))

            qstats = e.get('question_stats') or []
            for q in qstats:
                qtext = escape(str(q.get('text') or ''))
                qtype = escape(str(q.get('qtype') or ''))
                block.append(Paragraph(f'Q: {qtext} <font color="#66738a">({qtype})</font>',
                                       styles['q']))
                opts = q.get('options') or []
                if opts:
                    rows = [[_cell(h, styles['head']) for h in ('Option', 'Count', '%')]]
                    for o in opts:
                        rows.append([
                            _cell(escape(str(o.get('label') or '')), styles['cell']),
                            _cell(o.get('count', 0), styles['cell']),
                            _cell(f"{o.get('pct', 0)}%", styles['cell']),
                        ])
                    t = Table(rows, colWidths=[doc.width * 0.6, doc.width * 0.2, doc.width * 0.2])
                    t.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
                        ('GRID', (0, 0), (-1, -1), 0.3, LINE),
                        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, NAVY_SOFT]),
                        ('LEFTPADDING', (0, 0), (-1, -1), 3),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                        ('TOPPADDING', (0, 0), (-1, -1), 2),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                    ]))
                    block.append(t)
                # Anonymous text only — never names/rolls
                suggestions = q.get('suggestions') or []
                if suggestions:
                    block.append(Paragraph(
                        'Anonymous comments (not linked to students):', styles['meta']))
                    for c in suggestions[:20]:
                        block.append(Paragraph(f'• {escape(str(c)[:400])}', styles['cell']))
            block.append(Spacer(1, 6))
            block.append(HRFlowable(width='100%', thickness=0.4, color=LINE, spaceAfter=4))
            story.append(KeepTogether(block))

    doc.build(story, canvasmaker=_numbered_canvas(
        footer or f'Faculty Feedback System — {cat_label} results (no student identities)'))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# HOD "Teachers by Score Band" downloads (PDF + Excel)
# ---------------------------------------------------------------------------
BAND_LIST_HEADERS = ['#', 'Teacher', 'Emp code', 'Subject code', 'Subject',
                     'Category', 'Responses', 'Score (%)', 'Email']


def band_list_rows(bands):
    """Flatten ``[{'label', 'rows'}]`` into ``[(band_label, [cell, ...]), ...]``.

    One entry per teacher card shown on screen (one teacher x one subject),
    highest score first within each band. Anonymous answers are never touched.
    """
    cat = {'main': 'Main Faculty', 'second': 'Second Faculty', 'lab': 'Lab Faculty'}
    out = []
    for b in bands:
        for i, r in enumerate(b['rows'], 1):
            out.append((b['label'], [
                i,
                r.get('teacher_name') or 'Faculty',
                r.get('teacher_emp_code') or '',
                r.get('subject_code') or '',
                r.get('subject_name') or '',
                cat.get(r.get('category'), ''),
                r.get('valid_responses') or r.get('response_count') or 0,
                round(float(r['overall_score']), 2),
                r.get('teacher_email') or '',
            ]))
    return out


def build_band_list_pdf(bands, title, subtitle=None, generated_at='', footer='',
                        filter_lines=None):
    """Landscape PDF listing the teachers of each given score band."""
    from xml.sax.saxutils import escape

    styles = _styles()
    styles['h2'] = ParagraphStyle(
        'BH2', parent=styles['subtitle'], fontName='Helvetica-Bold',
        fontSize=11.5, textColor=NAVY, spaceBefore=10, spaceAfter=4)
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=16 * mm,
        title=title, author='Faculty Feedback System')
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='main',
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='page', frames=[frame])])

    total = sum(len(b['rows']) for b in bands)
    story = [Paragraph(escape(title), styles['title'])]
    if subtitle:
        story.append(Paragraph(escape(subtitle), styles['subtitle']))
    if generated_at:
        story.append(Paragraph(f'Generated: {escape(str(generated_at))}', styles['meta']))
    for line in (filter_lines or []):
        story.append(Paragraph(escape(str(line)), styles['meta']))
    story.append(Paragraph(f'Total: <b>{total}</b> teacher card(s).', styles['meta']))

    widths = [8, 50, 20, 22, 56, 26, 22, 18, 45]            # mm
    scale = doc.width / (sum(widths) * mm)
    col_w = [w * mm * scale for w in widths]
    for b in bands:
        story.append(CondPageBreak(40 * mm))
        story.append(Paragraph(f"{escape(b['label'])} · {len(b['rows'])}", styles['h2']))
        if not b['rows']:
            story.append(Paragraph('No teachers in this score band.', styles['empty']))
            continue
        data = [[_cell(h, styles['head']) for h in BAND_LIST_HEADERS]]
        for _label, cells in band_list_rows([b]):
            data.append([_cell(escape(f'{c:.2f}' if j == 7 else str(c)),
                               styles['cellb'] if j == 7 else styles['cell'])
                         for j, c in enumerate(cells)])
        t = Table(data, colWidths=col_w, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), NAVY),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, NAVY_SOFT]),
            ('GRID', (0, 0), (-1, -1), 0.4, LINE),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
    doc.build(story, canvasmaker=_numbered_canvas(footer or title))
    return buffer.getvalue()


def build_band_list_xlsx(bands, title, filter_lines=None):
    """Excel workbook: a 'All bands' sheet plus one sheet per non-empty band."""
    import re
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill('solid', fgColor='1F3A68')
    widths = [6, 30, 12, 14, 38, 16, 11, 11, 32]

    def _sheet(ws, headers, rows):
        ws.append([title])
        ws['A1'].font = Font(bold=True, size=13, color='1F3A68')
        for line in (filter_lines or []):
            ws.append([str(line)])
        ws.append([])
        ws.append(headers)
        hr = ws.max_row
        for c in ws[hr]:
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = head_fill
            c.alignment = Alignment(vertical='center', wrap_text=True)
        for row in rows:
            ws.append(row)
        ncols = len(headers)
        for i in range(ncols):
            ws.column_dimensions[get_column_letter(i + 1)].width = \
                widths[i] if i < len(widths) else 14
        ws.freeze_panes = ws.cell(row=hr + 1, column=1)
        score_col = headers.index('Score (%)') + 1
        for r in range(hr + 1, ws.max_row + 1):
            ws.cell(row=r, column=score_col).number_format = '0.00'

    flat = band_list_rows(bands)
    ws = wb.active
    ws.title = 'All bands'
    _sheet(ws, ['Score band'] + BAND_LIST_HEADERS, [[label] + cells for label, cells in flat])
    ws.column_dimensions['A'].width = 18
    for i, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(i + 2)].width = w
    used = {'All bands'}
    for b in bands:
        if not b['rows']:
            continue
        name = re.sub(r'[\\/*?:\[\]]', '', b['label']).replace('–', '-')[:31] or 'Band'
        while name in used:
            name = name[:28] + '_x'
        used.add(name)
        _sheet(wb.create_sheet(name), BAND_LIST_HEADERS,
               [cells for _l, cells in band_list_rows([b])])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
