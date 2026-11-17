"""Final pass: the deferred hardening items and default-on for every creation path.

Concurrency tests release real threads together with a barrier; each thread has its own
app context and database connection.
"""
import logging
import threading
import uuid
from unittest import mock

import pytest

from feedback_system import authenticator as auth
from feedback_system import category_forms, create_app, db, hod, services
from helpers import find_session

ROLL = '25B11CS016'


def _questions():
    return [{'text': 'Teaching quality', 'qtype': 'rating', 'is_required': True,
             'options': [{'label': 'Excellent', 'value': 5}, {'label': 'Poor', 'value': 1}]}]


def _run_concurrently(app, n, work):
    barrier = threading.Barrier(n)
    results, errors = [None] * n, []

    def runner(i):
        try:
            barrier.wait(timeout=30)
            with app.app_context():
                results[i] = work(i)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return results, errors


@pytest.fixture()
def student_uid(app):
    with app.app_context():
        uid = db.query("SELECT id FROM users WHERE role='student' AND username=?",
                       (ROLL,), one=True)['id']

    def wipe():
        with app.app_context():
            db.execute('DELETE FROM student_backup_codes WHERE user_id=?', (uid,))
            db.execute('DELETE FROM student_authenticators WHERE user_id=?', (uid,))
            auth.clear_otp_failures(uid)

    wipe()
    yield uid
    wipe()


@pytest.fixture()
def made(app):
    bag = {'ids': [], 'titles': []}
    yield bag
    with app.app_context():
        ids = list(bag['ids'])
        for title in bag['titles']:
            ids += [r['id'] for r in db.query('SELECT id FROM feedback_forms WHERE title=?', (title,))]
        for fid in dict.fromkeys(ids):
            for q in db.query('SELECT id FROM form_questions WHERE form_id=?', (fid,)):
                db.execute('DELETE FROM question_options WHERE question_id=?', (q['id'],))
            db.execute('DELETE FROM form_questions WHERE form_id=?', (fid,))
            db.execute('DELETE FROM form_sessions WHERE form_id=?', (fid,))
            db.execute('DELETE FROM feedback_forms WHERE id=?', (fid,))


def _admin():
    return dict(db.query("SELECT * FROM users WHERE role='admin' LIMIT 1", one=True))


def _auth_of_title(app, title):
    with app.app_context():
        return [r['auth_required'] for r in db.query(
            'SELECT auth_required FROM feedback_forms WHERE title=?', (title,))]


# --------------------------------------------------------------------------- #
# Decode errors and key handling
# --------------------------------------------------------------------------- #
def test_non_ascii_stored_token_fails_closed(app):
    with app.app_context():
        with pytest.raises(auth.AuthenticatorConfigError):
            auth.decrypt_secret('ünïcödé-token')
        with pytest.raises(auth.AuthenticatorConfigError):
            auth.reencrypt_secret('ünïcödé-token')


def test_cipher_cache_is_keyed_by_digest_not_the_raw_key(app):
    with app.app_context():
        auth.encrypt_secret(auth.new_secret())
        raw = app.config['TOTP_ENCRYPTION_KEY']
        assert raw not in auth._CIPHER_CACHE
        assert all(len(k) == 64 and set(k) <= set('0123456789abcdef') for k in auth._CIPHER_CACHE)


def test_missing_key_logs_a_warning_without_any_key_value(tmp_path):
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = Capture()
    handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    feature_logger = logging.getLogger('feedback_system')
    feature_logger.addHandler(handler)
    try:
        create_app({'TESTING': True, 'AUTO_IMPORT': False, 'DATABASE_URL': None,
                    'DATABASE': str(tmp_path / 'startup.db'), 'SECRET_KEY': 'x' * 40,
                    'TOTP_ENCRYPTION_KEY': None})
    finally:
        feature_logger.removeHandler(handler)
    text = '\n'.join(records)
    assert 'TOTP_ENCRYPTION_KEY is not set' in text


