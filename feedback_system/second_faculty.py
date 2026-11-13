"""Second Faculty assignment pages and JSON API (Phase 4).

Only the authenticated *teacher* who teaches a session can manage its Second
Faculty.  The main-faculty identity always comes from the server session; the
browser only ever sends a session id, an employee code or an assignment id, and
each of those is re-validated in :mod:`second_faculty_service`.

CSRF is enforced globally (``security.init_csrf``): JSON calls send the token in
the ``X-CSRF-Token`` header.
"""
import logging

from flask import Blueprint, jsonify, render_template, request

from . import second_faculty_service as svc
from .security import audit, current_user, role_required

logger = logging.getLogger(__name__)

bp = Blueprint('second_faculty', __name__, url_prefix='/teacher/second-faculty')


def _json_error(err):
    return jsonify(ok=False, error=err.code, message=err.message), err.status


def _api(view):
    """Turn ServiceError into a JSON error with the right HTTP status."""
    import functools

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except svc.ServiceError as err:
            return _json_error(err)
        except Exception:
            logger.exception('Second-faculty API error on %s', request.path)
            return jsonify(ok=False, error='server_error',
                           message='Something went wrong. Please try again.'), 500
    return wrapper


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@bp.route('/')
@role_required('teacher')
def index():
    user = current_user()
    return render_template('teacher_second_faculty.html', cards=svc.list_cards(user))


@bp.route('/api/employees')
@role_required('teacher')
@_api
def api_employees():
    user = current_user()
    session = svc.owned_session(user, request.args.get('session_id'))
    svc.require_lab(session)
    out = svc.search_employees(user, session, request.args.get('q', ''),
                               page=request.args.get('page', 1, type=int))
    return jsonify(ok=True, **out)


@bp.route('/api/preview')
@role_required('teacher')
@_api
def api_preview():
    user = current_user()
    replace_id = request.args.get('replace_id', type=int)
    data = svc.build_preview(
        user, request.args.get('session_id'), request.args.get('emp_code', ''),
        page=request.args.get('page', 1, type=int),
        page_size=request.args.get('page_size', svc.STUDENT_PAGE_SIZE, type=int),
        replace_assignment_id=replace_id)
    return jsonify(ok=True, **data)


@bp.route('/api/cards/<int:session_id>')
@role_required('teacher')
@_api
def api_card(session_id):
    user = current_user()
    svc.owned_session(user, session_id)
    return jsonify(ok=True, card=svc.card_for_session(user, session_id))


@bp.route('/api/assign', methods=['POST'])
@role_required('teacher')
@_api
def api_assign():
    user = current_user()
    body = _body()
    res = svc.create_assignment(user, body.get('session_id'), body.get('emp_code'))
    audit('second_faculty.assign', 'second_faculty_assignments', res['assignment_id'],
          f"session={res['session']['id']} subject={res['session']['subject_code']} "
          f"second={res['employee']['emp_code']}")
    return jsonify(ok=True, assignment_id=res['assignment_id'],
                   card=svc.card_for_session(user, res['session']['id']),
                   message='Second Faculty assigned.'), 201


@bp.route('/api/assignments/<int:assignment_id>/replace', methods=['POST'])
@role_required('teacher')
@_api
def api_replace(assignment_id):
    user = current_user()
    res = svc.replace_assignment(user, assignment_id, _body().get('emp_code'))
    audit('second_faculty.replace', 'second_faculty_assignments', res['assignment_id'],
          f"replaced={res['replaced_id']} session={res['session']['id']} "
          f"second={res['employee']['emp_code']}")
    return jsonify(ok=True, assignment_id=res['assignment_id'],
                   card=svc.card_for_session(user, res['session']['id']),
                   message='Second Faculty replaced.')


@bp.route('/api/assignments/<int:assignment_id>/remove', methods=['POST'])
@role_required('teacher')
@_api
def api_remove(assignment_id):
    user = current_user()
    res = svc.remove_assignment(user, assignment_id)
    audit('second_faculty.remove', 'second_faculty_assignments', res['assignment_id'],
          f"session={res['session_id']} second={res['emp_code']}")
    return jsonify(ok=True, card=svc.card_for_session(user, res['session_id']),
                   message='Second Faculty removed.')
