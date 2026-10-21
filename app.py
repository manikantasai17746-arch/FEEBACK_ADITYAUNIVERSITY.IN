"""
Feedback System - project entry point.

Preserves the original launch contract:
    python app.py   ->  http://localhost:5000

The implementation lives in the ``feedback_system`` package so the code stays
organised, but the technology stack (Flask + SQLite + openpyxl + Jinja2) and the
existing database conventions are unchanged.
"""
from feedback_system import create_app

app = create_app()

if __name__ == '__main__':
    # Same defaults as the original single-file app.
    app.run(debug=False, host='0.0.0.0', port=5000)
