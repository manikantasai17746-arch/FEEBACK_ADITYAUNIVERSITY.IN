"""Phase 3 UI tests (T30-T32 + real click-through of the four steps) in headless Chromium.

Skipped automatically when Playwright/Chromium is not installed.  A real Flask
server is started on a free port so the actual JavaScript runs against the real
routes and database.
"""
import os
import re
import socket
import tempfile
import threading

import pytest

pw = pytest.importorskip('playwright.sync_api')

from werkzeug.serving import make_server  # noqa: E402

from feedback_system import create_app, db  # noqa: E402
from feedback_system.security import hash_password  # noqa: E402

PW = 'pass12345'


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def live():
    tmp = tempfile.mkdtemp(prefix='ffs-phase3-ui-')
    app = create_app({
        'TESTING': True, 'DATABASE': os.path.join(tmp, 'ui.db'), 'DATABASE_URL': None,
        'SECRET_KEY': 'ui-secret', 'AUTO_IMPORT': False, 'ADMIN_EMAIL': 'a@college.edu',
        'ADMIN_PASSWORD': 'admin123', 'LOGIN_MAX_ATTEMPTS': 100, 'REQUIRE_ACCOUNT_SETUP': False,
    })
    with app.app_context():
        db.execute("INSERT INTO branches(code,name) VALUES('CSE','Computer Science')")
        b = db.query("SELECT id FROM branches", one=True)['id']
        db.execute("INSERT INTO semesters(number,label) VALUES(5,'Semester V')")
        sem = db.query("SELECT id FROM semesters", one=True)['id']
        db.execute("INSERT INTO academic_years(name,is_active) VALUES('2025-26',1)")
        y = db.query("SELECT id FROM academic_years", one=True)['id']
        db.execute("""INSERT INTO users(role,username,emp_code,name,email,password_hash,is_active,
                      onboarding_complete,first_login_complete,email_verified)
                      VALUES('teacher','UIT','UIT','A Very Long Teacher Name For Mobile Layout',
                             'u@t.edu',?,1,1,1,1)""", (hash_password(PW),))
        t = db.query("SELECT id FROM users WHERE username='UIT'", one=True)['id']
        ids = {}
        for sec in ('A', 'B'):
            tt = db.insert_id('INSERT INTO timetables(branch_id,semester_id,academic_year_id,code,name) '
                              'VALUES(?,?,?,?,?)', (b, sem, y, sec, sec))
            for code, name in (('ADSI', 'Advanced Data Structures and Algorithm Analysis'),
                               ('DBMS', 'Database Management Systems')):
                ids[(code, sec)] = db.insert_id(
                    'INSERT INTO class_sessions(timetable_id,subject_code,subject_name,'
                    'teacher_user_id,period_no) VALUES(?,?,?,?,?)', (tt, code, name, t, 2))
        db.get_db().commit()
    port = _free_port()
    server = make_server('127.0.0.1', port, app, threaded=True)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    yield {'url': f'http://127.0.0.1:{port}', 'app': app, 'ids': ids}
    server.shutdown()


@pytest.fixture(scope='module')
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:                       # pragma: no cover
            pytest.skip(f'Chromium unavailable: {exc}')
        yield b
        b.close()


@pytest.fixture(autouse=True)
def _reset(live):
    yield
    with live['app'].app_context():
        db.execute('DELETE FROM teacher_subject_settings')


def _page(browser, live, width, height):
    ctx = browser.new_context(viewport={'width': width, 'height': height})
    page = ctx.new_page()
    page.goto(live['url'] + '/teacher/login')
    page.fill('input[name=emp_code]', 'UIT')
    page.fill('input[name=password]', PW)
    page.click('form button[type=submit], form input[type=submit]')
    page.goto(live['url'] + '/teacher/assignment-setup')
    page.wait_for_selector('#tas')
    return ctx, page


def _no_overflow(page):
    return page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1')


def _visible_panel(page):
    return page.evaluate("[...document.querySelectorAll('.tas-panel')].filter(p=>!p.hidden)"
                         ".map(p=>p.dataset.panel)")


def _walk_all_steps(page):
    for n in (1, 2, 3, 4):
        page.click(f'.tas-step[data-go="{n}"]')
        assert _visible_panel(page) == [str(n)]
        assert _no_overflow(page), f'horizontal overflow on step {n}'


# --------------------------------------------------------------------------- #
def test_t30_desktop_layout(browser, live):
    ctx, page = _page(browser, live, 1280, 800)
    try:
        assert page.is_visible('#tasSteps') and _visible_panel(page) == ['1']
        assert page.locator('.tas-step').count() == 4
        # step 1 lists every section of every subject, grouped by subject code
        assert page.locator('[data-panel="1"] .tas-subject').count() == 2
        assert page.locator('[data-panel="1"] .tas-row').count() == 4
        box = page.locator('.tas-steps').bounding_box()
        assert box['width'] > 600
        # stepper is one row on desktop
        ys = page.evaluate("[...document.querySelectorAll('.tas-step')].map(e=>Math.round(e.getBoundingClientRect().top))")
        assert len(set(ys)) == 1
        _walk_all_steps(page)
    finally:
        ctx.close()


