"""Remove the fake "combined" teacher accounts created by an old timetable import.

An earlier import read a Second Emp.Code cell such as ``6470;6705`` (several
people separated by ``;``) as ONE teacher, creating an account whose employee
code is ``6470;6705`` and whose name is ``V V SUBRAHMANYA SARMA M;AALAMURI ...``.
This script finds those accounts and deletes them.

    python scripts/remove_combined_teacher_accounts.py            # preview only
    python scripts/remove_combined_teacher_accounts.py --apply    # really delete

Point it at the right database first (same as the app): DATABASE_URL for
Supabase/PostgreSQL, otherwise FEEDBACK_DB / local SQLite.

Safety rules:
* The default run only PREVIEWS; nothing changes without --apply.
* Only teacher accounts whose employee code contains ';' are touched.
* An account is SKIPPED (and reported) if it is used for anything other than
  being a Second Faculty on an import-created assignment: for example it owns
  a class session, a lab assignment, a question set or an action, or a feedback
  form / submission already exists for its assignment.
* Everything is deleted in one transaction; any error rolls it all back.

After cleaning, import the same college workbook again with the fixed importer.
Re-importing never overwrites existing data, and it creates the real Second
Faculty assignments for each person (6470, 6705, 6462, ...).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feedback_system import create_app, db  # noqa: E402


def find_combined(conn):
    return conn.execute(
        "SELECT id, username, name, emp_code FROM users "
        "WHERE role='teacher' AND (emp_code LIKE '%;%' OR username LIKE '%;%') "
        "ORDER BY emp_code").fetchall()


# (what it means, SQL with ONE ? for the account id)
_BLOCKERS = [
    ("teaches a class session",
     "SELECT COUNT(*) FROM class_sessions WHERE teacher_user_id=?"),
    ("has teacher subject settings",
     "SELECT COUNT(*) FROM teacher_subject_settings WHERE teacher_user_id=?"),
    ("is the main teacher of a Second Faculty assignment",
     "SELECT COUNT(*) FROM second_faculty_assignments WHERE main_teacher_user_id=?"),
    ("is the main teacher of a lab faculty assignment",
     "SELECT COUNT(*) FROM lab_faculty_assignments WHERE main_teacher_user_id=?"),
    ("is the lab teacher of a lab faculty assignment",
     "SELECT COUNT(*) FROM lab_faculty_assignments WHERE lab_teacher_user_id=?"),
    ("owns a question set",
     "SELECT COUNT(*) FROM question_sets WHERE owner_user_id=?"),
    ("owns an improvement action",
     "SELECT COUNT(*) FROM improvement_actions WHERE owner_user_id=?"),
    ("already has a feedback form on its Second Faculty assignment",
     "SELECT COUNT(*) FROM feedback_forms WHERE second_faculty_assignment_id IN "
     "(SELECT id FROM second_faculty_assignments WHERE second_teacher_user_id=?)"),
]


def blockers(conn, uid):
    """Reasons this account must NOT be deleted (empty list = safe)."""
    out = []
    for label, sql in _BLOCKERS:
        n = conn.execute(sql, (uid,)).fetchone()[0]
        if n:
            out.append(f'{label} ({n})')
    return out


def clean(conn, apply=False, log=print):
    """Preview or delete. Returns (deleted_accounts, skipped_accounts)."""
    accounts = find_combined(conn)
    if not accounts:
        log('No combined teacher accounts found. Nothing to do.')
        return [], []
    deleted, skipped = [], []
    for a in accounts:
        why = blockers(conn, a['id'])
        sfa = conn.execute(
            "SELECT COUNT(*) FROM second_faculty_assignments "
            "WHERE second_teacher_user_id=?", (a['id'],)).fetchone()[0]
        if why:
            skipped.append(a)
            log(f"SKIP   {a['emp_code']!r} ({a['name']}): " + '; '.join(why))
        else:
            deleted.append(a)
            log(f"REMOVE {a['emp_code']!r} ({a['name']}) "
                f"- {sfa} second-faculty assignment(s) to delete")
    if not apply:
        log(f'\nPREVIEW ONLY: {len(deleted)} account(s) would be removed, '
            f'{len(skipped)} skipped. Run again with --apply to delete.')
        return deleted, skipped

    try:
        for a in deleted:
            uid, code = a['id'], a['emp_code']
            conn.execute("DELETE FROM second_faculty_assignments "
                         "WHERE second_teacher_user_id=?", (uid,))
            conn.execute("UPDATE second_faculty_import_staging "
                         "SET second_teacher_user_id=NULL "
                         "WHERE second_teacher_user_id=?", (uid,))
            conn.execute("DELETE FROM employee_photos WHERE emp_code=?", (code,))
            for tbl in ('email_codes', 'notifications', 'user_department_scopes',
                        'student_authenticators', 'student_backup_codes'):
                conn.execute(f"DELETE FROM {tbl} WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM users WHERE id=? AND role='teacher'", (uid,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    log(f'\nDONE: removed {len(deleted)} account(s); {len(skipped)} skipped.')
    return deleted, skipped


def main():
    apply = '--apply' in sys.argv[1:]
    app = create_app({'AUTO_IMPORT': False})
    with app.app_context():
        backend = ('PostgreSQL' if db.using_postgres(app)
                   else f'SQLite ({db.database_target(app)})')
        print(f'Database: {backend}')
        print('Mode: ' + ('APPLY (deleting)' if apply else 'preview only') + '\n')
        clean(db.get_db(), apply=apply)


if __name__ == '__main__':
    main()
