#!/usr/bin/env python3
"""Dean UI browser + print regression harness (synthetic data, recording mail only).

Drives the real Dean Performance page in Chromium (Playwright) against a throwaway
SQLite database. ``feedback_system.mailer.send_email`` is replaced by an in-memory
recording transport, so nothing ever reaches SMTP.  Run from the repository root:

    python scripts/dean_ui_harness.py [--root PATH] [--widths 1280,390]

``--root`` points at another checkout (for example an untouched baseline or an older
checkpoint) so the same checks can be run against it.  Needs: playwright + chromium,
pdfplumber, pypdfium2 and the app requirements.  Exit code 0 only if every check passes.

Mixed-delivery counts asserted (teacher mail, HOD assignment notice and HOD review mail
are counted separately):
  5 ticked teachers: 3 delivered, 1 send returns False, 1 send raises.
  Actions enabled: 3 actions, only for the delivered teachers.
  HOD assignment notices: 3 sent (Alpha 1, Beta 2).  Second run: Alpha's notice fails ->
  the Dean's result must carry a warning (``hod_notify_failed``).
Print: the open result popup is rendered to an A4 PDF; its text, page size and pixels are
inspected, only the filtered (visible) comments are printed and there is no Flagged filter.
"""
import argparse
import logging
import os
import re
import sys
import tempfile
import threading

ap = argparse.ArgumentParser()
ap.add_argument('--root', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ap.add_argument('--widths', default='1280,390')
ARGS = ap.parse_args()
ROOT = os.path.abspath(ARGS.root)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))

from feedback_system import create_app            # noqa: E402
from feedback_system import db as db_module        # noqa: E402
from feedback_system import mailer                 # noqa: E402
from test_dean_hod_scope import _form, _user       # noqa: E402  (existing synthetic builders)

BAND = '95-100'
RESULTS = []


logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('pdfminer').setLevel(logging.CRITICAL)
logging.getLogger('pdfplumber').setLevel(logging.CRITICAL)


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok), detail))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail and not ok else ''), flush=True)


class Outbox:
    """Recording mail transport. Never touches SMTP."""
    def __init__(self):
        self.sent, self.fail_for, self.raise_for = [], set(), set()

    def reset(self):
        self.sent.clear(); self.fail_for.clear(); self.raise_for.clear()

    def __call__(self, to, subject, text, html=None, attachments=None):
        if to in self.raise_for:
            raise RuntimeError('smtp exploded (synthetic)')
        if to in self.fail_for:
            return False
        self.sent.append({'to': to, 'subject': subject, 'text': text})
        return True

    def to(self, prefix=''):
        return sorted(m['to'] for m in self.sent if m['to'].startswith(prefix))


OUT = Outbox()


def build_world(app):
    with app.app_context():
        db_module.execute("INSERT INTO academic_years(name, is_active) VALUES('2026-27', 1)")
        ay = db_module.query('SELECT id FROM academic_years', one=True)['id']
        db_module.execute("INSERT INTO semesters(number, label) VALUES(3, 'Semester 3')")
        sem = db_module.query('SELECT id FROM semesters', one=True)['id']
        db_module.execute("INSERT INTO branches(code, name) VALUES('CSE', 'CSE Dept')")
        br = db_module.query("SELECT id FROM branches WHERE code='CSE'", one=True)['id']
        tt = {code: db_module.insert_id(
            'INSERT INTO timetables(branch_id, semester_id, code, name) VALUES(?,?,?,?)',
            (br, sem, code, code)) for code in ('CSE-A', 'CSE-B')}
        studs = [_user('student', f'HS{i}') for i in range(9)]
        w = {'teachers': {}, 'forms': {}}
        # (code, timetable): A* report to Alpha, B* to Beta
        for code, t in (('TA1', 'CSE-A'), ('TA2', 'CSE-A'), ('TB1', 'CSE-B'),
                        ('TB2', 'CSE-B'), ('TB3', 'CSE-B')):
            tid = _user('teacher', code, f'Teacher {code}')
            w['teachers'][code] = tid
            w['forms'][code] = _form(br, ay, sem, tt[t], tid, studs, 'Excellent', f'S-{code}')
        fid = w['forms']['TA1']
        qid = db_module.insert_id(
            "INSERT INTO form_questions(form_id, position, text, qtype) VALUES(?,?,?,'text')",
            (fid, 2, 'Suggestions'))
        subs = db_module.query('SELECT id FROM submissions WHERE form_id=? ORDER BY id', (fid,))
        for sub, st in zip(subs, [5, 5, 4, 3, 3, 2, 1, None]):
            db_module.execute(
                'INSERT INTO answers(submission_id, question_id, text_value, comment_stars)'
                ' VALUES(?,?,?,?)', (sub['id'], qid, f'Comment text star {st}', st))
        hodA = _user('hod', 'HODA', 'Dr. Alpha')
        hodB = _user('hod', 'HODB', 'Dr. Beta')
        for hid, t in ((hodA, 'CSE-A'), (hodB, 'CSE-B')):
            db_module.execute(
                'INSERT INTO hod_allocations(hod_user_id, academic_year_id, branch_id,'
                ' semester_id, timetable_id) VALUES(?,?,?,?,?)', (hid, ay, br, sem, tt[t]))
        dean = _user('dean', 'HDEAN1', 'Dean One')
        for h in (hodA, hodB):
            db_module.execute(
                'INSERT INTO dean_hod_assignments(dean_user_id, hod_user_id) VALUES(?,?)', (dean, h))
        w['ta1'] = w['teachers']['TA1']
        return w


