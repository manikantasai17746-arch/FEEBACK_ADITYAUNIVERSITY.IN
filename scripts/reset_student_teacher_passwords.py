"""Reset all student and teacher passwords to the configured defaults.

This replaces personal passwords for every student and teacher account in the
configured database. Verify DATABASE_URL/FEEDBACK_DB before running.

    python scripts/reset_student_teacher_passwords.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feedback_system import create_app, db  # noqa: E402
from feedback_system.import_data import reset_student_teacher_passwords  # noqa: E402


def main():
    app = create_app({'AUTO_IMPORT': False})
    with app.app_context():
        teacher_count, student_count = reset_student_teacher_passwords(
            db.get_db(),
            app.config.get('DEFAULT_TEACHER_PASSWORD'),
            app.config.get('DEFAULT_STUDENT_PASSWORD'))
    print(f'Reset passwords for {teacher_count} teacher and {student_count} student accounts.')
    print('Teachers will be required to change their password after login.')


if __name__ == '__main__':
    main()