# --------------------------------------------------------------------------- #
# Concurrent first-time enrollment
# --------------------------------------------------------------------------- #
def test_concurrent_first_time_enrollment_never_errors_and_stores_one_row(app, student_uid):
    results, errors = _run_concurrently(app, 8, lambda i: auth.begin_enrollment(student_uid))
    assert not errors, errors
    with app.app_context():
        rows = db.query('SELECT secret_enc FROM student_authenticators WHERE user_id=?',
                        (student_uid,))
        assert len(rows) == 1
        assert auth.decrypt_secret(rows[0]['secret_enc']) in results


def test_an_insert_conflict_is_retried_as_an_update(app, student_uid):
    """Deterministic version of the race: the first existence check is stale, so the
    INSERT hits the existing row, the transaction is rolled back, and the retry succeeds."""
    with app.app_context():
        auth.begin_enrollment(student_uid)
        real_update = auth._update_pending
        calls = {'n': 0}

        def stale_then_real(user_id, token, now):
            calls['n'] += 1
            if calls['n'] == 1:
                return False
            return real_update(user_id, token, now)

        with mock.patch.object(auth, '_update_pending', side_effect=stale_then_real):
            secret = auth.begin_enrollment(student_uid)
        assert calls['n'] == 2
        stored = db.query('SELECT secret_enc FROM student_authenticators WHERE user_id=?',
                          (student_uid,), one=True)['secret_enc']
        assert auth.decrypt_secret(stored) == secret


def test_refused_replacement_of_an_active_authenticator_is_unchanged(app, student_uid):
    import time
    import pyotp
    past = time.time() - 120
    with app.app_context():
        secret = auth.begin_enrollment(student_uid)
        assert auth.confirm_enrollment(student_uid, pyotp.TOTP(secret).at(past), now=past)
        with pytest.raises(auth.AuthenticatorStateError):
            auth.begin_enrollment(student_uid)
        assert auth.is_active(student_uid)


# --------------------------------------------------------------------------- #
# Default ON on every creation path
# --------------------------------------------------------------------------- #
def test_bulk_creation_without_a_choice_defaults_every_form_on(app, made):
    with app.app_context():
        sid = find_session(app, '6817', '2501IT07')
        s = db.query('SELECT timetable_id, teacher_user_id, subject_code FROM class_sessions '
                     'WHERE id=?', (sid,), one=True)
        title = f'Final bulk {uuid.uuid4().hex[:8]}'
        made['titles'].append(title)
        services.bulk_create_forms({'timetable_id': s['timetable_id'],
                                    'teacher_id': s['teacher_user_id'],
                                    'subject_code': s['subject_code']},
                                   _questions(), title, 'draft', _admin()['id'])
        flags = _auth_of_title(app, title)
        assert flags and all(f == 1 for f in flags)


def test_create_forms_for_session_defaults_every_form_on(app, made):
    with app.app_context():
        row = db.query('SELECT cs.id FROM class_sessions cs WHERE cs.is_deleted=0 AND NOT EXISTS ('
                       'SELECT 1 FROM feedback_forms f WHERE f.session_id=cs.id AND f.is_deleted=0) '
                       'ORDER BY cs.id LIMIT 1', one=True)
        created = [item['form_id'] for item in
                   category_forms.create_forms_for_session(_admin(), row['id'])
                   if item.get('form_id')]
        made['ids'].extend(created)
        assert created, 'no category form was created for a session with no forms'
        for fid in created:
            with app.app_context():
                assert db.query('SELECT auth_required FROM feedback_forms WHERE id=?',
                                (fid,), one=True)['auth_required'] == 1


def test_legacy_hod_save_defaults_the_form_on(app, made):
    with app.app_context():
        sid = find_session(app, '6817', '2501IT07')
        title = f'Final hod {uuid.uuid4().hex[:8]}'
        made['titles'].append(title)
        out = hod._save_hod_forms(_admin(), {'title': title, 'session_id': sid, 'scope': 'session',
                                             'deadline_at': '2030-01-31T23:59'},
                                  _questions(), [sid], None)
        made['ids'].append(out[0]['form_id'])
        assert _auth_of_title(app, title) == [1]
