"""Durable measured-case records for an interrupted offline evaluation.

This journal is never used by runtime understanding. It preserves completed
measurements from one unchanged evaluation, with every case still in its scope.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


OUTCOMES = ('selector_hit', 'parsed', 'command_mode', 'command_kind',
            'fallback_mode', 'fallback_answered')


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


class MeasuredCases:
    def __init__(self, path: Path, inputs: dict, expected: int, *, resume: bool = False):
        if type(expected) is not int or expected < 1:
            raise ValueError('Evaluation scope must include cases')
        self.path = path
        self.expected = expected
        self.header = {'kind': 'evaluation_inputs', 'fingerprint': fingerprint(inputs),
                       'inputs': inputs, 'expected_cases': expected}
        self.records: list[dict] = []
        path.parent.mkdir(parents=True, exist_ok=True)
        if resume and path.exists():
            raw = path.read_bytes()
            # A process may stop between writing bytes and finishing the line.
            # Keep only complete lines; the unfinished case will be measured.
            durable = raw[:raw.rfind(b'\n') + 1]
            lines = durable.decode('utf-8').splitlines()
            if not lines or json.loads(lines[0]) != self.header:
                raise ValueError('Evaluation checkpoint inputs changed')
            for line in lines[1:]:
                self._validate(json.loads(line))
                self.records.append(json.loads(line))
            if durable != raw:
                path.write_bytes(durable)
        else:
            with path.open('w', encoding='utf-8', newline='\n') as out:
                out.write(json.dumps(self.header, ensure_ascii=False) + '\n')
                out.flush()
                os.fsync(out.fileno())

    def _validate(self, record: dict) -> None:
        if (not isinstance(record, dict) or type(record.get('index')) is not int
                or record.get('index') != len(self.records)
                or not 0 <= record['index'] < self.expected):
            raise ValueError('Evaluation checkpoint case order is invalid')
        outcome = record.get('outcome')
        if (not isinstance(outcome, dict) or set(outcome) != set(OUTCOMES)
                or any(type(value) is not bool for value in outcome.values())):
            raise ValueError('Evaluation checkpoint outcomes are invalid')

    def measured(self, index: int) -> dict | None:
        return self.records[index] if index < len(self.records) else None

    def append(self, record: dict) -> None:
        self._validate(record)
        with self.path.open('a', encoding='utf-8', newline='\n') as out:
            out.write(json.dumps(record, ensure_ascii=False) + '\n')
            out.flush()
            os.fsync(out.fileno())
        self.records.append(record)

    @property
    def complete(self) -> bool:
        return len(self.records) == self.expected


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    pending.replace(path)