def clear_actions(app):
    with app.app_context():
        db_module.execute('DELETE FROM improvement_action_log')
        db_module.execute('DELETE FROM improvement_actions')


def actions(app):
    with app.app_context():
        return [dict(a) for a in db_module.query('SELECT * FROM improvement_actions ORDER BY id')]


def login(ctx, base):
    r = ctx.request.get(base + '/dean/login')
    tok = re.search(r'name="_csrf" value="([^"]+)"', r.text()) or \
        re.search(r'name="csrf-token" content="([^"]+)"', r.text())
    resp = ctx.request.post(base + '/dean/login', form={
        'emp_code': 'HDEAN1', 'password': 'pass12345', '_csrf': tok.group(1)}, max_redirects=0)
    return resp.status == 302


def result_text(page):
    page.wait_for_function(
        "() => { const b = document.querySelector('[data-result]'); "
        "return b && !b.hidden && !/Sending/.test(b.textContent); }", timeout=15000)
    box = page.locator('#band-mail-form [data-result]')
    return box.inner_text(), (box.get_attribute('class') or '')


def send_all(page, w, token_tag):
    page.goto(page.url.split('#')[0])           # fresh page: fresh submit token
    page.fill('#band-message', 'Dear {name}, your score is {score} for {subject}. {dean}')
    page.check('[data-act-toggle]')
    page.locator('[data-send-teachers]').click()
    return result_text(page)


