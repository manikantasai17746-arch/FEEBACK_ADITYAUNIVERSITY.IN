"""Student authenticator (TOTP) core - Checkpoint 1.

Covers encryption at rest and key rotation, RFC 6238 test vectors, time drift,
replay prevention, enrollment, single-use backup codes, rate limiting, the v25
migration (existing forms stay OFF; PostgreSQL translation), and the rule that
new forms default to Authentication Required = ON.

Each test that touches student authenticator state uses a dedicated cleanup
fixture, so the shared session database stays clean for the rest of the suite.
"""
import re
import sqlite3
import time

import pyotp
import pytest
from cryptography.fernet import Fernet

from feedback_system import authenticator as auth
from feedback_system import db
from feedback_system import db as db_module
from feedback_system import pg, services
from feedback_system import schema
from feedback_system.security import _db_key
from feedback_system.services import AuthSettingLocked

STUDENT_ROLL = '25B11CS015'
# RFC 6238 Appendix B test secret (SHA-1), ASCII "12345678901234567890" in base32.
RFC_SECRET = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _wipe_student_auth(app, uid):
    with app.app_context():
        db.execute('DELETE FROM student_backup_codes WHERE user_id=?', (uid,))
        db.execute('DELETE FROM student_authenticators WHERE user_id=?', (uid,))
        auth.clear_otp_failures(uid)


@pytest.fixture()
def student_uid(app):
    with app.app_context():
        uid = db.query("SELECT id FROM users WHERE role='student' AND username=?",
                       (STUDENT_ROLL,), one=True)['id']
    _wipe_student_auth(app, uid)
    yield uid
    _wipe_student_auth(app, uid)


@pytest.fixture()
def made_forms(app):
    """Track forms a test creates, and delete them afterwards."""
    ids = []
    yield ids
    with app.app_context():
        for fid in ids:
            for q in db.query('SELECT id FROM form_questions WHERE form_id=?', (fid,)):
                db.execute('DELETE FROM question_options WHERE question_id=?', (q['id'],))
            db.execute('DELETE FROM form_questions WHERE form_id=?', (fid,))
            db.execute('DELETE FROM form_sessions WHERE form_id=?', (fid,))
            db.execute('DELETE FROM feedback_forms WHERE id=?', (fid,))


@pytest.fixture()
def key_override(app):
    """Temporarily change a config value and restore it afterwards."""
    saved = {}

    def set_(name, value):
        saved.setdefault(name, app.config.get(name))
        app.config[name] = value

    yield set_
    for name, value in saved.items():
        app.config[name] = value


def _enroll(app, uid):
    with app.app_context():
        secret = auth.begin_enrollment(uid)
        assert auth.confirm_enrollment(uid, pyotp.TOTP(secret).now())
    return secret


def _questions():
    return [{'text': 'Teaching quality', 'qtype': 'rating', 'is_required': True,
             'options': [{'label': 'Excellent', 'value': 5}, {'label': 'Poor', 'value': 1}]}]


