"""Database helpers and the migration runner.

Two backends are supported transparently:

* **SQLite** (the default, used for local development and the test suite) — the
  database file configured by ``DATABASE`` / ``FEEDBACK_DB``.
* **PostgreSQL / Supabase** — selected automatically when ``DATABASE_URL`` (or
  ``SUPABASE_DB_URL``) is set to a ``postgres://`` connection string.  The
  adapter in :mod:`feedback_system.pg` keeps the sqlite3 ergonomics, so the rest
  of the code is backend-agnostic.
"""
import os
import sqlite3

from flask import current_app, g

from . import pg
from .schema import MIGRATIONS


# --------------------------------------------------------------------------- #
# Backend selection
# --------------------------------------------------------------------------- #
def database_target(app=None):
    """The configured target: a Postgres URL or a SQLite file path."""
    app = app or current_app
    return app.config.get('DATABASE_URL') or app.config['DATABASE']


def using_postgres(app=None):
    return pg.is_postgres_url(database_target(app))


# --------------------------------------------------------------------------- #
# Connection handling (request scoped, same pattern as the original app)
# --------------------------------------------------------------------------- #
def get_db():
    if 'db' not in g:
        target = database_target()
        if pg.is_postgres_url(target):
            g.db = pg.shared_connection(target)
        else:
            g.db = sqlite3.connect(target)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def close_db(_exc=None):
    db = g.pop('db', None)
    if db is not None:
        if isinstance(db, pg.Connection):
            pg.release(db)        # keep the Postgres link open for the next request
        else:
            db.close()


def init_app(app):
    app.teardown_appcontext(close_db)


# --------------------------------------------------------------------------- #
# Small query helpers
# --------------------------------------------------------------------------- #
def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur


def executemany(sql, seq):
    db = get_db()
    cur = db.executemany(sql, seq)
    db.commit()
    return cur


def insert_id(sql, args=()):
    """Run an INSERT and return the id of the new row (SQLite and Postgres).

    Does not commit; the caller commits as part of its own transaction.
    """
    conn = get_db()
    if using_postgres():
        row = conn.execute(pg.with_returning(sql, 'id'), args).fetchone()
        return row[0] if row is not None else None
    return conn.execute(sql, args).lastrowid


# Kept for backward compatibility with earlier code / callers.
def execute_returning_id(sql, args=()):
    new_id = insert_id(sql, args)
    get_db().commit()
    return new_id


# --------------------------------------------------------------------------- #
# Migrations
# --------------------------------------------------------------------------- #
_PG_MIGRATION_LOCK = 0x5F0BAC  # arbitrary, stable advisory-lock key


def current_version(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback_system_schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    row = conn.execute(
        "SELECT MAX(version) FROM feedback_system_schema_migrations"
    ).fetchone()
    return (row[0] or 0) if row else 0



def _apply_statement(conn, stmt):
    """Execute one statement; tolerate an idempotent duplicate table/column."""
    try:
        conn.execute(stmt)
    except Exception as exc:  # pragma: no cover - defensive
        msg = str(exc).lower()
        if ('duplicate column' in msg or 'already exists' in msg
                or 'duplicate_column' in msg or 'duplicate_table' in msg
                or 'duplicateobject' in msg or 'duplicate object' in msg):
            return
        raise


def _expand_user_roles(conn, is_pg):
    """Safely expand users.role CHECK to include 'hod' and 'dean'.

    Existing rows are preserved.  SQLite requires a table rebuild; PostgreSQL
    uses DROP/ADD CONSTRAINT.  Failures that indicate the constraint is already
    expanded are tolerated.
    """
    allowed = "('admin','teacher','student','hod','dean')"
    if is_pg:
        try:
            # Discover the existing constraint name (Postgres auto-names it).
            row = conn.execute("""
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'users'::regclass AND contype = 'c'
                  AND pg_get_constraintdef(oid) ILIKE '%role%'
            """).fetchone()
            if row:
                cname = row[0] if not isinstance(row, dict) else row['conname']
                conn.execute(f'ALTER TABLE users DROP CONSTRAINT IF EXISTS {cname}')
            conn.execute(
                f"ALTER TABLE users ADD CONSTRAINT users_role_check "
                f"CHECK (role IN {allowed})"
            )
        except Exception as exc:
            msg = str(exc).lower()
            if 'already exists' in msg or 'duplicate' in msg:
                return
            # Constraint may already allow the new roles.
            if 'check' in msg and 'role' in msg:
                return
            raise
        return

    # SQLite: rebuild the users table with the expanded CHECK.
    # Only run when the current DDL still has the old three-role CHECK.
    try:
        ddl_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        ddl = (ddl_row[0] if ddl_row else '') or ''
        if "'hod'" in ddl and "'dean'" in ddl:
            return  # already expanded
        conn.execute('PRAGMA foreign_keys = OFF')
        conn.execute("""
            CREATE TABLE users_v8(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL CHECK(role IN ('admin','teacher','student','hod','dean')),
                username TEXT NOT NULL UNIQUE,
                email TEXT,
                name TEXT,
                roll TEXT,
                emp_code TEXT,
                password_hash TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            INSERT INTO users_v8(id, role, username, email, name, roll, emp_code,
                                 password_hash, is_active, must_change_password, created_at)
            SELECT id, role, username, email, name, roll, emp_code,
                   password_hash, is_active, must_change_password, created_at
            FROM users
        """)
        conn.execute('DROP TABLE users')
        conn.execute('ALTER TABLE users_v8 RENAME TO users')
        conn.execute('CREATE INDEX IF NOT EXISTS ix_users_role ON users(role)')
        conn.execute('CREATE INDEX IF NOT EXISTS ix_users_roll ON users(roll)')
        conn.execute('CREATE INDEX IF NOT EXISTS ix_users_emp ON users(emp_code)')
        conn.execute('CREATE INDEX IF NOT EXISTS ix_users_username ON users(username)')
        conn.execute('PRAGMA foreign_keys = ON')
    except Exception as exc:
        msg = str(exc).lower()
        if 'already exists' in msg or 'duplicate' in msg:
            return
        raise


def run_migrations(target):
    """Bring the database at ``target`` up to the latest schema version.

    ``target`` is either a SQLite file path or a Postgres connection string.
    """
    is_pg = pg.is_postgres_url(target)
    if is_pg:
        conn = pg.Connection(target)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(target)) or '.', exist_ok=True)
        conn = sqlite3.connect(target)
        conn.row_factory = sqlite3.Row
    locked = False
    try:
        ver = current_version(conn)
        if ver >= max(m[0] for m in MIGRATIONS):
            return                # already up to date: skip lock/unlock round trips
        if is_pg:
            # Serialise concurrent cold-start migrations across serverless
            # instances (session-level lock, released in the finally block).
            conn.execute('SELECT pg_advisory_lock(?)', (_PG_MIGRATION_LOCK,))
            locked = True
            ver = current_version(conn)
        for version, _description, statements in MIGRATIONS:
            if version <= ver:
                continue
            for stmt in statements:
                _apply_statement(conn, stmt)
            # Post-step for v8: expand the users.role CHECK safely.
            if version == 8:
                _expand_user_roles(conn, is_pg)
            conn.execute(
                'INSERT INTO feedback_system_schema_migrations(version) VALUES(?)',
                (version,)
            )
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            if is_pg and locked:
                try:
                    conn.execute(
                        'SELECT pg_advisory_unlock(?)',
                        (_PG_MIGRATION_LOCK,)
                    )
                except Exception:
                    conn.rollback()
        finally:
            conn.close()
