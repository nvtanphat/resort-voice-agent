"""Score a production HTTP smoke result against the production product contract.

This is intentionally a release smoke gate, not a substitute for the checked-in
end-to-end scenario, journey and failure corpora.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / 'datasets' / 'evaluation' / 'audit' / 'production_hardening_audit.json'


def score(path: Path) -> dict:
    result = json.loads(path.read_text(encoding='utf-8'))
    audit = json.loads(AUDIT.read_text(encoding='utf-8'))
    if audit.get('status') not in {'PASS', 'PASS_WITH_GATED_HUMAN_REVIEW'}:
        raise ValueError('production hardening audit is not in a releasable state')
    rows = result.get('results') or []
    if not rows:
        raise ValueError('HTTP benchmark has no result rows')
    non_5xx = sum(int(int(row.get('http_status', 599)) < 500) for row in rows) / len(rows)
    by_language = result.get('by_language') or {}
    language_rates = {
        lang: values['passed'] / values['cases']
        for lang, values in by_language.items() if values.get('cases')
    }
    gap_pp = ((max(language_rates.values()) - min(language_rates.values())) * 100
              if language_rates else 100.0)
    latency = result.get('latency_ms') or {}
    deterministic_p95_target = 800
    checks = {
        'all_selected_oracles_pass': result.get('passed') == result.get('cases'),
        'http_non_5xx_rate': non_5xx >= 0.999,
        'language_pass_gap': gap_pp <= 5.0,
        'deterministic_p95_latency': float(latency.get('p95', 10**9)) <= deterministic_p95_target,
        'all_emergency_smoke_cases_pass': all(row.get('passed') for row in rows if row.get('oracle') == 'emergency_route'),
        'all_staff_gate_smoke_cases_pass': all(row.get('passed') for row in rows if row.get('oracle') == 'staff_gate_preserved'),
        'all_unsupported_fact_smoke_cases_abstain': all(row.get('passed') for row in rows if row.get('oracle') == 'unsupported_fact_abstained'),
        'all_low_risk_slot_fidelity_cases_pass': all(row.get('passed') for row in rows if row.get('oracle') == 'autonomous_dispatch_with_slots'),
    }
    return {
        'classification': 'production_local_http_smoke_score',
       
        'release_smoke_ready': all(checks.values()),
        'checks': checks,
        'observed': {
            'cases': len(rows), 'pass_rate': round(sum(bool(row.get('passed')) for row in rows) / len(rows), 4),
            'http_non_5xx_rate': round(non_5xx, 4),
            'language_pass_gap_pp': round(gap_pp, 2),
            'latency_p50_ms': latency.get('p50'), 'latency_p95_ms': latency.get('p95'),
        },
        'guard': 'Local smoke score only; counterfactual and local timings are not measured Furama production KPIs.',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('result', type=Path)
    args = parser.parse_args()
    print(json.dumps(score(args.result), ensure_ascii=False, indent=2, sort_keys=True))
