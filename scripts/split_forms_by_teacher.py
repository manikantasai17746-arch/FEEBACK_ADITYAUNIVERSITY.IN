"""Split old multi-teacher forms into ONE form per teacher (keeps all responses).

Older "Subject only" forms covered every teacher of a subject in a single form,
so the Dean / HOD tables could show only one of those teachers.  This script
turns each such form into one form per teacher:

* the teacher of the form's primary session keeps the ORIGINAL form (same ID);
* every other teacher gets a NEW form (same title, phase, deadline, status,
  questions and options) holding just that teacher's sessions;
* each student's submission (and its answers) moves to the form of the teacher
  whose session the student belongs to, so every teacher keeps exactly the
  responses that were meant for them;
* result publications of moved sessions move with them.

Usage (from the project root, with DATABASE_URL / FEEDBACK_DB pointing at the
database you want to change - same as the other scripts):

    python scripts/split_forms_by_teacher.py            # DRY RUN: only prints the plan
    python scripts/split_forms_by_teacher.py --apply    # really changes the database

Take a database backup first (Supabase: Database -> Backups).  The whole change
runs in ONE transaction: if anything fails nothing is changed.  Re-running is
safe - forms that already have a single teacher are skipped.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feedback_system import create_app, db  # noqa: E402


def find_multi_teacher_forms():
    """[(form_row_dict, {teacher_user_id: {'name', 'sessions': [session_id...]}})]"""
    forms = [dict(r) for r in db.query(
        "SELECT * FROM feedback_forms WHERE is_deleted=0 ORDER BY id")]
    out = []
    for f in forms:
        rows = db.query(
            """SELECT cs.id AS sid, cs.teacher_user_id AS tid, u.name AS tname,
                      cs.period_no AS period
               FROM form_sessions fs
               JOIN class_sessions cs ON cs.id=fs.session_id
               LEFT JOIN users u ON u.id=cs.teacher_user_id
               WHERE fs.form_id=? ORDER BY cs.id""", (f['id'],))
        groups = {}
        for r in rows:
            g = groups.setdefault(r['tid'], {'name': r['tname'] or 'Unassigned teacher',
                                             'sessions': [], 'period': r['period']})
            g['sessions'].append(r['sid'])
        if len(groups) > 1:
            out.append((f, groups))
    return out


def primary_teacher(form, groups):
    """Teacher who keeps the original form: owner of the form's own session_id."""
    for tid, g in groups.items():
        if form.get('session_id') in g['sessions']:
            return tid
    return next(iter(groups))


def student_group(student_id, submission_session, groups):
    """Teacher group a submission belongs to (None if it cannot be decided)."""
    if submission_session:
        for tid, g in groups.items():
            if submission_session in g['sessions']:
                return tid
    all_sessions = [s for g in groups.values() for s in g['sessions']]
    ph = ','.join('?' * len(all_sessions))
    rows = db.query(
        f"SELECT DISTINCT session_id FROM session_students "
        f"WHERE student_user_id=? AND session_id IN ({ph})",
        (student_id, *all_sessions))
    found = set()
    for r in rows:
        for tid, g in groups.items():
            if r['session_id'] in g['sessions']:
                found.add(tid)
    return next(iter(found)) if len(found) == 1 else None


def plan_form(form, groups):
    keep = primary_teacher(form, groups)
    subs = db.query("SELECT id, session_id, student_user_id FROM submissions WHERE form_id=?",
                    (form['id'],))
    moves = {}        # submission id -> new teacher id
    unresolved = []
    for s in subs:
        tid = student_group(s['student_user_id'], s['session_id'], groups)
        if tid is None:
            unresolved.append(s['id'])
        elif tid != keep:
            moves[s['id']] = tid
    return keep, moves, unresolved


def describe(form, groups, keep, moves, unresolved):
    print(f"\nForm #{form['id']} '{form['title']}' ({form.get('snapshot_subject_code') or '-'}, "
          f"status={form['status']}) has {len(groups)} teachers:")
    for tid, g in groups.items():
        n_moved = sum(1 for t in moves.values() if t == tid)
        if tid == keep:
            print(f"   keeps form #{form['id']}: {g['name']} ({len(g['sessions'])} session(s))")
        else:
            print(f"   NEW form for {g['name']} ({len(g['sessions'])} session(s)), "
                  f"{n_moved} response(s) move there")
    if unresolved:
        print(f"   {len(unresolved)} response(s) could not be matched to one teacher "
              f"and stay on form #{form['id']}")


