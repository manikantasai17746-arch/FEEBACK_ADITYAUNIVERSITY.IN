"""Phase 1 correctness: category-aware phase rules + faculty identity.

Regression coverage for:
* Main / Second / Lab Phase 1 coexistence on the same session
* validate_form_for_publish passes category + second_faculty_assignment_id
* form_teachers_map returns Second Faculty for second forms (not Main)
* phase conflict messages name the correct faculty
* duplicate Second Phase 1 for the same SFA is still rejected

Run:
    python -m pytest tests/test_phase1_category_identity.py -v
"""
from datetime import datetime, timedelta

from feedback_system import db, services
from feedback_system import category_forms as cf
from feedback_system import feedback_categories as fc
from feedback_system.security import hash_password


FUTURE = (datetime.utcnow() + timedelta(days=7)).isoformat(timespec='seconds')


def _q():
    return [{'text': 'Quality', 'qtype': 'rating', 'is_required': True,
             'options': [{'label': 'Excellent', 'value': 5}, {'label': 'Poor', 'value': 1}]}]


def _seed_world(app):
    """Minimal Main + Second Faculty world on one lab-available session."""
    with app.app_context():
        # Prefer real imported session if present; otherwise create one.
        sess = db.query(
            """SELECT cs.id AS sid, cs.teacher_user_id AS tid, t.branch_id AS bid,
                      cs.subject_code AS sc, cs.subject_name AS sn, t.id AS ttid,
                      t.semester_id AS sem, t.academic_year_id AS ay, u.emp_code AS emp,
                      u.name AS tname
               FROM class_sessions cs
               JOIN timetables t ON t.id = cs.timetable_id
               JOIN users u ON u.id = cs.teacher_user_id
               WHERE cs.is_deleted = 0 AND u.is_active = 1
               ORDER BY cs.id LIMIT 1""", one=True)
        if not sess:
            raise RuntimeError('No class_sessions in test DB; AUTO_IMPORT required')
        sess = dict(sess)

        # Ensure lab available
        existing = db.query(
            'SELECT 1 FROM teacher_subject_settings WHERE session_id=?',
            (sess['sid'],), one=True)
        if existing:
            db.execute(
                "UPDATE teacher_subject_settings SET lab_available=1, lab_status='available' "
                "WHERE session_id=?", (sess['sid'],))
        else:
            db.execute(
                """INSERT INTO teacher_subject_settings(session_id, teacher_user_id,
                       lab_available, lab_status) VALUES(?,?,1,'available')""",
                (sess['sid'], sess['tid']))

        # Second faculty user (not the main teacher)
        second = db.query(
            """SELECT id, emp_code, name FROM users
               WHERE role='teacher' AND is_active=1 AND id <> ?
               ORDER BY id LIMIT 1""", (sess['tid'],), one=True)
        if not second:
            sid = db.execute_returning_id(
                """INSERT INTO users(role, username, emp_code, name, email, password_hash,
                   is_active, onboarding_complete, first_login_complete, email_verified)
                   VALUES('teacher','SFTEST','SF001','NOOKALA JAYA PAVANI','sf@t.edu',?,1,1,1,1)""",
                (hash_password('teacher@123'),))
            second = db.query('SELECT id, emp_code, name FROM users WHERE id=?', (sid,), one=True)
        second = dict(second)

        # Confirmed SFA for this session
        sfa = db.query(
            """SELECT id FROM second_faculty_assignments
               WHERE session_id=? AND second_teacher_user_id=? AND status='confirmed'
               ORDER BY id DESC LIMIT 1""", (sess['sid'], second['id']), one=True)
        if not sfa:
            sfa_id = db.execute_returning_id(
                """INSERT INTO second_faculty_assignments(
                       academic_year_id, semester_id, branch_id, timetable_id, session_id,
                       subject_code, subject_name, main_emp_code, main_teacher_user_id,
                       second_emp_code, second_teacher_user_id, status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,'confirmed')""",
                (sess['ay'], sess['sem'], sess['bid'], sess['ttid'], sess['sid'],
                 sess['sc'], sess['sn'], sess['emp'], sess['tid'],
                 second['emp_code'], second['id']))
        else:
            sfa_id = sfa['id']

        admin = db.query("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)['id']
        return {
            'sess': sess,
            'second': second,
            'sfa_id': sfa_id,
            'admin_id': admin,
            'main_name': sess['tname'],
            'second_name': second['name'],
        }


def _make_category_form(admin_id, category, assignment_id, phase='phase1', status='draft'):
    title = f'{phase} {category}'
    payload = {
        'title': title,
        'instructions': services.DEFAULT_INSTRUCTIONS,
        'phase': phase,
        'category': category,
        'scope': 'session',
        'deadline_at': FUTURE,
    }
    sc = fc.resolve_scope(category, assignment_id)
    payload.update({
        'academic_year_id': sc['academic_year_id'],
        'semester_id': sc['semester_id'],
        'branch_id': sc['branch_id'],
        'timetable_id': sc['timetable_id'],
        'session_id': sc['session_id'],
        'subject_code': sc['subject_code'],
        'subject_name': sc['subject_name'],
        'second_faculty_assignment_id': sc.get('second_faculty_assignment_id'),
        'lab_faculty_assignment_id': sc.get('lab_faculty_assignment_id'),
        'category_basis': 'declared',
    })
    fid = services.save_form(payload, _q(), admin_id, session_ids=[sc['session_id']])
    if status != 'draft':
        db.get_db().execute('UPDATE feedback_forms SET status=? WHERE id=?', (status, fid))
        db.get_db().commit()
    return fid


def test_main_second_lab_phase1_coexist_on_publish(app):
    """Main + Second + Lab Phase 1 for the same session must all be publishable."""
    w = _seed_world(app)
    with app.app_context():
        sid = w['sess']['sid']
        # Clear live forms for this session so the test is deterministic
        db.execute(
            """UPDATE feedback_forms SET is_deleted=1
               WHERE session_id=? AND phase='phase1' AND is_deleted=0""", (sid,))

        main_id = _make_category_form(w['admin_id'], 'main', sid, 'phase1')
        lab_id = _make_category_form(w['admin_id'], 'lab', sid, 'phase1')
        second_id = _make_category_form(w['admin_id'], 'second', w['sfa_id'], 'phase1')

        # Phase rules WITH category must not conflict across categories
        # (exclude the form under consideration — same as validate_form_for_publish).
        assert not services.phase_rule_errors(
            'phase1', [sid], exclude_form_id=main_id, category='main')
        assert not services.phase_rule_errors(
            'phase1', [sid], exclude_form_id=lab_id, category='lab')
        assert not services.phase_rule_errors(
            'phase1', [sid], exclude_form_id=second_id, category='second',
            second_assignment_id=w['sfa_id'])

        # Publish validation must not reject Second because of Main Phase 1
        main_errs = services.validate_form_for_publish(main_id)
        lab_errs = services.validate_form_for_publish(lab_id)
        second_errs = services.validate_form_for_publish(second_id)

        assert not any('already exists' in e for e in main_errs), main_errs
        assert not any('already exists' in e for e in lab_errs), lab_errs
        assert not any('already exists' in e for e in second_errs), second_errs


def test_duplicate_second_phase1_still_rejected(app):
    """Two Second Phase 1 forms for the SAME SFA must conflict."""
    w = _seed_world(app)
    with app.app_context():
        db.execute(
            """UPDATE feedback_forms SET is_deleted=1
               WHERE second_faculty_assignment_id=? AND phase='phase1' AND is_deleted=0""",
            (w['sfa_id'],))
        first = _make_category_form(w['admin_id'], 'second', w['sfa_id'], 'phase1', status='open')
        second = _make_category_form(w['admin_id'], 'second', w['sfa_id'], 'phase1')

        errs = services.phase_rule_errors(
            'phase1', [w['sess']['sid']], exclude_form_id=second,
            category='second', second_assignment_id=w['sfa_id'])
        assert errs, 'Expected duplicate Second Phase 1 to be rejected'
        assert any(f'form #{first}' in e for e in errs)

        pub_errs = services.validate_form_for_publish(second)
        assert any('already exists' in e for e in pub_errs), pub_errs


def test_form_teachers_map_second_uses_second_faculty(app):
    """List Teacher(s) for a Second form must show Second Faculty, not Main."""
    w = _seed_world(app)
    with app.app_context():
        second_id = _make_category_form(w['admin_id'], 'second', w['sfa_id'], 'phase1')
        main_id = _make_category_form(w['admin_id'], 'main', w['sess']['sid'], None)

        tm = services.form_teachers_map([second_id, main_id])
        second_labels = ' | '.join(tm.get(second_id) or [])
        main_labels = ' | '.join(tm.get(main_id) or [])

        assert w['second_name'] in second_labels, (
            f'Second form teachers={tm.get(second_id)!r} expected {w["second_name"]!r}')
        assert w['main_name'] not in second_labels or w['second_name'] == w['main_name'], (
            f'Second form incorrectly shows Main Faculty: {second_labels!r}')
        assert w['main_name'] in main_labels, (
            f'Main form teachers={tm.get(main_id)!r} expected {w["main_name"]!r}')


def test_phase_error_message_names_second_faculty(app):
    """Conflict message for a Second form must name the Second Faculty member."""
    w = _seed_world(app)
    with app.app_context():
        db.execute(
            """UPDATE feedback_forms SET is_deleted=1
               WHERE second_faculty_assignment_id=? AND phase='phase1' AND is_deleted=0""",
            (w['sfa_id'],))
        existing = _make_category_form(
            w['admin_id'], 'second', w['sfa_id'], 'phase1', status='open')
        errs = services.phase_rule_errors(
            'phase1', [w['sess']['sid']],
            category='second', second_assignment_id=w['sfa_id'])
        assert errs
        joined = ' '.join(errs)
        assert w['second_name'] in joined, (
            f'Error should name Second Faculty {w["second_name"]!r}, got: {joined!r}')
        # Main name should not appear unless they happen to be the same person
        if w['main_name'] != w['second_name']:
            assert w['main_name'] not in joined, (
                f'Error incorrectly names Main Faculty: {joined!r}')
        assert f'form #{existing}' in joined


def test_phase_rule_without_category_keeps_legacy_behaviour(app):
    """Calls without category still treat all categories as one pool (legacy)."""
    w = _seed_world(app)
    with app.app_context():
        sid = w['sess']['sid']
        db.execute(
            """UPDATE feedback_forms SET is_deleted=1
               WHERE session_id=? AND phase='phase1' AND is_deleted=0""", (sid,))
        _make_category_form(w['admin_id'], 'main', sid, 'phase1', status='open')
        # No category arg -> Main Phase 1 blocks any other Phase 1 on the session
        assert services.phase_rule_errors('phase1', [sid])


def test_publish_many_partial_success(app):
    """Bulk publish: valid drafts open; invalid ones fail without undoing successes."""
    w = _seed_world(app)
    with app.app_context():
        sid = w['sess']['sid']
        db.execute(
            """UPDATE feedback_forms SET is_deleted=1
               WHERE session_id=? AND is_deleted=0""", (sid,))
        admin = {'id': w['admin_id'], 'role': 'admin', 'name': 'Admin'}
        ok1 = _make_category_form(w['admin_id'], 'main', sid, 'phase1')
        ok2 = _make_category_form(w['admin_id'], 'lab', sid, 'phase1')
        # Second form without deadline will still pass phase coexistence but
        # validate_form_for_publish needs deadline for publish — set one.
        bad = _make_category_form(w['admin_id'], 'second', w['sfa_id'], 'phase1')
        db.execute("UPDATE feedback_forms SET deadline_at=NULL WHERE id=?", (bad,))
        # Ensure ok forms have deadline
        db.execute("UPDATE feedback_forms SET deadline_at=? WHERE id IN (?,?)",
                   (FUTURE, ok1, ok2))

        result = cf.publish_many(admin, [ok1, bad, ok2, 999999])
        pub_ids = {p['form_id'] for p in result['published']}
        fail_ids = {f['form_id'] for f in result['failed']}
        assert ok1 in pub_ids and ok2 in pub_ids, result
        assert bad in fail_ids or bad in {s['form_id'] for s in result['skipped']}, result
        assert 999999 in fail_ids
        # Successful opens stuck
        for fid in (ok1, ok2):
            st = db.query('SELECT status FROM feedback_forms WHERE id=?', (fid,), one=True)['status']
            assert st == 'open', fid
            assert db.query(
                'SELECT COUNT(*) c FROM form_eligible_students WHERE form_id=?',
                (fid,), one=True)['c'] > 0
