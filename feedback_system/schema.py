"""Versioned SQLite migrations.

Design notes
------------
* Migration 1 reproduces the *original* schema verbatim, so an existing
  ``feedback.db`` from the legacy single-file app is recognised as "version 1"
  and no data is ever dropped.
* Later migrations only ADD columns/tables/indexes.  Nothing is renamed or
  deleted, so historical ``assignments``/``feedback`` rows survive untouched.
* ``ALTER TABLE ... ADD COLUMN`` statements are applied defensively (a column
  that already exists is skipped) so a hand-upgraded database still works.
"""

MIGRATIONS = [
    # ------------------------------------------------------------------ v1
    (1, "Baseline schema from the original application", [
        """CREATE TABLE IF NOT EXISTS assignments(
            id INTEGER PRIMARY KEY, roll TEXT, dept TEXT, course_code TEXT,
            course_name TEXT, emp_code TEXT, emp_name TEXT,
            UNIQUE(roll, course_code, emp_code))""",
        """CREATE TABLE IF NOT EXISTS feedback(
            id INTEGER PRIMARY KEY, roll TEXT, course_code TEXT, emp_code TEXT,
            rating INTEGER, comment TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(roll, course_code, emp_code))""",
        "CREATE INDEX IF NOT EXISTS ix_roll ON assignments(roll)",
    ]),

    # ------------------------------------------------------------------ v2
    (2, "Add timetable / period / academic context to assignments", [
        "ALTER TABLE assignments ADD COLUMN timetable TEXT",
        "ALTER TABLE assignments ADD COLUMN period_no INTEGER",
        "ALTER TABLE assignments ADD COLUMN semester INTEGER",
        "ALTER TABLE assignments ADD COLUMN academic_year TEXT",
        "CREATE INDEX IF NOT EXISTS ix_assignments_tt ON assignments(dept, timetable)",
        "CREATE INDEX IF NOT EXISTS ix_assignments_emp ON assignments(emp_code)",
    ]),

    # ------------------------------------------------------------------ v3
    (3, "Academic structure, users and feedback domain tables", [
        """CREATE TABLE IF NOT EXISTS branches(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS academic_years(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS semesters(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number INTEGER NOT NULL UNIQUE,
            label TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL CHECK(role IN ('admin','teacher','student')),
            username TEXT NOT NULL UNIQUE,
            email TEXT,
            name TEXT,
            roll TEXT,
            emp_code TEXT,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        "CREATE INDEX IF NOT EXISTS ix_users_role ON users(role)",
        "CREATE INDEX IF NOT EXISTS ix_users_roll ON users(roll)",
        "CREATE INDEX IF NOT EXISTS ix_users_emp ON users(emp_code)",
        "CREATE INDEX IF NOT EXISTS ix_users_username ON users(username)",
        """CREATE TABLE IF NOT EXISTS timetables(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER NOT NULL REFERENCES branches(id),
            semester_id INTEGER NOT NULL REFERENCES semesters(id),
            academic_year_id INTEGER REFERENCES academic_years(id),
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            is_deleted INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT,
            UNIQUE(branch_id, semester_id, academic_year_id, code))""",
        """CREATE TABLE IF NOT EXISTS class_sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timetable_id INTEGER NOT NULL REFERENCES timetables(id),
            subject_code TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            teacher_user_id INTEGER REFERENCES users(id),
            period_no INTEGER,
            is_deleted INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT,
            UNIQUE(timetable_id, subject_code, teacher_user_id))""",
        "CREATE INDEX IF NOT EXISTS ix_sessions_teacher ON class_sessions(teacher_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_sessions_tt ON class_sessions(timetable_id)",
        """CREATE TABLE IF NOT EXISTS enrollments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_user_id INTEGER NOT NULL REFERENCES users(id),
            timetable_id INTEGER NOT NULL REFERENCES timetables(id),
            semester_id INTEGER NOT NULL REFERENCES semesters(id),
            academic_year_id INTEGER REFERENCES academic_years(id),
            is_deleted INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT,
            UNIQUE(student_user_id, timetable_id))""",
        "CREATE INDEX IF NOT EXISTS ix_enroll_tt ON enrollments(timetable_id)",
        """CREATE TABLE IF NOT EXISTS feedback_forms(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            instructions TEXT NOT NULL DEFAULT '',
            academic_year_id INTEGER REFERENCES academic_years(id),
            semester_id INTEGER REFERENCES semesters(id),
            branch_id INTEGER REFERENCES branches(id),
            timetable_id INTEGER REFERENCES timetables(id),
            session_id INTEGER REFERENCES class_sessions(id),
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','open','closed','archived')),
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            published_at TEXT,
            closed_at TEXT,
            archived_at TEXT,
            is_deleted INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT,
            snapshot_academic_year TEXT,
            snapshot_semester INTEGER,
            snapshot_branch TEXT,
            snapshot_timetable TEXT,
            snapshot_subject_code TEXT,
            snapshot_subject_name TEXT,
            snapshot_teacher TEXT,
            snapshot_period INTEGER)""",
        "CREATE INDEX IF NOT EXISTS ix_forms_status ON feedback_forms(status)",
        """CREATE TABLE IF NOT EXISTS form_questions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_id INTEGER NOT NULL REFERENCES feedback_forms(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            text TEXT NOT NULL,
            qtype TEXT NOT NULL DEFAULT 'rating'
                CHECK(qtype IN ('rating','text','single_choice','multiple_choice')),
            is_required INTEGER NOT NULL DEFAULT 1,
            help_text TEXT)""",
        "CREATE INDEX IF NOT EXISTS ix_questions_form ON form_questions(form_id)",
        """CREATE TABLE IF NOT EXISTS question_options(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL REFERENCES form_questions(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            label TEXT NOT NULL,
            value INTEGER)""",
        "CREATE INDEX IF NOT EXISTS ix_options_question ON question_options(question_id)",
        """CREATE TABLE IF NOT EXISTS submissions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_id INTEGER NOT NULL REFERENCES feedback_forms(id),
            session_id INTEGER REFERENCES class_sessions(id),
            student_user_id INTEGER NOT NULL REFERENCES users(id),
            submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(form_id, student_user_id))""",
        "CREATE INDEX IF NOT EXISTS ix_submissions_form ON submissions(form_id)",
        "CREATE INDEX IF NOT EXISTS ix_submissions_student ON submissions(student_user_id)",
        """CREATE TABLE IF NOT EXISTS answers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
            question_id INTEGER NOT NULL REFERENCES form_questions(id),
            option_id INTEGER REFERENCES question_options(id),
            text_value TEXT)""",
        "CREATE INDEX IF NOT EXISTS ix_answers_question ON answers(question_id)",
        "CREATE INDEX IF NOT EXISTS ix_answers_submission ON answers(submission_id)",
        # Anonymity enforced at the database layer: this view deliberately
        # exposes answers WITHOUT any link to the student who produced them.
        """CREATE VIEW IF NOT EXISTS anonymous_answers AS
            SELECT a.id AS answer_id, a.question_id, a.option_id, a.text_value,
                   s.form_id, s.session_id, s.submitted_at
            FROM answers a
            JOIN submissions s ON s.id = a.submission_id""",
        """CREATE TABLE IF NOT EXISTS result_publications(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_id INTEGER NOT NULL REFERENCES feedback_forms(id),
            session_id INTEGER NOT NULL REFERENCES class_sessions(id),
            is_published INTEGER NOT NULL DEFAULT 0,
            published_by INTEGER REFERENCES users(id),
            published_at TEXT,
            unpublished_by INTEGER REFERENCES users(id),
            unpublished_at TEXT,
            UNIQUE(form_id, session_id))""",
        """CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER REFERENCES users(id),
            actor_label TEXT,
            action TEXT NOT NULL,
            entity TEXT,
            entity_id INTEGER,
            detail TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        "CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_log(created_at)",
    ]),

    # ------------------------------------------------------------------ v4
    (4, "Eligible student population per teaching session", [
        """CREATE TABLE IF NOT EXISTS session_students(
            session_id INTEGER NOT NULL REFERENCES class_sessions(id),
            student_user_id INTEGER NOT NULL REFERENCES users(id),
            PRIMARY KEY (session_id, student_user_id))""",
        "CREATE INDEX IF NOT EXISTS ix_session_students_student "
        "ON session_students(student_user_id)",
    ]),

    # ------------------------------------------------------------------ v5
    # Subject-scoped ("all timetables") feedback forms.  A form can now cover
    # several teaching sessions so one form serves every student group that
    # studies the same subject in the selected academic configuration.
    # Purely additive: existing session-scoped forms are back-filled so every
    # form has at least one row in ``form_sessions``.  No data is dropped.
    (5, "Subject-scoped forms and the sessions they cover", [
        """CREATE TABLE IF NOT EXISTS form_sessions(
            form_id INTEGER NOT NULL REFERENCES feedback_forms(id),
            session_id INTEGER NOT NULL REFERENCES class_sessions(id),
            PRIMARY KEY (form_id, session_id))""",
        "CREATE INDEX IF NOT EXISTS ix_form_sessions_session ON form_sessions(session_id)",
        "ALTER TABLE feedback_forms ADD COLUMN scope TEXT NOT NULL DEFAULT 'session'",
        "ALTER TABLE feedback_forms ADD COLUMN subject_code TEXT",
        "ALTER TABLE feedback_forms ADD COLUMN subject_name TEXT",
        # Back-fill: every pre-existing form covers exactly its own session.
        """INSERT OR IGNORE INTO form_sessions(form_id, session_id)
            SELECT id, session_id FROM feedback_forms WHERE session_id IS NOT NULL""",
        """UPDATE feedback_forms SET
            subject_code = (SELECT cs.subject_code FROM class_sessions cs
                            WHERE cs.id = feedback_forms.session_id),
            subject_name = (SELECT cs.subject_name FROM class_sessions cs
                            WHERE cs.id = feedback_forms.session_id)
            WHERE session_id IS NOT NULL
              AND (subject_code IS NULL OR subject_code = '')""",
    ]),
    # ------------------------------------------------------------------ v6
    # One-time e-mail codes: confirming a user's e-mail address and resetting a
    # forgotten password.  Only a keyed hash of the code is stored.
    (6, "E-mail verification / password-reset codes", [
        """CREATE TABLE IF NOT EXISTS email_codes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            purpose TEXT NOT NULL,
            email TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_ts BIGINT NOT NULL,
            expires_ts BIGINT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS ix_email_codes_user ON email_codes(user_id, purpose)",
    ]),

    # ------------------------------------------------------------------ v7
    # Prevent duplicate workbook imports and identify imports interrupted in flight.
    (7, "Track timetable workbook imports", [
        """CREATE TABLE IF NOT EXISTS timetable_imports(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT NOT NULL,
            semester INTEGER NOT NULL,
            academic_year TEXT NOT NULL,
            filename TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('importing','completed')),
            started_ts BIGINT NOT NULL,
            UNIQUE(fingerprint, semester, academic_year))""",
    ]),

    # ------------------------------------------------------------------ v8
    # HOD / Dean portals, department scopes, second-faculty assignments,
    # feedback requirements, and form category (main vs second faculty).
    # Role CHECK expansion is performed by a post-migration step in db.py
    # (SQLite table rebuild + PostgreSQL constraint replace) so existing
    # users and records are never dropped.
    (8, "HOD/Dean scopes, second-faculty, feedback requirements", [
        # Explicit department scopes for HOD and Dean accounts.
        """CREATE TABLE IF NOT EXISTS user_department_scopes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            branch_id INTEGER NOT NULL REFERENCES branches(id),
            is_primary INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, branch_id))""",
        "CREATE INDEX IF NOT EXISTS ix_user_scopes_user ON user_department_scopes(user_id)",
        "CREATE INDEX IF NOT EXISTS ix_user_scopes_branch ON user_department_scopes(branch_id)",

        # Second-faculty assignment mappings (verified or unresolved).
        # NULL second_emp_code / second_teacher_user_id = unresolved / not configured.
        """CREATE TABLE IF NOT EXISTS second_faculty_assignments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            academic_year_id INTEGER REFERENCES academic_years(id),
            semester_id INTEGER REFERENCES semesters(id),
            branch_id INTEGER REFERENCES branches(id),
            timetable_id INTEGER REFERENCES timetables(id),
            session_id INTEGER REFERENCES class_sessions(id),
            subject_code TEXT NOT NULL,
            subject_name TEXT,
            main_emp_code TEXT,
            main_teacher_user_id INTEGER REFERENCES users(id),
            second_emp_code TEXT,
            second_teacher_user_id INTEGER REFERENCES users(id),
            period_no INTEGER,
            day_label TEXT,
            room TEXT,
            status TEXT NOT NULL DEFAULT 'unresolved'
                CHECK(status IN ('confirmed','unresolved','rejected','conflict')),
            source_batch_id INTEGER,
            source_row INTEGER,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(session_id, second_emp_code))""",
        "CREATE INDEX IF NOT EXISTS ix_sfa_session ON second_faculty_assignments(session_id)",
        "CREATE INDEX IF NOT EXISTS ix_sfa_branch ON second_faculty_assignments(branch_id)",
        "CREATE INDEX IF NOT EXISTS ix_sfa_status ON second_faculty_assignments(status)",
        "CREATE INDEX IF NOT EXISTS ix_sfa_second ON second_faculty_assignments(second_teacher_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_sfa_subject ON second_faculty_assignments(subject_code)",

        # Import batch history for second-faculty workbooks.
        """CREATE TABLE IF NOT EXISTS second_faculty_import_batches(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            fingerprint TEXT,
            academic_year TEXT,
            semester INTEGER,
            branch_code TEXT,
            status TEXT NOT NULL DEFAULT 'preview'
                CHECK(status IN ('preview','committed','cancelled','failed')),
            total_rows INTEGER NOT NULL DEFAULT 0,
            confirmed_rows INTEGER NOT NULL DEFAULT 0,
            unresolved_rows INTEGER NOT NULL DEFAULT 0,
            rejected_rows INTEGER NOT NULL DEFAULT 0,
            conflict_rows INTEGER NOT NULL DEFAULT 0,
            duplicate_rows INTEGER NOT NULL DEFAULT 0,
            imported_by INTEGER REFERENCES users(id),
            detail_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            committed_at TEXT)""",

        # Staging rows for preview-before-commit second-faculty imports.
        """CREATE TABLE IF NOT EXISTS second_faculty_import_staging(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL REFERENCES second_faculty_import_batches(id),
            row_number INTEGER,
            academic_year TEXT,
            semester INTEGER,
            branch_code TEXT,
            timetable_code TEXT,
            subject_code TEXT,
            subject_name TEXT,
            main_emp_code TEXT,
            second_emp_code TEXT,
            period_no INTEGER,
            day_label TEXT,
            room TEXT,
            match_status TEXT,
            match_notes TEXT,
            session_id INTEGER,
            main_teacher_user_id INTEGER,
            second_teacher_user_id INTEGER,
            is_duplicate INTEGER NOT NULL DEFAULT 0,
            is_conflict INTEGER NOT NULL DEFAULT 0)""",
        "CREATE INDEX IF NOT EXISTS ix_sfis_batch ON second_faculty_import_staging(batch_id)",

        # Admin-configured feedback requirements (for Dean publication monitoring).
        """CREATE TABLE IF NOT EXISTS feedback_requirements(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            academic_year_id INTEGER REFERENCES academic_years(id),
            semester_id INTEGER REFERENCES semesters(id),
            branch_id INTEGER REFERENCES branches(id),
            category TEXT NOT NULL DEFAULT 'main'
                CHECK(category IN ('main','second','subject_only','any')),
            subject_code TEXT,
            title TEXT NOT NULL,
            deadline TEXT,
            status TEXT NOT NULL DEFAULT 'required'
                CHECK(status IN ('required','optional','cancelled')),
            matched_form_id INTEGER REFERENCES feedback_forms(id),
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        "CREATE INDEX IF NOT EXISTS ix_freq_branch ON feedback_requirements(branch_id)",
        "CREATE INDEX IF NOT EXISTS ix_freq_ay_sem ON feedback_requirements(academic_year_id, semester_id)",

        # Form category: main-faculty vs second-faculty (additive; default main).
        "ALTER TABLE feedback_forms ADD COLUMN category TEXT NOT NULL DEFAULT 'main'",
        "ALTER TABLE feedback_forms ADD COLUMN second_faculty_assignment_id INTEGER",

        # Submissions already unique on (form_id, student_user_id) — category
        # separation is achieved by distinct forms, so no schema change needed
        # for completion tracking. Answers remain anonymous via the existing view.
    ]),

    # ------------------------------------------------------------------ v9
    # HOD/Dean exclusive multi-dimensional allocations, onboarding state,
    # Dean↔HOD assignments, and allocation audit history.
    (9, "HOD/Dean exclusive allocations, onboarding, Dean-HOD assignments", [
        # Onboarding / email verification flags (additive; safe defaults).
        "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN onboarding_complete INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN designation TEXT",

        # Canonical exclusive allocation key:
        # (academic_year_id, branch_id, semester_id, timetable_id)
        # One HOD owns each exact combination. timetable_id may be NULL only when
        # the schema truly has no timetable (we keep it NOT NULL when possible).
        """CREATE TABLE IF NOT EXISTS hod_allocations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hod_user_id INTEGER NOT NULL REFERENCES users(id),
            academic_year_id INTEGER NOT NULL REFERENCES academic_years(id),
            branch_id INTEGER NOT NULL REFERENCES branches(id),
            semester_id INTEGER NOT NULL REFERENCES semesters(id),
            timetable_id INTEGER NOT NULL REFERENCES timetables(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER REFERENCES users(id),
            UNIQUE(academic_year_id, branch_id, semester_id, timetable_id))""",
        "CREATE INDEX IF NOT EXISTS ix_hod_alloc_hod ON hod_allocations(hod_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_hod_alloc_branch ON hod_allocations(branch_id)",
        "CREATE INDEX IF NOT EXISTS ix_hod_alloc_tt ON hod_allocations(timetable_id)",

        # Dean is assigned one or more HODs; visibility is derived from those
        # HOD allocations. No second ownership claim on the allocation itself.
        """CREATE TABLE IF NOT EXISTS dean_hod_assignments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dean_user_id INTEGER NOT NULL REFERENCES users(id),
            hod_user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER REFERENCES users(id),
            UNIQUE(dean_user_id, hod_user_id))""",
        "CREATE INDEX IF NOT EXISTS ix_dean_hod_dean ON dean_hod_assignments(dean_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_dean_hod_hod ON dean_hod_assignments(hod_user_id)",

        # Allocation ownership history (admin reassign / release).
        """CREATE TABLE IF NOT EXISTS allocation_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            allocation_key TEXT NOT NULL,
            academic_year_id INTEGER,
            branch_id INTEGER,
            semester_id INTEGER,
            timetable_id INTEGER,
            previous_hod_user_id INTEGER,
            new_hod_user_id INTEGER,
            action TEXT NOT NULL CHECK(action IN ('claim','release','reassign','admin_assign')),
            actor_user_id INTEGER REFERENCES users(id),
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        "CREATE INDEX IF NOT EXISTS ix_alloc_hist_key ON allocation_history(allocation_key)",
    ]),

    # ------------------------------------------------------------------ v10
    # Student notifications, form drafts, improvement action tracker (Dean).
    (10, "Notifications, drafts, improvement actions", [
        """CREATE TABLE IF NOT EXISTS notifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            kind TEXT NOT NULL DEFAULT 'info'
                CHECK(kind IN ('deadline','published','confirmation','announcement','info')),
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            link TEXT,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        "CREATE INDEX IF NOT EXISTS ix_notif_user ON notifications(user_id, is_read)",

        """CREATE TABLE IF NOT EXISTS form_drafts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_user_id INTEGER NOT NULL REFERENCES users(id),
            form_id INTEGER NOT NULL REFERENCES feedback_forms(id),
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(student_user_id, form_id))""",
        "CREATE INDEX IF NOT EXISTS ix_drafts_student ON form_drafts(student_user_id)",

        """CREATE TABLE IF NOT EXISTS improvement_actions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER REFERENCES branches(id),
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'planned'
                CHECK(status IN ('planned','in_progress','completed','cancelled')),
            priority TEXT NOT NULL DEFAULT 'medium'
                CHECK(priority IN ('low','medium','high','critical')),
            owner_user_id INTEGER REFERENCES users(id),
            owner_name TEXT,
            due_date TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT)""",
        "CREATE INDEX IF NOT EXISTS ix_ia_branch ON improvement_actions(branch_id)",
        "CREATE INDEX IF NOT EXISTS ix_ia_status ON improvement_actions(status)",

        """CREATE TABLE IF NOT EXISTS improvement_action_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_id INTEGER NOT NULL REFERENCES improvement_actions(id) ON DELETE CASCADE,
            actor_user_id INTEGER REFERENCES users(id),
            note TEXT NOT NULL DEFAULT '',
            old_status TEXT,
            new_status TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        "CREATE INDEX IF NOT EXISTS ix_ial_action ON improvement_action_log(action_id)",
    ]),
]