def apply_form(conn, form, groups, keep, moves):
    old_id = form['id']
    cols = [c for c in form if c != 'id']
    q_rows = [dict(r) for r in db.query(
        "SELECT * FROM form_questions WHERE form_id=? ORDER BY position, id", (old_id,))]
    opt_rows = {q['id']: [dict(o) for o in db.query(
        "SELECT * FROM question_options WHERE question_id=? ORDER BY position, id", (q['id'],))]
        for q in q_rows}

    # the original form now lists only the primary teacher's sessions
    for tid, g in groups.items():
        if tid != keep:
            for sid in g['sessions']:
                conn.execute("DELETE FROM form_sessions WHERE form_id=? AND session_id=?",
                             (old_id, sid))
    keep_g = groups[keep]
    conn.execute("UPDATE feedback_forms SET snapshot_teacher=?, snapshot_period=? WHERE id=?",
                 (keep_g['name'], keep_g['period'], old_id))

    created = {}
    for tid, g in groups.items():
        if tid == keep:
            continue
        vals = dict(form)
        vals.pop('id')
        vals['session_id'] = g['sessions'][0]
        vals['snapshot_teacher'] = g['name']
        vals['snapshot_period'] = g['period']
        new_id = db.insert_id(
            f"INSERT INTO feedback_forms({', '.join(cols)}) "
            f"VALUES({', '.join('?' for _ in cols)})",
            tuple(vals[c] for c in cols))
        created[tid] = new_id
        for sid in g['sessions']:
            conn.execute("INSERT INTO form_sessions(form_id, session_id) VALUES(?,?)",
                         (new_id, sid))
            conn.execute("UPDATE result_publications SET form_id=? WHERE form_id=? AND session_id=?",
                         (new_id, old_id, sid))
        # copy questions / options, remember old -> new ids for the answers
        qmap, omap = {}, {}
        for q in q_rows:
            new_q = db.insert_id(
                "INSERT INTO form_questions(form_id, position, text, qtype, is_required, help_text) "
                "VALUES(?,?,?,?,?,?)",
                (new_id, q['position'], q['text'], q['qtype'], q['is_required'], q.get('help_text')))
            qmap[q['id']] = new_q
            for o in opt_rows[q['id']]:
                omap[o['id']] = db.insert_id(
                    "INSERT INTO question_options(question_id, position, label, value) "
                    "VALUES(?,?,?,?)", (new_q, o['position'], o['label'], o['value']))
        # move this teacher's submissions and re-point their answers
        for sub_id, t in moves.items():
            if t != tid:
                continue
            conn.execute("UPDATE submissions SET form_id=? WHERE id=?", (new_id, sub_id))
            for a in db.query("SELECT id, question_id, option_id FROM answers WHERE submission_id=?",
                              (sub_id,)):
                conn.execute("UPDATE answers SET question_id=?, option_id=? WHERE id=?",
                             (qmap.get(a['question_id'], a['question_id']),
                              omap.get(a['option_id']) if a['option_id'] else None, a['id']))
    return created


def main():
    apply = '--apply' in sys.argv
    app = create_app({'AUTO_IMPORT': False})
    with app.app_context():
        backend = 'PostgreSQL' if db.using_postgres(app) else f'SQLite ({db.database_target(app)})'
        print(f"Database: {backend}")
        todo = find_multi_teacher_forms()
        if not todo:
            print("No multi-teacher forms found. Nothing to do.")
            return
        plans = []
        for form, groups in todo:
            keep, moves, unresolved = plan_form(form, groups)
            describe(form, groups, keep, moves, unresolved)
            plans.append((form, groups, keep, moves))
        if not apply:
            print("\nDRY RUN - nothing was changed. Re-run with --apply to split these forms.")
            return
        conn = db.get_db()
        try:
            for form, groups, keep, moves in plans:
                created = apply_form(conn, form, groups, keep, moves)
                print(f"Split form #{form['id']} -> new forms: "
                      + ', '.join(f"#{i} ({groups[t]['name']})" for t, i in created.items()))
            conn.commit()
        except Exception:
            conn.rollback()
            print("FAILED - rolled back, nothing was changed.", file=sys.stderr)
            raise
        print("\nDone. Refresh the Dean / HOD pages.")


if __name__ == '__main__':
    main()
