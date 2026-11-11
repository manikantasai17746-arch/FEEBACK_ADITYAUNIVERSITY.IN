"""Phase 1 / Phase 2 rules: pick-list names, no duplicate phase, Phase 2 gating."""
from feedback_system import db, services


def _q():
    return [{'text': 'Teaching quality', 'qtype': 'rating', 'is_required': True,
             'options': [{'label': 'Excellent', 'value': 5}, {'label': 'Poor', 'value': 1}]}]


def _sessions(n, offset):
    return [r['id'] for r in db.query(
        'SELECT id FROM class_sessions WHERE is_deleted=0 ORDER BY id LIMIT ? OFFSET ?',
        (n, offset))]


def _form(sids, phase, title=None, status='draft'):
    admin = db.query("SELECT id FROM users WHERE role='admin' LIMIT 1", one=True)['id']
    fid = services.save_form(
        {'title': title or services.PHASE_LABELS.get(phase, 'Custom'), 'phase': phase,
         'session_id': sids[0], 'scope': 'session'}, _q(), admin, session_ids=sids)
    db.get_db().execute('UPDATE feedback_forms SET status=? WHERE id=?', (status, fid))
    db.get_db().commit()
    return fid


def test_phase_from_title():
    assert services.phase_from_title(' phase  1 ') == 'phase1'
    assert services.phase_from_title('PHASE2') == 'phase2'
    assert services.phase_from_title('Mid-sem feedback') is None


def test_same_phase_blocked_on_single_shared_section(app):
    with app.app_context():
        a, b, c = _sessions(3, 0)
        _form([a, b], 'phase1')
        # shares only session b with the existing Phase 1 -> blocked
        assert services.phase_rule_errors('phase1', [b, c])
        # no overlap -> allowed
        assert not services.phase_rule_errors('phase1', [c])
        # editing the existing form itself is not a clash
        fid = db.query("SELECT id FROM feedback_forms WHERE phase='phase1' "
                       "ORDER BY id DESC LIMIT 1", one=True)['id']
        assert not services.phase_rule_errors('phase1', [a, b], exclude_form_id=fid)


def test_custom_names_unrestricted(app):
    with app.app_context():
        s, = _sessions(1, 5)
        _form([s], None, title='Mid-semester check')
        assert not services.phase_rule_errors(None, [s])


def test_phase2_requires_phase1_closed(app):
    with app.app_context():
        s, = _sessions(1, 8)
        # no Phase 1 at all -> allowed
        assert not services.phase_rule_errors('phase2', [s])
        p1 = _form([s], 'phase1', status='open')
        assert any('until Phase 1 is closed' in e
                   for e in services.phase_rule_errors('phase2', [s]))
        db.get_db().execute("UPDATE feedback_forms SET status='closed' WHERE id=?", (p1,))
        db.get_db().commit()
        assert not services.phase_rule_errors('phase2', [s])


def test_phase2_publish_blocked_until_phase1_closed(app):
    with app.app_context():
        s, = _sessions(1, 11)
        p2 = _form([s], 'phase2')                      # created while no Phase 1 exists
        p1 = _form([s], 'phase1', status='open')       # Phase 1 now running
        errs = services.validate_form_for_publish(p2)
        assert any('until Phase 1 is closed' in e for e in errs)
        db.get_db().execute("UPDATE feedback_forms SET status='closed' WHERE id=?", (p1,))
        db.get_db().commit()
        errs = services.validate_form_for_publish(p2)
        assert not any('Phase 1' in e for e in errs)


def test_soft_deleted_form_does_not_block(app):
    with app.app_context():
        s, = _sessions(1, 14)
        f = _form([s], 'phase1')
        db.get_db().execute('UPDATE feedback_forms SET is_deleted=1 WHERE id=?', (f,))
        db.get_db().commit()
        assert not services.phase_rule_errors('phase1', [s])