def test_t31_mobile_layout(browser, live):
    ctx, page = _page(browser, live, 375, 740)
    try:
        assert _no_overflow(page)
        _walk_all_steps(page)
        # touch targets are at least 44px tall on the interactive controls
        page.click('.tas-step[data-go="3"]')
        h = page.evaluate("Math.min(...[...document.querySelectorAll('[data-panel=\"3\"] .tas-radio span')].map(e=>e.getBoundingClientRect().height))")
        assert h >= 44
        page.click('.tas-step[data-go="2"]')
        assert page.locator('.tas-room').first.bounding_box()['height'] >= 44
        # bulk buttons exist for multi-section subjects and work
        page.click('.tas-step[data-go="3"]')
        page.locator('[data-bulk="available"]').first.click()
        assert page.locator('[data-panel="3"] input[value="available"]:checked').count() == 2
    finally:
        ctx.close()


def test_full_workflow_navigate_save_reload_edit(browser, live):
    ids = live['ids']
    ctx, page = _page(browser, live, 1280, 900)
    try:
        # Step 2: rooms (one left empty on purpose)
        page.click('#tasNext')
        assert _visible_panel(page) == ['2']
        page.fill(f'#room-{ids[("ADSI","A")]}', '205')
        page.fill(f'#room-{ids[("ADSI","B")]}', '206')
        page.fill(f'#room-{ids[("DBMS","A")]}', '301')
        # Step 3: labs
        page.click('#tasNext')
        assert _visible_panel(page) == ['3']
        page.locator(f'input[name="lab-{ids[("ADSI","A")]}"][value="available"]').check(force=True)
        page.locator(f'input[name="lab-{ids[("ADSI","B")]}"][value="none"]').check(force=True)
        page.locator(f'input[name="lab-{ids[("DBMS","A")]}"][value="available"]').check(force=True)
        # Previous keeps what was typed
        page.click('#tasPrev')
        assert page.input_value(f'#room-{ids[("ADSI","A")]}') == '205'
        page.click('#tasNext')
        # Step 4: summary shows pending (DBMS-B has no lab yet) before saving
        page.click('#tasNext')
        assert _visible_panel(page) == ['4'] and page.is_visible('#tasSave')
        stats = page.inner_text('#tasStats')
        assert re.search(r'4\s*Total assigned subjects', stats)
        assert re.search(r'3\s*Subjects configured', stats)
        assert re.search(r'1\s*Pending configuration', stats)
        assert re.search(r'2\s*Lab Available', stats) and re.search(r'1\s*No Lab', stats)
        assert re.search(r'1\s*Without room number', stats)
        # Confirmation dialog: "Go back" saves nothing
        page.click('#tasSave')
        assert page.is_visible('#tasModal') and 'still have no lab choice' in page.inner_text('#tasModalBody')
        page.click('#tasModalCancel')
        assert not page.is_visible('#tasModal')
        with live['app'].app_context():
            assert db.query('SELECT COUNT(*) c FROM teacher_subject_settings', one=True)['c'] == 0
        # Confirm -> success
        page.click('#tasSave'); page.click('#tasConfirm')
        page.wait_for_selector('#tasAlert.ok')
        assert 'Configuration saved' in page.inner_text('#tasAlert')
        assert 'pending' in page.inner_text('#tasStatus')
        with live['app'].app_context():
            rows = {r['session_id']: dict(r) for r in db.query('SELECT * FROM teacher_subject_settings')}
        assert rows[ids[('ADSI', 'A')]]['room'] == '205' and rows[ids[('ADSI', 'A')]]['lab_status'] == 'available'
        assert rows[ids[('ADSI', 'B')]]['lab_status'] == 'none'
        assert rows[ids[('DBMS', 'A')]]['room'] == '301'
        # Reload: values persist and step 1 shows them
        page.reload(); page.wait_for_selector('#tas')
        assert page.inner_text(f'[data-view-room="{ids[("ADSI","A")]}"]') == '205'
        assert page.inner_text(f'[data-view-lab="{ids[("ADSI","B")]}"]') == 'No Lab'
        assert page.inner_text(f'[data-view-lab="{ids[("DBMS","B")]}"]') == 'Not configured'
        # Edit: finish the last one and change a room, via the review step's Edit button
        page.click('.tas-step[data-go="4"]')
        page.click('#tasEdit')
        assert _visible_panel(page) == ['2']
        page.fill(f'#room-{ids[("ADSI","A")]}', '210')
        page.click('#tasNext')
        page.locator(f'input[name="lab-{ids[("DBMS","B")]}"][value="none"]').check(force=True)
        page.click('#tasNext'); page.click('#tasSave'); page.click('#tasConfirm')
        page.wait_for_selector('#tasAlert.ok')
        assert 'Setup complete' in page.inner_text('#tasStatus')
        with live['app'].app_context():
            r = db.query('SELECT room FROM teacher_subject_settings WHERE session_id=?',
                         (ids[('ADSI', 'A')],), one=True)
            assert r['room'] == '210'
            assert db.query('SELECT COUNT(*) c FROM teacher_subject_settings', one=True)['c'] == 4
            assert db.query('SELECT COUNT(*) c FROM lab_faculty_assignments', one=True)['c'] == 0
    finally:
        ctx.close()


