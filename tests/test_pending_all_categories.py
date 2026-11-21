"""HOD Pending submissions: Main, Second Faculty and Lab Faculty forms are all
selectable (grouped by category) and each shows its own faculty."""
import re

import pytest

from feedback_system import db, mailer, reports, services
from feedback_system.security import hash_password
from helpers import future_deadline
from test_hod_feedback_second_and_results import (PW, _ensure_sfa, _login_hod,  # noqa: F401
                                                  _seed_hod, app, client)


def _csrf(c, path):
    return re.search(r'name="csrf-token" content="([^"]+)"',
                     c.get(path).get_data(as_text=True)).group(1)


def _three_forms(app, client):
    seed = _seed_hod(app)
    aids = _ensure_sfa(app, seed['branch_id'], 1)
    if not aids:
        pytest.skip('no second-faculty assignment')
    with app.app_context():
        sfa = dict(db.query('SELECT * FROM second_faculty_assignments WHERE id=?',
                            (aids[0],), one=True))
        qsid = db.query('SELECT id FROM question_sets WHERE owner_user_id=? AND is_deleted=0 LIMIT 1',
                        (seed['hod_id'],), one=True)['id']
        db.execute('UPDATE feedback_forms SET is_deleted=1 WHERE session_id=? '
                   'OR second_faculty_assignment_id=?', (sfa['session_id'], aids[0]))
    c = _login_hod(client)
    dl, out = future_deadline(), {}
    for cat, body in (('main', {'session_ids': [sfa['session_id']]}),
                      ('lab', {'session_ids': [sfa['session_id']]}),
                      ('second', {'assignment_ids': [aids[0]]})):
        r = c.post('/hod/feedback/%s/api/create' % cat, headers={'X-CSRF-Token': _csrf(
            c, '/hod/feedback/%s/create' % cat)}, json=dict(body, question_set_id=qsid,
                                                           publish=True, deadline_at=dl))
        d = r.get_json()
        if not (d.get('ok') and d.get('created')):
            pytest.skip('could not publish %s form: %s' % (cat, d))
        out[cat] = d['created'][0]['form_id']
    return c, sfa, out


def test_all_three_categories_listed_and_selectable(app, client):
    c, sfa, fid = _three_forms(app, client)
    page = c.get('/hod/pending').get_data(as_text=True)
    for label in ('Main Faculty Feedback', 'Second Faculty Feedback', 'Lab Faculty Feedback'):
        assert '<optgroup label="%s">' % label in page
    for f in fid.values():
        assert 'value="%d"' % f in page
    with app.app_context():
        main_t = db.query('SELECT emp_code FROM users WHERE id=?', (sfa['main_teacher_user_id'],),
                          one=True)['emp_code']
    # each category's form opens and lists its own teacher
    for cat, emp in (('main', main_t), ('lab', main_t), ('second', sfa['second_emp_code'])):
        html = c.get('/hod/pending?form_id=%d' % fid[cat]).get_data(as_text=True)
        assert re.search(r'<td class="wrap"><strong>[^<]+</strong>\s*<div class="sub">%s' % re.escape(emp), html), cat
        label = {'main': 'Main Faculty Feedback', 'second': 'Second Faculty Feedback',
                 'lab': 'Lab Faculty Feedback'}[cat]
        assert '<span class="badge">%s</span>' % label in html


def test_second_form_lists_only_its_own_second_faculty(app, client):
    c, sfa, fid = _three_forms(app, client)
    with app.app_context():
        other = db.query("SELECT id, emp_code FROM users WHERE role='teacher' AND id NOT IN (?,?) LIMIT 1",
                         (sfa['main_teacher_user_id'], sfa['second_teacher_user_id']), one=True)
        db.execute(
            """INSERT INTO second_faculty_assignments
               (academic_year_id, semester_id, branch_id, timetable_id, session_id, subject_code,
                subject_name, main_emp_code, main_teacher_user_id, second_emp_code,
                second_teacher_user_id, period_no, status, source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'confirmed','test')""",
            (sfa['academic_year_id'], sfa['semester_id'], sfa['branch_id'], sfa['timetable_id'],
             sfa['session_id'], sfa['subject_code'], sfa['subject_name'], sfa['main_emp_code'],
             sfa['main_teacher_user_id'], other['emp_code'], other['id'], sfa['period_no']))
        db.get_db().commit()
        rows = services.pending_by_teacher(fid['second'])
    assert [r['teacher_user_id'] for r in rows] == [sfa['second_teacher_user_id']]


# --------------------------------------------------------------------------- #
# One e-mail per teacher, however many forms (Main / Lab / Second) are pending
# --------------------------------------------------------------------------- #
def _capture_mail(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, 'send_email',
                        lambda to, subject, text, html=None, attachments=None:
                        sent.append({'to': to, 'subject': subject, 'text': text,
                                     'attachments': attachments}) or True)
    return sent


def _give_emails(app, sfa):
    main_mail, second_mail = 'main.teacher@onemail.test', 'second.teacher@onemail.test'
    with app.app_context():
        db.execute('UPDATE users SET email=? WHERE id=?', (main_mail, sfa['main_teacher_user_id']))
        db.execute('UPDATE users SET email=? WHERE id=?', (second_mail, sfa['second_teacher_user_id']))
    return main_mail, second_mail


