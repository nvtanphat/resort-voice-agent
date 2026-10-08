from __future__ import annotations

import pytest

from tools.evaluation.command_results import MeasuredCases, OUTCOMES


def _case(index, *, correct=True):
    return {'index': index, 'outcome': {key: correct for key in OUTCOMES}}


def test_resume_preserves_measured_failures_and_requires_the_entire_scope(tmp_path):
    path = tmp_path / 'cases.jsonl'
    journal = MeasuredCases(path, {'model': 'local', 'source_hash': 'abc'}, 3)
    journal.append(_case(0, correct=False))
    journal.append(_case(1))
    assert not journal.complete
    resumed = MeasuredCases(path, {'model': 'local', 'source_hash': 'abc'}, 3, resume=True)
    assert resumed.measured(0)['outcome']['command_mode'] is False
    assert resumed.measured(2) is None
    resumed.append(_case(2))
    assert resumed.complete
    assert sum(row['outcome']['command_mode'] for row in resumed.records) == 2


def test_resume_refuses_changed_training_model_or_case_scope(tmp_path):
    path = tmp_path / 'cases.jsonl'
    journal = MeasuredCases(path, {'model': 'local', 'source_hash': 'abc'}, 3)
    journal.append(_case(0))
    original = path.read_bytes()
    for inputs, count in [({'model': 'other', 'source_hash': 'abc'}, 3),
                          ({'model': 'local', 'source_hash': 'changed'}, 3),
                          ({'model': 'local', 'source_hash': 'abc'}, 2)]:
        with pytest.raises(ValueError, match='inputs changed'):
            MeasuredCases(path, inputs, count, resume=True)
        assert path.read_bytes() == original


def test_interrupted_line_is_remeasured_without_losing_completed_cases(tmp_path):
    path = tmp_path / 'cases.jsonl'
    journal = MeasuredCases(path, {'source_hash': 'abc'}, 2)
    journal.append(_case(0, correct=False))
    with path.open('ab') as out:
        out.write(b'{"index":1,"outcome":')
    resumed = MeasuredCases(path, {'source_hash': 'abc'}, 2, resume=True)
    assert len(resumed.records) == 1
    assert resumed.measured(1) is None
    resumed.append(_case(1))
    assert resumed.complete
