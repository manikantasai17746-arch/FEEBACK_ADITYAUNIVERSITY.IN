"""Admin screens for teacher leave / replacement and student TC / removal."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

from . import db, people_service as ps
from .security import audit, current_user, role_required

bp = Blueprint('people', __name__, url_prefix='/admin/people')


def _int_list(name):
    return [int(v) for v in request.form.getlist(name) if v.isdigit()]


@bp.route('/students/<int:uid>', methods=['GET', 'POST'])
@role_required('admin')
def student(uid):
    person = ps.get_person(uid, 'student') or abort(404)
    if request.method == 'POST':
        status = request.form.get('status', '')
        try:
            changed = ps.set_student_status(
                [uid], status, request.form.get('date'), request.form.get('reason'),
                current_user()['id'])
        except ps.PeopleError as exc:
            flash(str(exc), 'error')
        else:
            if changed:
                audit('admin.student_status', 'users', uid, f'status={status}')
            flash('Student status saved.', 'success')
        return redirect(url_for('people.student', uid=uid))
    return render_template('admin_person_student.html', user=current_user(), person=person,
                           statuses=ps.STUDENT_STATUSES, history=ps.history(uid))


@bp.route('/students/bulk', methods=['GET', 'POST'])
@role_required('admin')
def students_bulk():
    ctx = {'user': current_user(), 'statuses': ps.STUDENT_STATUSES, 'preview': None,
           'form': request.form}
    if request.method == 'POST':
        status = request.form.get('status', '')
        try:
            if status not in ps.STUDENT_STATUSES:
                raise ps.PeopleError('Choose a status.')
            ps.clean_date(request.form.get('date'))
            found, unknown = ps.match_rolls(request.form.get('rolls'))
            if request.form.get('confirm') == '1':
                changed = ps.set_student_status(
                    [p['id'] for p in found], status, request.form.get('date'),
                    request.form.get('reason'), current_user()['id'])
                audit('admin.student_status_bulk', 'users', None,
                      f'status={status} changed={changed} rolls='
                      + ' '.join(p['roll'] for p in found))
                flash(f'{changed} student(s) updated.', 'success')
                return redirect(url_for('people.students_bulk'))
            ctx['preview'] = {'found': found, 'unknown': unknown}
        except ps.PeopleError as exc:
            flash(str(exc), 'error')
    return render_template('admin_students_bulk.html', **ctx)


@bp.route('/teachers/<int:uid>', methods=['GET'])
@role_required('admin')
def teacher(uid):
    return _teacher_page(uid, {})


def _teacher_page(uid, form):
    person = ps.get_person(uid, 'teacher') or abort(404)
    return render_template(
        'admin_person_teacher.html', user=current_user(), person=person, form=form,
        statuses=ps.TEACHER_STATUSES, classes=ps.teacher_classes(uid),
        second_roles=ps.second_faculty_roles(uid), choices=ps.replacement_choices(uid),
        away=ps.open_replacements(teacher_id=uid), history=ps.history(uid))


@bp.route('/teachers/<int:uid>/deactivate', methods=['POST'])
@role_required('admin')
def deactivate(uid):
    ps.get_person(uid, 'teacher') or abort(404)
    status = request.form.get('status', '')
    try:
        if request.form.get('confirm') != '1':
            raise ps.PeopleError('Tick the confirmation box first.')
        plan = {}
        for session_id in _int_list('session_id'):
            mode = request.form.get(f'mode_{session_id}', '')
            if mode == 'replace':
                code = request.form.get(f'emp_{session_id}', '')
                target = ps.teacher_by_code(code.split(' ')[0] if code else '')
                if not target:
                    raise ps.PeopleError('Enter the Employee ID of an active teacher for '
                                         'every class marked "Replace".')
                plan[session_id] = target
            elif mode in ('vacant', 'keep'):
                plan[session_id] = None if mode == 'vacant' else 'keep'
            else:
                raise ps.PeopleError('Choose what happens to every class.')
        result = ps.deactivate_teacher(uid, status, request.form.get('date'),
                                       request.form.get('reason'), plan, current_user()['id'])
    except ps.PeopleError as exc:
        flash(str(exc), 'error')
        return _teacher_page(uid, request.form)      # keep what the admin had chosen
    else:
        audit('admin.teacher_status', 'users', uid,
              f"status={status} replaced={result['replaced']} vacant={result['vacant']}")
        flash(f"Saved. {result['replaced']} class(es) replaced, "
              f"{result['vacant']} without a teacher.", 'success')
    return redirect(url_for('people.teacher', uid=uid))


@bp.route('/teachers/<int:uid>/reactivate', methods=['POST'])
@role_required('admin')
def reactivate(uid):
    ps.get_person(uid, 'teacher') or abort(404)
    try:
        returned = ps.reactivate_teacher(
            uid, request.form.get('date'), request.form.get('reason'),
            _int_list('replacement_id'), current_user()['id'])
    except ps.PeopleError as exc:
        flash(str(exc), 'error')
    else:
        audit('admin.teacher_status', 'users', uid, f'status=active returned={returned}')
        flash(f'Teacher is active again. {returned} class(es) returned.', 'success')
    return redirect(url_for('people.teacher', uid=uid))


@bp.route('/vacancies', methods=['GET', 'POST'])
@role_required('admin')
def vacancies():
    if request.method == 'POST':
        rid = request.form.get('replacement_id', type=int)
        try:
            target = ps.teacher_by_code(request.form.get('emp', '').split(' ')[0])
            if not target:
                raise ps.PeopleError('Enter the Employee ID of an active teacher.')
            ps.assign_vacancy(rid, target, current_user()['id'])
        except ps.PeopleError as exc:
            flash(str(exc), 'error')
        else:
            audit('admin.assign_vacancy', 'class_replacements', rid, f'teacher={target}')
            flash('Teacher assigned.', 'success')
        return redirect(url_for('people.vacancies'))
    return render_template('admin_vacancies.html', user=current_user(),
                           rows=ps.open_replacements(vacant_only=True),
                           choices=ps.replacement_choices(0))
