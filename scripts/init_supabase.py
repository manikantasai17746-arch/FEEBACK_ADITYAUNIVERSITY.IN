"""One-off: create the Supabase schema and import the timetable workbook.

Usage (from the project root):

    set DATABASE_URL=postgresql://postgres.<ref>:<pw>@<host>:5432/postgres
    python scripts/init_supabase.py

Tips
----
* Use the **direct** connection (port **5432**) for this one-off import — it is
  much faster than the transaction pooler.  Use the **pooler** (port **6543**)
  as ``DATABASE_URL`` for the running app on Vercel.
* The script is safe to re-run: migrations are applied once and existing rows are
  preserved.  It only performs the workbook import when ``assignments`` is empty.
* It creates the admin account (from ``ADMIN_EMAIL`` / ``ADMIN_PASSWORD``) and
  then imports every branch, timetable, user, class session and eligibility row
  from ``III_semester_Student_timetable_detailed_report.xlsx``.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feedback_system import create_app, db  # noqa: E402
from feedback_system.import_data import bootstrap_if_empty, import_timetable  # noqa: E402


def _count(table):
    return db.query(f'SELECT COUNT(*) AS c FROM {table}', one=True)['c']


def main():
    url = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
    if not url:
        sys.exit('Set DATABASE_URL (or SUPABASE_DB_URL) to your Supabase connection string.')
    if not url.startswith(('postgres://', 'postgresql://')):
        sys.exit('DATABASE_URL must be a postgres:// connection string.')

    force = '--force' in sys.argv
    app = create_app({'DATABASE_URL': url, 'AUTO_IMPORT': False})

    with app.app_context():
        existing = _count('assignments')
        if existing and not force:
            print(f'assignments already has {existing} rows; running bootstrap only '
                  f'(pass --force to re-import the workbook).')
            bootstrap_if_empty(app)
        else:
            if force and existing:
                print('Re-importing the workbook (idempotent; only missing rows are added).')
            import_timetable(app)
        stats = {t: _count(t) for t in
                 ('users', 'branches', 'timetables', 'class_sessions',
                  'enrollments', 'session_students')}
    print('Supabase seed complete:', stats)


if __name__ == '__main__':
    main()
