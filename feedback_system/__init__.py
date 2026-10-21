"""Application factory and configuration for the Faculty Feedback System."""
import os
import secrets

from flask import Flask, g, request, session, render_template

from . import db as db_module

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load secrets from a local ``.env`` file (never committed).  Real environment
# variables always win, so Vercel / server settings override the file.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, '.env'), override=False)
except ImportError:  # python-dotenv is optional at runtime
    pass


def is_production():
    """True on Vercel or when APP_ENV=production."""
    return bool(os.environ.get('VERCEL')) or \
        os.environ.get('APP_ENV', '').lower() == 'production'
DEFAULT_XLSX = os.path.join(BASE_DIR, 'III_semester_Student_timetable_detailed_report.xlsx')


def _load_or_create_secret_key(base_dir):
    """Persist a generated secret so sessions survive restarts.

    An explicit SECRET_KEY environment variable always wins (production).
    """
    env = os.environ.get('SECRET_KEY')
    if env:
        if is_production() and len(env) < 32:
            raise RuntimeError('SECRET_KEY must be at least 32 characters in production.')
        return env
    if is_production():
        # Never fall back to a generated key in production: sessions would
        # silently break and a file-based key could leak.
        raise RuntimeError('SECRET_KEY is not set. Add it to the environment variables.')
    instance = os.path.join(base_dir, 'instance')
    path = os.path.join(instance, 'secret_key')
    try:
        os.makedirs(instance, exist_ok=True)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as fh:
                value = fh.read().strip()
            if value:
                return value
        value = secrets.token_hex(32)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(value)
        return value
    except OSError:
        # Read-only filesystem (e.g. Vercel serverless): use an ephemeral key.
        # Set SECRET_KEY in the environment for production so sessions survive.
        return secrets.token_hex(32)


def create_app(config=None):
    app = Flask(__name__)

    app.config.update(
        # --- storage -------------------------------------------------------
        DATABASE=os.environ.get('FEEDBACK_DB', os.path.join(BASE_DIR, 'feedback.db')),
        # Supabase / PostgreSQL: set this to the pooled connection string and the
        # app uses Postgres instead of SQLite automatically.
        DATABASE_URL=(os.environ.get('DATABASE_URL')
                      or os.environ.get('SUPABASE_DB_URL')),
        XLSX_PATH=os.environ.get('FEEDBACK_XLSX', DEFAULT_XLSX),
        # --- security ------------------------------------------------------
        SECRET_KEY=_load_or_create_secret_key(BASE_DIR),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        # Secure cookies are on by default in production (HTTPS).
        SESSION_COOKIE_SECURE=os.environ.get(
            'SESSION_COOKIE_SECURE', '1' if is_production() else '0') == '1',
        # --- bootstrap credentials (only used when the DB has no admins) ----
        ADMIN_EMAIL=os.environ.get('ADMIN_EMAIL'),
        ADMIN_PASSWORD=os.environ.get('ADMIN_PASSWORD'),
        DEFAULT_TEACHER_PASSWORD=os.environ.get('DEFAULT_TEACHER_PASSWORD', 'teacher@123'),
        DEFAULT_STUDENT_PASSWORD=os.environ.get('DEFAULT_STUDENT_PASSWORD', 'student@123'),
        AUTO_IMPORT=os.environ.get('AUTO_IMPORT', '1') == '1',
        # --- misc ----------------------------------------------------------
        MAX_CONTENT_LENGTH=int(os.environ.get('MAX_UPLOAD_MB', '10')) * 1024 * 1024,
        # Student ID-card scan logs in directly (no password prompt).
        SCAN_LOGIN=os.environ.get('SCAN_LOGIN', '1') == '1',
        # ...but only while the student is still on the shared default password;
        # students with a personal password are asked for it after scanning.
        SCAN_LOGIN_ONLY_DEFAULT=os.environ.get('SCAN_LOGIN_ONLY_DEFAULT', '1') == '1',
        LOGIN_MAX_ATTEMPTS=int(os.environ.get('LOGIN_MAX_ATTEMPTS', '8')),
        LOGIN_LOCKOUT_SECONDS=int(os.environ.get('LOGIN_LOCKOUT_SECONDS', '300')),
        JSON_SORT_KEYS=False,
        # Let browsers cache CSS/JS/logo so they are not re-fetched (each fetch
        # is a serverless call) on every page.
        SEND_FILE_MAX_AGE_DEFAULT=int(os.environ.get('STATIC_MAX_AGE', '86400')),
    )

    if config:
        app.config.update(config)

    # Templates/static live inside the package.
    app.template_folder = os.path.join(os.path.dirname(__file__), 'templates')
    app.static_folder = os.path.join(os.path.dirname(__file__), 'static')

    db_module.init_app(app)

    from .security import init_csrf
    init_csrf(app)

    # Blueprints
    from .auth import bp as auth_bp
    from .admin import bp as admin_bp
    from .hod import bp as hod_bp
    from .dean import bp as dean_bp
    from .student import bp as student_bp
    from .teacher import bp as teacher_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(hod_bp)
    app.register_blueprint(dean_bp)

    _register_jinja(app)
    _register_error_handlers(app)
    _register_security_headers(app)

    # Ensure schema exists and, on a brand-new database, import the timetable.
    with app.app_context():
        db_module.run_migrations(db_module.database_target(app))
        if app.config.get('AUTO_IMPORT'):
            from .import_data import bootstrap_if_empty
            bootstrap_if_empty(app)

    return app


def _register_security_headers(app):
    @app.after_request
    def _headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'DENY')
        resp.headers.setdefault('Referrer-Policy', 'same-origin')
        resp.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=()')
        if is_production():
            resp.headers.setdefault('Strict-Transport-Security',
                                    'max-age=31536000; includeSubDomains')
        if request.endpoint and request.endpoint.startswith('admin.') or \
                request.endpoint in ('auth.change_password',):
            resp.headers.setdefault('Cache-Control', 'no-store')
        return resp


def _register_jinja(app):
    from .security import csrf_token

    @app.context_processor
    def inject_globals():
        from .auth import current_user
        return {
            'csrf_token': csrf_token,
            'current_user': current_user(),
            'status_labels': {
                'draft': 'Draft',
                'open': 'Published / Open',
                'closed': 'Closed',
                'archived': 'Archived',
            },
        }

    @app.template_filter('pct')
    def pct(value, total):
        if not total:
            return 0
        return round(value * 100.0 / total, 1)


def _register_error_handlers(app):
    @app.errorhandler(403)
    def forbidden(_e):
        from .auth import current_user
        return render_template('error.html', code=403,
                               message='You are not authorised to view this page.',
                               user=current_user()), 403

    @app.errorhandler(404)
    def not_found(_e):
        from .auth import current_user
        return render_template('error.html', code=404,
                               message='The page you requested was not found.',
                               user=current_user()), 404

    @app.errorhandler(500)
    def server_error(_e):
        from .auth import current_user
        return render_template('error.html', code=500,
                               message='Something went wrong. Please try again.',
                               user=current_user()), 500