def run_width(pw, base, app, w, width):
    tag = f'@{width}'
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={'width': width, 'height': 900 if width > 600 else 780},
                              accept_downloads=False)
    check(f'login{tag}', login(ctx, base))
    page = ctx.new_page()
    page.on('dialog', lambda d: d.accept())      # window.confirm() before sending
    url = f'{base}/dean/performance?band={BAND}'
    page.goto(url)

    # ---- mixed delivery: 5 ticked, 3 delivered, 1 False, 1 raises ----------------------
    clear_actions(app); OUT.reset()
    OUT.fail_for.add('ta2@t.edu'.replace('t.edu', 't.edu'))
    OUT.raise_for.add('tb3@t.edu')
    text, cls = send_all(page, w, tag)
    teacher_to = OUT.to('t')
    check(f'mixed: teacher mail delivered 3{tag}', teacher_to == ['ta1@t.edu', 'tb1@t.edu', 'tb2@t.edu'],
          str(teacher_to))
    check(f'mixed: 2 teacher mails failed (message){tag}', '2 emails could not be sent' in text, text)
    acts = actions(app)
    owners = sorted(a['owner_user_id'] for a in acts)
    delivered = {w['teachers'][k] for k in ('TA1', 'TB1', 'TB2')}
    check(f'mixed: 3 actions, only for delivered teachers{tag}',
          len(acts) == 3 and {a['teacher_user_id'] for a in acts} == delivered, str(acts))
    hod_to = OUT.to('hod')
    check(f'mixed: 3 HOD assignment notices (Alpha 1, Beta 2){tag}',
          hod_to == ['hoda@t.edu', 'hodb@t.edu', 'hodb@t.edu'], str(hod_to))
    check(f'mixed: warning level, no HOD-notify warning when notices delivered{tag}',
          'is-warning' in cls and 'could not be notified' not in text, cls + ' | ' + text)
    check(f'mixed: "View in Actions" link present{tag}',
          page.locator('#band-mail-form [data-result] a.mail-link').count() == 1)

    # ---- HOD assignment notice fails: Dean must be warned -------------------------------
    clear_actions(app); OUT.reset()
    OUT.fail_for.add('hoda@t.edu')
    page.goto(url)
    page.fill('#band-message', 'Dear {name}, your score is {score} for {subject}. {dean}')
    for cb in ('input.band-cb[value="%d"]' % w['forms'][k] for k in ('TA2', 'TB3')):
        page.uncheck(cb)                           # 3 clean teachers: TA1 (Alpha), TB1, TB2 (Beta)
    page.check('[data-act-toggle]')
    page.locator('[data-send-teachers]').click()
    text, cls = result_text(page)
    check(f'hod-notify-fail: teachers all delivered, 3 actions kept{tag}',
          OUT.to('t') == ['ta1@t.edu', 'tb1@t.edu', 'tb2@t.edu'] and len(actions(app)) == 3, text)
    check(f'hod-notify-fail: only Beta notices went out (2){tag}',
          OUT.to('hod') == ['hodb@t.edu', 'hodb@t.edu'], str(OUT.to('hod')))
    check(f'hod-notify-fail: Dean sees a warning naming the failed notice{tag}',
          'is-warning' in cls and '1 HOD could not be notified' in text, cls + ' | ' + text)

    # ---- optional action OFF: nothing created, no HOD mail ------------------------------
    clear_actions(app); OUT.reset()
    page.goto(url)
    page.fill('#band-message', 'Dear {name}, {score}. {dean}')
    page.locator('[data-send-teachers]').click()
    text, cls = result_text(page)
    check(f'actions off: 0 actions, no HOD mail, success{tag}',
          not actions(app) and not OUT.to('hod') and len(OUT.to('t')) == 5 and 'is-success' in cls,
          text)

    # ---- repeat send skips existing actions ---------------------------------------------
    clear_actions(app); OUT.reset()
    send_all(page, w, tag)
    n_first = len(actions(app))
    OUT.reset()
    text, cls = send_all(page, w, tag)
    check(f'repeat send: existing open actions skipped, no duplicates{tag}',
          n_first == 5 and len(actions(app)) == 5 and 'skipped: an open action already exists' in text, text)

    # ---- identical re-submit is replayed, not re-sent -----------------------------------
    clear_actions(app); OUT.reset()
    page.goto(url)
    page.fill('#band-message', 'Replay {name} {score}')
    page.locator('[data-send-teachers]').click()
    result_text(page)
    sent_once = len(OUT.sent)
    tok = page.locator('#band-mail-form input[name=submit_token]').input_value()
    csrf = page.locator('#band-mail-form input[name=_csrf]').input_value()
    form = {'band': BAND, 'subject': 'x', 'message': 'Replay {name} {score}', 'submit_token': tok,
            '_csrf': csrf, 'form_id': [str(w['forms'][k]) for k in w['forms']]}
    r = ctx.request.post(base + '/dean/performance/bands/send', form=form,
                         headers={'X-Requested-With': 'fetch'})
    check(f'identical re-submit replayed (duplicate flag, no new mail){tag}',
          r.ok and r.json().get('duplicate') is True and len(OUT.sent) == sent_once, r.text()[:200])

    # ---- mail dialog fits the viewport ---------------------------------------------------
    page.goto(url)
    page.locator('[data-mail-teacher]').first.click()
    dlg = page.locator('#mail-dialog')
    dlg.wait_for(state='visible')
    box = dlg.bounding_box()
    vp = page.viewport_size
    check(f'mail dialog fits viewport{tag}',
          box and box['x'] >= -1 and box['y'] >= -1 and box['x'] + box['width'] <= vp['width'] + 1
          and box['y'] + box['height'] <= vp['height'] + 1, str(box))
    page.keyboard.press('Escape')

    # ---- result popup + real A4 PDF -------------------------------------------------------
    page.goto(url)
    # NB: end with a non-function value, or Playwright would invoke the stub itself
    page.evaluate('() => { window.__printCalls = 0; window.print = function () { window.__printCalls++; }; }')
    page.locator(f'[data-result-url*="/performance/faculty/{w["ta1"]}/result"]').first.click()
    rd = page.locator('#result-dialog')
    rd.locator('.rd-card').first.wait_for(state='visible')
    box = rd.bounding_box()
    check(f'result popup fills mobile viewport / fits desktop{tag}',
          box and box['x'] + box['width'] <= vp['width'] + 1
          and (width > 600 or box['width'] >= vp['width'] - 2), str(box))
    check(f'no Flagged filter; rating filters present{tag}',
          'Flagged' not in rd.inner_text()
          and rd.locator('.rd-filters button').count() == 4)
    rd.locator('.rd-filters button[data-f="pos"]').click()      # positive only: 3 of 8
    page.locator('[data-rd-print]').click()
    calls = page.evaluate('window.__printCalls')
    cls_on = page.evaluate("document.documentElement.classList.contains('rd-printing')")
    check(f'print button adds rd-printing and calls window.print once{tag}',
          calls == 1 and cls_on, f'calls={calls} rd-printing={cls_on}')
    pdf = page.pdf(format='A4', print_background=True)
    path = os.path.join(tempfile.mkdtemp(prefix='dean-pdf-'), f'result{width}.pdf')
    open(path, 'wb').write(pdf)
    import pdfplumber
    import pypdfium2 as pdfium
    with pdfplumber.open(path) as doc:
        text = '\n'.join((p.extract_text() or '') for p in doc.pages)
        wpt, hpt = doc.pages[0].width, doc.pages[0].height
    check(f'PDF is A4{tag}', abs(wpt - 595.3) < 2 and abs(hpt - 841.9) < 2, f'{wpt}x{hpt}')
    check(f'PDF is not blank (text present){tag}', len(text.strip()) > 200, f'{len(text)} chars')
    img = pdfium.PdfDocument(path)[0].render(scale=0.5).to_pil().convert('L')
    dark = sum(1 for px in img.tobytes() if px < 200)
    check(f'PDF page 1 has visible ink{tag}', dark > 500, f'{dark} dark px')
    check(f'PDF shows teacher, score and question table{tag}',
          'Teacher TA1' in text and '100.00%' in text and 'Question-wise results' in text)
    shown = [f'Comment text star {s}' for s in (5, 4)]
    hidden = [f'Comment text star {s}' for s in (3, 2, 1, 'None')]
    check(f'PDF prints only the comments currently shown (filtered){tag}',
          all(s in text for s in shown) and not any(s in text for s in hidden), text[-400:])
    check(f'PDF has no page chrome (topbar/filters/buttons){tag}',
          'Print / Save as PDF' not in text and 'Show more comments' not in text)
    page.evaluate("window.dispatchEvent(new Event('afterprint'))")
    check(f'afterprint removes rd-printing{tag}',
          not page.evaluate("document.documentElement.classList.contains('rd-printing')"))
    # close buttons
    page.keyboard.press('Escape')
    check(f'Escape closes the result popup{tag}', not rd.is_visible())
    page.locator(f'[data-result-url*="/performance/faculty/{w["ta1"]}/result"]').first.click()
    rd.locator('.rd-card').first.wait_for(state='visible')
    rd.locator('[data-rd-close]').last.click()
    check(f'Close button closes the result popup{tag}', not rd.is_visible())
    browser.close()


def main():
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    tmp = tempfile.mkdtemp(prefix='ffs-harness-')
    app = create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmp, 'h.db'), 'DATABASE_URL': None,
        'SECRET_KEY': 'harness-secret', 'AUTO_IMPORT': False, 'ADMIN_EMAIL': 'admin@college.edu',
        'ADMIN_PASSWORD': 'admin123', 'REQUIRE_ACCOUNT_SETUP': False, 'LOGIN_MAX_ATTEMPTS': 500})
    mailer.send_email = OUT                                   # recording transport only
    w = build_world(app)
    srv = make_server('127.0.0.1', 0, app, threaded=True)
    base = f'http://127.0.0.1:{srv.server_port}'
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            for width in [int(x) for x in ARGS.widths.split(',')]:
                run_width(pw, base, app, w, width)
    finally:
        srv.shutdown()
    bad = [r for r in RESULTS if not r[1]]
    print(f'\n{len(RESULTS) - len(bad)}/{len(RESULTS)} checks passed (root={ROOT})')
    for n, _, d in bad:
        print('  FAILED:', n, d[:300])
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