def test_t32_loading_error_and_success_states(browser, live):
    ids = live['ids']
    ctx, page = _page(browser, live, 1280, 900)
    try:
        page.click('.tas-step[data-go="2"]')
        page.fill(f'#room-{ids[("ADSI","A")]}', '205')
        page.click('.tas-step[data-go="4"]')

        # --- client-side validation error: bad characters block the review/save
        page.click('.tas-step[data-go="2"]')
        page.fill(f'#room-{ids[("ADSI","B")]}', '<b>x</b>')
        page.click('#tasNext'); page.click('#tasNext')
        assert _visible_panel(page) == ['2']
        assert page.is_visible('.tas-room.is-invalid')
        assert 'fix the highlighted' in page.inner_text('#tasAlert')
        page.fill(f'#room-{ids[("ADSI","B")]}', '206')

        # --- loading state: request held open, button shows "Saving…" and is disabled
        gate = threading.Event()

        def slow(route):
            gate.wait(5)
            route.continue_()
        page.route('**/assignment-setup/api/save', slow)
        page.click('.tas-step[data-go="4"]'); page.click('#tasSave'); page.click('#tasConfirm')
        page.wait_for_function("document.getElementById('tasSave').textContent.includes('Saving')")
        assert page.is_disabled('#tasSave') and page.is_disabled('#tasConfirm')
        gate.set()
        page.wait_for_selector('#tasAlert.ok')
        assert page.inner_text('#tasSave').strip() == 'Save Configuration'
        page.unroute('**/assignment-setup/api/save')

        # --- server error: friendly message, form state kept, nothing claimed as saved
        page.route('**/assignment-setup/api/save', lambda r: r.fulfill(
            status=500, content_type='application/json',
            body='{"ok": false, "error": "Could not save right now. Nothing was changed; please try again."}'))
        page.click('.tas-step[data-go="2"]')
        page.fill(f'#room-{ids[("ADSI","A")]}', '999')
        page.click('.tas-step[data-go="4"]'); page.click('#tasSave'); page.click('#tasConfirm')
        page.wait_for_selector('#tasAlert.err')
        assert 'Nothing was changed' in page.inner_text('#tasAlert')
        assert not page.is_visible('#tasModal')
        assert page.input_value(f'#room-{ids[("ADSI","A")]}') == '999'
        page.unroute('**/assignment-setup/api/save')

        # --- network failure
        page.route('**/assignment-setup/api/save', lambda r: r.abort())
        page.click('#tasSave'); page.click('#tasConfirm')
        page.wait_for_selector('#tasAlert.err')
        assert 'Network problem' in page.inner_text('#tasAlert')
        page.unroute('**/assignment-setup/api/save')

        # --- cancel reverts to the last saved values
        page.click('.tas-step[data-go="2"]')
        page.once('dialog', lambda d: d.accept())
        page.click('#tasCancel')
        assert page.input_value(f'#room-{ids[("ADSI","A")]}') == '205'
    finally:
        ctx.close()


def test_empty_state_renders(browser, live):
    with live['app'].app_context():
        db.execute("""INSERT INTO users(role,username,emp_code,name,email,password_hash,is_active,
                      onboarding_complete,first_login_complete,email_verified)
                      VALUES('teacher','EMPTY','EMPTY','No Subjects','e@t.edu',?,1,1,1,1)""",
                   (hash_password(PW),))
    ctx = browser.new_context(viewport={'width': 375, 'height': 700})
    page = ctx.new_page()
    try:
        page.goto(live['url'] + '/teacher/login')
        page.fill('input[name=emp_code]', 'EMPTY'); page.fill('input[name=password]', PW)
        page.click('form button[type=submit], form input[type=submit]')
        page.goto(live['url'] + '/teacher/assignment-setup')
        assert 'No subjects assigned yet' in page.inner_text('body')
        assert _no_overflow(page)
    finally:
        ctx.close()
