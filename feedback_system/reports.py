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
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)

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
