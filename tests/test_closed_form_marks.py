"""Maximum marks: OPEN form uses all eligible students, CLOSED form uses only
the students who responded.  The per-question split (10 / questions) is unchanged.
"""
from datetime import datetime, timedelta

from feedback_system import scoring

LABELS = ['Excellent', 'Very Good', 'Good', 'Average', 'Poor']


def _data(*, responded, closed, per_question_answers, n_questions=5, form_id=1):
    """Build the pre-loaded data dict that _marks_from_data expects.

    per_question_answers: {label: count} used for EVERY question.
    """
    questions = []
    for qi in range(1, n_questions + 1):
        opts = [{'id': qi * 10 + i, 'label': lab, 'value': 5 - i, 'position': i}
                for i, lab in enumerate(LABELS)]
        questions.append({'id': qi, 'form_id': form_id, 'qtype': 'rating',
                          'text': f'Q{qi}', 'position': qi, 'options': opts})
    answers = []
    for qi in range(1, n_questions + 1):
        for i, lab in enumerate(LABELS):
            n = per_question_answers.get(lab, 0)
            if n:
                answers.append({'form_id': form_id, 'question_id': qi,
                                'label': lab, 'value': 5 - i, 'position': i, 'n': n})
    return {'questions': {form_id: questions},
            'sub_count': {form_id: responded},
            'answers': {form_id: answers},
            'closed': {form_id: closed}}


def _pct(fm):
    return round(fm['obtained'] / fm['maximum'] * 100, 2) if fm['maximum'] else None


# 60 students, 5 questions: 40 Excellent + 20 Good per question
#   = (40*1.0 + 20*0.5) * 2 marks * 5 questions = 500 marks
MIX = {'Excellent': 40, 'Good': 20}


def test_open_form_uses_all_eligible_students():
    fm = scoring._marks_from_data(1, 72, _data(responded=60, closed=False,
                                               per_question_answers=MIX))
    assert fm['marks_per_question'] == 2.0          # 10 / 5, unchanged
    assert fm['obtained'] == 500.0
    assert fm['maximum'] == 720.0                   # 72 x 10
    assert fm['max_students'] == 72 and fm['closed'] is False
    assert _pct(fm) == 69.44


def test_closed_form_uses_only_responders():
    fm = scoring._marks_from_data(1, 72, _data(responded=60, closed=True,
                                               per_question_answers=MIX))
    assert fm['marks_per_question'] == 2.0          # 10 / 5, unchanged
    assert fm['obtained'] == 500.0                  # marks themselves unchanged
    assert fm['maximum'] == 600.0                   # 60 x 10
    assert fm['max_students'] == 60 and fm['closed'] is True
    assert fm['eligible'] == 72                     # real eligible still reported
    assert _pct(fm) == 83.33


def test_all_excellent_closed_is_100_open_is_83():
    ex = {'Excellent': 60}
    openf = scoring._marks_from_data(1, 72, _data(responded=60, closed=False,
                                                  per_question_answers=ex))
    closed = scoring._marks_from_data(1, 72, _data(responded=60, closed=True,
                                                   per_question_answers=ex))
    assert openf['obtained'] == closed['obtained'] == 600.0
    assert _pct(openf) == 83.33
    assert _pct(closed) == 100.0


def test_marks_never_exceed_maximum_for_one_student():
    fm = scoring._marks_from_data(1, 72, _data(responded=1, closed=True,
                                               per_question_answers={'Excellent': 1}))
    assert fm['obtained'] == 10.0 and fm['maximum'] == 10.0   # never 10.1


def test_closed_with_no_responses_does_not_crash():
    fm = scoring._marks_from_data(1, 72, _data(responded=0, closed=True,
                                               per_question_answers={}))
    assert fm['maximum'] == 0.0 and fm['obtained'] == 0.0
    assert _pct(fm) is None


def test_data_without_closed_key_behaves_as_open():
    d = _data(responded=60, closed=True, per_question_answers=MIX)
    del d['closed']
    fm = scoring._marks_from_data(1, 72, d)
    assert fm['maximum'] == 720.0 and fm['closed'] is False


def test_form_is_closed_rules():
    past = (datetime.utcnow() - timedelta(hours=1)).isoformat(timespec='seconds')
    future = (datetime.utcnow() + timedelta(hours=1)).isoformat(timespec='seconds')
    assert scoring._form_is_closed('closed', None) is True
    assert scoring._form_is_closed('archived', None) is True
    assert scoring._form_is_closed('open', past) is True       # deadline passed
    assert scoring._form_is_closed('open', future) is False
    assert scoring._form_is_closed('open', None) is False
    assert scoring._form_is_closed('draft', past) is False