def _admin_id():
    return db.query("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)['id']


def _session_id():
    return db.query('SELECT id FROM class_sessions WHERE is_deleted=0 ORDER BY id LIMIT 1',
                    one=True)['id']


def _new_form(made_forms, title, **extra):
    payload = {'title': title, 'session_id': _session_id(), 'scope': 'session', **extra}
    fid = services.save_form(payload, _questions(), _admin_id(), session_ids=[payload['session_id']])
    made_forms.append(fid)
    return fid


def _auth_flag(fid):
    return db.query('SELECT auth_required FROM feedback_forms WHERE id=?', (fid,),
                    one=True)['auth_required']


# --------------------------------------------------------------------------- #
# Encryption at rest and key rotation
# --------------------------------------------------------------------------- #
def test_encrypt_roundtrip_and_ciphertext_hides_secret(app):
    with app.app_context():
        secret = auth.new_secret()
        token = auth.encrypt_secret(secret)
        assert secret not in token
        assert auth.decrypt_secret(token) == secret
        assert auth.encrypt_secret(secret) != token          # fresh nonce each time


def test_tampered_ciphertext_fails_closed(app):
    with app.app_context():
        token = auth.encrypt_secret(auth.new_secret())
        tampered = token[:-4] + ('AAAA' if token[-4:] != 'AAAA' else 'BBBB')
        with pytest.raises(auth.AuthenticatorConfigError):
            auth.decrypt_secret(tampered)


def test_missing_key_fails_closed(app, key_override):
    key_override('TOTP_ENCRYPTION_KEY', None)
    with app.app_context():
        with pytest.raises(auth.AuthenticatorConfigError):
            auth.encrypt_secret(auth.new_secret())


def test_malformed_key_fails_closed_and_message_does_not_echo_key(app, key_override):
    key_override('TOTP_ENCRYPTION_KEY', 'not-a-real-key-VALUE123')
    with app.app_context():
        with pytest.raises(auth.AuthenticatorConfigError) as exc:
            auth.encrypt_secret(auth.new_secret())
    assert 'not-a-real-key' not in str(exc.value)


def test_wrong_key_cannot_decrypt(app, key_override):
    with app.app_context():
        token = auth.encrypt_secret(auth.new_secret())
    key_override('TOTP_ENCRYPTION_KEY', Fernet.generate_key().decode())
    with app.app_context():
        with pytest.raises(auth.AuthenticatorConfigError):
            auth.decrypt_secret(token)


def test_key_rotation_reads_old_tokens_and_reencrypts_under_new_key(app, key_override):
    old_key = app.config['TOTP_ENCRYPTION_KEY']
    new_key = Fernet.generate_key().decode()
    with app.app_context():
        secret = auth.new_secret()
        old_token = auth.encrypt_secret(secret)
    # Rotation window: newest key first, old key still accepted for reads.
    key_override('TOTP_ENCRYPTION_KEY', f'{new_key},{old_key}')
    with app.app_context():
        assert auth.decrypt_secret(old_token) == secret
        new_token = auth.reencrypt_secret(old_token)
    # After rotation: only the new key remains.
    key_override('TOTP_ENCRYPTION_KEY', new_key)
    with app.app_context():
        assert auth.decrypt_secret(new_token) == secret
        with pytest.raises(auth.AuthenticatorConfigError):
            auth.decrypt_secret(old_token)


def test_secret_key_rotation_does_not_break_totp_decryption(app, key_override):
    with app.app_context():
        secret = auth.new_secret()
        token = auth.encrypt_secret(secret)
    key_override('SECRET_KEY', 'a-completely-different-secret-key-value-0123456789')
    with app.app_context():
        assert auth.decrypt_secret(token) == secret


# --------------------------------------------------------------------------- #
# RFC 6238 vectors, drift window, replay
# --------------------------------------------------------------------------- #
def test_rfc6238_vector_t59_sha1_six_digits():
    # RFC 6238 Appendix B: T=59 gives 94287082 (8 digits), so 287082 at 6 digits.
    assert auth.verify_totp(RFC_SECRET, '287082', now=59) == 1


def test_rfc6238_vector_t1111111109_sha1_six_digits():
    # RFC 6238 Appendix B: T=1111111109 gives 07081804 (8 digits), so 081804 at 6 digits.
    assert auth.verify_totp(RFC_SECRET, '081804', now=1111111109) == 37037036


@pytest.mark.parametrize('delta, accepted', [
    (0, True),      # current step
    (-30, True),    # previous step (clock drift behind)
    (30, True),     # next step (clock drift ahead)
    (-60, False),   # two steps behind: outside the window
    (60, False),    # two steps ahead: outside the window
])
def test_time_drift_window_is_one_step_each_way(delta, accepted):
    now = 1_700_000_010
    code = pyotp.TOTP(RFC_SECRET).at(now + delta)
    matched = auth.verify_totp(RFC_SECRET, code, now=now)
    assert (matched is not None) == accepted


def test_replayed_step_is_rejected_but_later_step_is_accepted():
    now = 1_700_000_010
    code = pyotp.TOTP(RFC_SECRET).at(now)
    step = auth.verify_totp(RFC_SECRET, code, now=now)
    assert step is not None
    assert auth.verify_totp(RFC_SECRET, code, last_step=step, now=now) is None
    assert auth.verify_totp(RFC_SECRET, code, last_step=step - 1, now=now) == step


@pytest.mark.parametrize('bad', ['12345', '1234567', 'abcdef', ' 287082', '', None, 287082])
def test_malformed_codes_are_rejected(bad):
    assert auth.verify_totp(RFC_SECRET, bad, now=59) is None


def test_wrong_code_is_rejected():
    assert auth.verify_totp(RFC_SECRET, '000000', now=59) is None


# --------------------------------------------------------------------------- #
# Enrollment state (DB)
# --------------------------------------------------------------------------- #
def test_begin_enrollment_stores_only_ciphertext_and_is_pending(app, student_uid):
    with app.app_context():
        secret = auth.begin_enrollment(student_uid)
        row = db.query('SELECT secret_enc, status FROM student_authenticators WHERE user_id=?',
                       (student_uid,), one=True)
        assert row['status'] == 'pending'
        assert secret not in row['secret_enc']
        assert auth.decrypt_secret(row['secret_enc']) == secret
        assert not auth.is_active(student_uid)


def test_confirm_with_wrong_code_keeps_pending(app, student_uid):
    with app.app_context():
        auth.begin_enrollment(student_uid)
        assert not auth.confirm_enrollment(student_uid, '000000')
        assert not auth.is_active(student_uid)


def test_confirm_activates_and_the_same_code_cannot_be_replayed(app, student_uid):
    with app.app_context():
        secret = auth.begin_enrollment(student_uid)
        code = pyotp.TOTP(secret).now()
        assert auth.confirm_enrollment(student_uid, code)
        assert auth.is_active(student_uid)
        # The confirming step is consumed, so the same code cannot pass at login.
        assert auth.verify_second_factor(student_uid, code) == (False, 'invalid')


def test_begin_enrollment_refuses_to_replace_an_active_authenticator(app, student_uid):
    _enroll(app, student_uid)
    with app.app_context():
        with pytest.raises(auth.AuthenticatorStateError):
            auth.begin_enrollment(student_uid)


def test_student_without_authenticator_is_not_enrolled(app, student_uid):
    with app.app_context():
        assert auth.verify_second_factor(student_uid, '123456') == (False, 'not_enrolled')


def test_verify_second_factor_accepts_once_then_rejects_replay(app, student_uid):
    _enroll(app, student_uid)
    later = time.time() + 120            # several steps after the enrollment step
    with app.app_context():
        code = pyotp.TOTP(db_secret(app, student_uid)).at(later)
        assert auth.verify_second_factor(student_uid, code, now=later) == (True, 'totp')
        assert auth.verify_second_factor(student_uid, code, now=later) == (False, 'invalid')


def db_secret(app, uid):
    """Test helper: recover the plaintext secret for an enrolled student (test-only)."""
    row = db.query('SELECT secret_enc FROM student_authenticators WHERE user_id=?',
                   (uid,), one=True)
    return auth.decrypt_secret(row['secret_enc'])


def test_consume_step_is_atomic_and_monotonic(app, student_uid):
    _enroll(app, student_uid)
    with app.app_context():
        base = int(time.time()) // 30 + 1000
        assert auth.consume_step(student_uid, base + 10)
        assert not auth.consume_step(student_uid, base + 10)   # same step twice
        assert not auth.consume_step(student_uid, base + 5)    # older step
        assert auth.consume_step(student_uid, base + 11)       # newer step


# --------------------------------------------------------------------------- #
# Backup codes
# --------------------------------------------------------------------------- #
def test_backup_codes_have_expected_shape_and_are_hashed_at_rest(app, student_uid):
    codes = auth.generate_backup_codes()
    assert len(codes) == auth.BACKUP_CODE_COUNT
    shape = re.compile(r'^[abcdefghjkmnpqrstuvwxyz23456789]{4}-[abcdefghjkmnpqrstuvwxyz23456789]{4}'
                       r'-[abcdefghjkmnpqrstuvwxyz23456789]{4}$')
    assert all(shape.match(c) for c in codes)
    assert len(set(codes)) == len(codes)
    with app.app_context():
        auth.store_backup_codes(student_uid, codes)
        rows = db.query('SELECT code_hash FROM student_backup_codes WHERE user_id=?',
                        (student_uid,))
        assert len(rows) == auth.BACKUP_CODE_COUNT
        stored = ' '.join(r['code_hash'] for r in rows)
        for c in codes:
            assert c not in stored and c.replace('-', '') not in stored


def test_backup_code_is_single_use_and_normalised(app, student_uid):
    codes = auth.generate_backup_codes()
    with app.app_context():
        auth.store_backup_codes(student_uid, codes)
        # Accepts the code typed in lower case, without dashes or with spaces.
        assert auth.consume_backup_code(student_uid, codes[0].upper().replace('-', ' '))
        assert not auth.consume_backup_code(student_uid, codes[0])          # already used
        assert auth.consume_backup_code(student_uid, codes[1])              # others still work
        assert not auth.consume_backup_code(student_uid, 'zzzz-zzzz-zzzz')  # wrong code


def test_storing_new_backup_codes_invalidates_the_old_ones(app, student_uid):
    old, new = auth.generate_backup_codes(), auth.generate_backup_codes()
    with app.app_context():
        auth.store_backup_codes(student_uid, old)
        auth.store_backup_codes(student_uid, new)
        assert not auth.consume_backup_code(student_uid, old[0])
        assert auth.consume_backup_code(student_uid, new[0])


def test_backup_code_works_through_second_factor_check(app, student_uid):
    _enroll(app, student_uid)
    codes = auth.generate_backup_codes()
    with app.app_context():
        auth.store_backup_codes(student_uid, codes)
        assert auth.verify_second_factor(student_uid, codes[2]) == (True, 'backup')
        assert auth.verify_second_factor(student_uid, codes[2]) == (False, 'invalid')


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #
def test_repeated_failures_lock_the_account_and_success_clears_the_lock(
        app, student_uid, key_override):
    key_override('OTP_MAX_ATTEMPTS', 3)
    _enroll(app, student_uid)
    later = time.time() + 120
    with app.app_context():
        for _ in range(3):
            assert auth.verify_second_factor(student_uid, '000000', now=later) == (False, 'invalid')
        assert auth.otp_locked_seconds(student_uid) > 0
        good = pyotp.TOTP(db_secret(app, student_uid)).at(later)
        # Even the correct code is refused while locked.
        assert auth.verify_second_factor(student_uid, good, now=later) == (False, 'locked')
        auth.clear_otp_failures(student_uid)
        assert auth.otp_locked_seconds(student_uid) == 0
        assert auth.verify_second_factor(student_uid, good, now=later) == (True, 'totp')


def test_failures_are_counted_in_the_shared_login_attempts_table(app, student_uid):
    _enroll(app, student_uid)
    with app.app_context():
        auth.verify_second_factor(student_uid, '000000', now=time.time() + 120)
        row = db.query('SELECT fail_count FROM login_attempts WHERE attempt_key=?',
                       (_db_key(f'otp:{student_uid}'),), one=True)
        assert row is not None and row['fail_count'] >= 1


# --------------------------------------------------------------------------- #
# Migration v25
# --------------------------------------------------------------------------- #
def _v25():
    return [stmts for version, _d, stmts in schema.MIGRATIONS if version == 25][0]


def test_v25_is_registered_and_additive():
    assert schema.MIGRATIONS[-1][0] == 25
    for stmt in _v25():
        assert not re.search(r'\b(DROP|DELETE|RENAME|TRUNCATE)\b', stmt, re.I), stmt
    # The only ALTER is an ADD COLUMN; nothing else touches existing schema.
    for stmt in _v25():
        if re.match(r'\s*ALTER', stmt, re.I):
            assert re.search(r'ADD COLUMN', stmt, re.I), stmt


def test_v25_translates_cleanly_to_postgres():
    stmts = _v25()
    assert stmts
    for stmt in stmts:
        out = pg.to_postgres(stmt)
        assert 'AUTOINCREMENT' not in out and 'BLOB' not in out
    pgsql = ' '.join(pg.to_postgres(s) for s in stmts)
    assert 'ADD COLUMN IF NOT EXISTS auth_required' in pgsql
    assert 'GENERATED BY DEFAULT AS IDENTITY' in pgsql      # backup-code table id


def test_v25_preserves_existing_forms_and_defaults_them_off(tmp_path, monkeypatch):
    """A populated pre-v25 database keeps every form, and each one is OFF after the upgrade."""
    full = list(db_module.MIGRATIONS)
    path = str(tmp_path / 'legacy.db')

    monkeypatch.setattr(db_module, 'MIGRATIONS', [m for m in full if m[0] <= 24])
    db_module.run_migrations(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO feedback_forms(title, status) VALUES('Legacy open form','open')")
    conn.execute("INSERT INTO feedback_forms(title, status) VALUES('Legacy closed form','closed')")
    conn.commit()
    before = conn.execute('SELECT COUNT(*) FROM feedback_forms').fetchone()[0]
    conn.close()

    monkeypatch.setattr(db_module, 'MIGRATIONS', full)
    db_module.run_migrations(path)

    conn = sqlite3.connect(path)
    try:
        assert conn.execute('SELECT COUNT(*) FROM feedback_forms').fetchone()[0] == before
        flags = [r[0] for r in conn.execute('SELECT auth_required FROM feedback_forms')]
        assert flags and all(f == 0 for f in flags)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'student_authenticators', 'student_backup_codes'} <= tables
        version = conn.execute('SELECT MAX(version) FROM feedback_system_schema_migrations').fetchone()[0]
        assert version == 25
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Authentication Required on forms: default ON for new, OFF for existing
# --------------------------------------------------------------------------- #
def test_new_form_defaults_to_authentication_required_on(app, made_forms):
    with app.app_context():
        fid = _new_form(made_forms, 'CP1 default on')
        assert _auth_flag(fid) == 1


def test_explicit_off_on_create_is_respected(app, made_forms):
    with app.app_context():
        off = _new_form(made_forms, 'CP1 explicit off', auth_required=False)
        on = _new_form(made_forms, 'CP1 explicit on', auth_required='1')
        assert _auth_flag(off) == 0
        assert _auth_flag(on) == 1


@pytest.mark.parametrize('bad', ['maybe', '2', 'yes please'])
def test_unrecognised_value_is_rejected_not_silently_turned_off(app, made_forms, bad):
    with app.app_context():
        with pytest.raises(ValueError):
            _new_form(made_forms, f'CP1 bad {bad}', auth_required=bad)


def test_draft_setting_can_change_and_is_untouched_when_not_mentioned(app, made_forms):
    with app.app_context():
        fid = _new_form(made_forms, 'CP1 draft edit')
        services.save_form({'title': 'CP1 draft edit', 'auth_required': '0', 'session_id': _session_id(),
                            'scope': 'session'}, _questions(), _admin_id(), form_id=fid,
                           session_ids=[_session_id()])
        assert _auth_flag(fid) == 0
        services.save_form({'title': 'CP1 draft edit renamed', 'session_id': _session_id(),
                            'scope': 'session'}, _questions(), _admin_id(), form_id=fid,
                           session_ids=[_session_id()])
        assert _auth_flag(fid) == 0          # not mentioned, so not changed


def test_setting_is_locked_once_the_form_is_published(app, made_forms):
    with app.app_context():
        fid = _new_form(made_forms, 'CP1 locked')
        db.execute("UPDATE feedback_forms SET status='open' WHERE id=?", (fid,))
        with pytest.raises(AuthSettingLocked):
            services.save_form({'title': 'CP1 locked', 'auth_required': '0',
                                'session_id': _session_id(), 'scope': 'session'},
                               _questions(), _admin_id(), form_id=fid,
                               session_ids=[_session_id()])
        assert _auth_flag(fid) == 1
        # Re-submitting the same value is not a change, so it is allowed.
        services.save_form({'title': 'CP1 locked', 'auth_required': '1',
                            'session_id': _session_id(), 'scope': 'session'},
                           _questions(), _admin_id(), form_id=fid, session_ids=[_session_id()])
        assert _auth_flag(fid) == 1


def test_existing_form_stays_off_when_edited(app, made_forms):
    with app.app_context():
        # A form that existed before v25 (backfilled to 0 by the migration).
        fid = _new_form(made_forms, 'CP1 legacy', auth_required=False)
        services.save_form({'title': 'CP1 legacy edited', 'session_id': _session_id(),
                            'scope': 'session'}, _questions(), _admin_id(), form_id=fid,
                           session_ids=[_session_id()])
        assert _auth_flag(fid) == 0