def test_email_all_sends_one_mail_per_teacher_across_categories(app, client, monkeypatch):
    c, sfa, fid = _three_forms(app, client)
    main_mail, second_mail = _give_emails(app, sfa)
    sent = _capture_mail(monkeypatch)
    r = c.post('/hod/pending/%d/email' % fid['main'],
               data={'_csrf': _csrf(c, '/hod/pending?form_id=%d' % fid['main']), 'all': '1'})
    assert r.status_code == 302
    recipients = [m['to'] for m in sent]
    assert len(recipients) == len(set(recipients)), 'a teacher was e-mailed more than once'
    to_main = [m for m in sent if m['to'] == main_mail]
    to_second = [m for m in sent if m['to'] == second_mail]
    assert len(to_main) == 1 and len(to_second) == 1
    # the Main teacher's single mail covers BOTH their Main and Lab forms
    assert 'Main Faculty Feedback' in to_main[0]['text'] and 'Lab Faculty Feedback' in to_main[0]['text']
    # the Second faculty only has the Second form pending: normal single-form wording
    assert 'Second Faculty' in to_second[0]['text']
    assert 'Main Faculty Feedback' not in to_second[0]['text']
    for m in (to_main[0], to_second[0]):
        assert len(m['attachments']) == 1 and m['attachments'][0][1][:5] == b'%PDF-'
    # roll numbers only travel in the PDF, never in the body
    with app.app_context():
        rolls = services.pending_by_teacher(fid['main'])[0]['pending_rolls']
    assert rolls and not any(x in to_main[0]['text'] for x in rolls)


def test_single_teacher_button_is_also_one_combined_mail(app, client, monkeypatch):
    c, sfa, fid = _three_forms(app, client)
    main_mail, _ = _give_emails(app, sfa)
    sent = _capture_mail(monkeypatch)
    # pressing "Email PDF" on the LAB form still sends one mail that includes Main
    c.post('/hod/pending/%d/email' % fid['lab'],
           data={'_csrf': _csrf(c, '/hod/pending?form_id=%d' % fid['lab']),
                 'teacher_user_id': sfa['main_teacher_user_id']})
    assert [m['to'] for m in sent] == [main_mail]
    assert 'Main Faculty Feedback' in sent[0]['text'] and 'Lab Faculty Feedback' in sent[0]['text']


def test_page_says_what_the_combined_email_covers(app, client):
    c, sfa, fid = _three_forms(app, client)
    html = c.get('/hod/pending?form_id=%d' % fid['main']).get_data(as_text=True)
    assert 'The email also covers' in html and 'Lab Faculty Feedback (' in html
    assert 'One email per teacher' in html


def test_combined_pdf_preview_and_builder(app, client):
    c, sfa, fid = _three_forms(app, client)
    r = c.get('/hod/pending/%d/teacher/%d.pdf' % (fid['lab'], sfa['main_teacher_user_id']))
    assert r.status_code == 200 and r.data[:5] == b'%PDF-'
    block = {'form_title': 'F', 'category_label': 'Main Faculty Feedback', 'subjects': ['S'],
             'sections': ['T1'], 'deadline_text': 'No deadline', 'rolls': ['R1', 'R2']}
    one = reports.build_pending_rolls_pdf_combined('T', 'E1', [block])
    two = reports.build_pending_rolls_pdf_combined(
        'T', 'E1', [block, dict(block, category_label='Lab Faculty Feedback')])
    assert one[:5] == two[:5] == b'%PDF-' and len(two) > len(one)


# --------------------------------------------------------------------------- #
# Forms whose deadline is over are not shown and no reminder is sent for them
# --------------------------------------------------------------------------- #
def test_expired_and_closed_forms_are_hidden_and_not_e_mailed(app, client, monkeypatch):
    c, sfa, fid = _three_forms(app, client)
    main_mail, second_mail = _give_emails(app, sfa)
    with app.app_context():       # Lab form: deadline passed but not swept yet; Second: closed
        db.execute("UPDATE feedback_forms SET deadline_at='2020-01-01T00:00:00' WHERE id=?",
                   (fid['lab'],))
        db.execute("UPDATE feedback_forms SET status='closed' WHERE id=?", (fid['second'],))
    html = c.get('/hod/pending').get_data(as_text=True)
    assert 'value="%d"' % fid['main'] in html
    assert 'value="%d"' % fid['lab'] not in html and 'value="%d"' % fid['second'] not in html
    # a direct link to a hidden form falls back to an open one instead of showing it
    assert 'value="%d" selected' % fid['lab'] not in c.get(
        '/hod/pending?form_id=%d' % fid['lab']).get_data(as_text=True)
    sent = _capture_mail(monkeypatch)
    c.post('/hod/pending/%d/email' % fid['main'],
           data={'_csrf': _csrf(c, '/hod/pending'), 'all': '1'})
    to_main = [m for m in sent if m['to'] == main_mail]
    assert len(to_main) == 1 and 'Lab Faculty Feedback' not in to_main[0]['text']
    assert not [m for m in sent if m['to'] == second_mail]      # its only form is closed
    # the e-mail route also refuses a form that is past its deadline
    sent.clear()
    c.post('/hod/pending/%d/email' % fid['lab'],
           data={'_csrf': _csrf(c, '/hod/pending'), 'all': '1'})
    assert sent == []
