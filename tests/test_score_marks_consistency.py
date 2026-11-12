"""Score / 100 and the 10-marks-per-student marks use ONE scale and never drift."""
import pytest

from feedback_system import scoring

SCALE = [('Excellent', 5), ('Very Good', 4), ('Good', 3), ('Average', 2), ('Poor', 1)]


def _data(n_questions, answers, students):
    """Synthetic form data. ``answers`` = {question_index: {label: count}}."""
    questions = [{
        'id': i, 'form_id': 1, 'qtype': 'rating', 'text': f'Q{i}', 'position': i,
        'options': [{'label': l, 'value': v, 'position': k}
                    for k, (l, v) in enumerate(SCALE)],
    } for i in range(1, n_questions + 1)]
    rows = [{'question_id': q, 'label': label, 'value': dict(SCALE)[label],
             'position': 0, 'n': n}
            for q, counts in answers.items() for label, n in counts.items()]
    return {'questions': {1: questions}, 'sub_count': {1: students},
            'answers': {1: rows}}


def _both(n_questions, answers, students, eligible=None):
    data = _data(n_questions, answers, students)
    score = scoring._scores_from_data(1, 1, data)['overall_score']
    marks = scoring._marks_from_data(1, eligible or students, data)
    return score, marks


@pytest.mark.parametrize('n_questions', [3, 7, 9, 12, 13])
def test_a_perfect_student_gets_exactly_10_marks_and_100_percent(n_questions):
    answers = {q: {'Excellent': 1} for q in range(1, n_questions + 1)}
    score, marks = _both(n_questions, answers, students=1)
    assert score == 100.0
    assert marks['obtained'] == 10.0          # was 9.96 for 12 questions, 10.01 for 7


def test_poor_is_the_same_value_in_score_and_marks():
    score, marks = _both(1, {1: {'Poor': 1}}, students=1)
    assert score == 25.0
    assert marks['obtained'] == 2.5


def test_mixed_ratings_score_matches_marks():
    ratings = ['Excellent', 'Very Good', 'Good', 'Average', 'Poor', 'Poor', 'Good']
    answers = {i + 1: {r: 1} for i, r in enumerate(ratings)}
    score, marks = _both(7, answers, students=1)
    assert score == pytest.approx(51.79, abs=0.01)       # 3.625 / 7
    assert marks['obtained'] == pytest.approx(5.18, abs=0.01)
    assert marks['obtained'] / 10 * 100 == pytest.approx(score, abs=0.06)


def test_several_students_score_equals_marks_over_marks_possible():
    # 3 students, 4 questions, different ratings.
    answers = {1: {'Excellent': 2, 'Good': 1}, 2: {'Very Good': 3},
               3: {'Average': 1, 'Poor': 2}, 4: {'Excellent': 1, 'Good': 2}}
    score, marks = _both(4, answers, students=3, eligible=30)
    assert marks['maximum'] == 300.0
    assert marks['obtained'] / (10 * 3) * 100 == pytest.approx(score, abs=0.06)


def test_score_never_exceeds_100_and_marks_never_exceed_10_per_student():
    answers = {q: {'Excellent': 5} for q in range(1, 8)}
    score, marks = _both(7, answers, students=5)
    assert score == 100.0
    assert marks['obtained'] == 50.0
