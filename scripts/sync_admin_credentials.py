"""Create or reset the first admin login from environment credentials.

Run from the project root after confirming DATABASE_URL / FEEDBACK_DB point
at the intended database:

    # required
    export ADMIN_EMAIL='admin@college.edu'
    export ADMIN_PASSWORD='your-strong-password'
    # optional: DATABASE_URL for Supabase, otherwise local SQLite is used
    python scripts/sync_admin_credentials.py

This script:
* creates the admin account when none exists, or
* updates username, email, and password_hash of the first admin row.

It never prints the password. Safe to re-run.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feedback_system import create_app, db  # noqa: E402
from feedback_system.import_data import set_admin_credentials  # noqa: E402


def main():
    email = os.environ.get('ADMIN_EMAIL')
    password = os.environ.get('ADMIN_PASSWORD')
    if not email or not password:
        print('ERROR: Set ADMIN_EMAIL and ADMIN_PASSWORD in the environment.',
              file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print('ERROR: ADMIN_PASSWORD must be at least 8 characters.',
              file=sys.stderr)
        sys.exit(1)

    app = create_app({'AUTO_IMPORT': False})
    with app.app_context():
        target = db.database_target(app)
        backend = 'PostgreSQL' if db.using_postgres(app) else f'SQLite ({target})'
        admin_id = set_admin_credentials(db.get_db(), email, password)
        row = db.query(
            "SELECT id, username, email, is_active, role FROM users WHERE id=?",
            (admin_id,), one=True)
    print(f'Admin credentials updated on {backend}.')
    print(f"  id={row['id']} username={row['username']} "
          f"email={row['email']} active={row['is_active']}")
    print('Sign in at /admin/login with ADMIN_EMAIL and ADMIN_PASSWORD.')
    print('(Password is not displayed.)')


if __name__ == '__main__':
    main()
