# NLU robustness workflow

The deterministic router remains the production authority. No semantic model is
allowed to create a service draft or bypass confirmation. The current semantic
router is configured as `shadow` and is only used for offline measurement.

## Reproduce the benchmark

Generate deterministic perturbations from reviewed scenarios:

```powershell
$env:PYTHONPATH = 'src'
python tools/nlu/perturb.py --output reports/nlu/nlu-perturbations.jsonl --per-case 4
python tools/nlu/robustness_report.py --per-case 1 --output reports/nlu/nlu-robustness.json
```

Normalization is profile-owned. It applies Unicode normalization, configured
Vietnamese accent restoration, repeated-letter cleanup, and a unique fuzzy
candidate only when the configured similarity threshold is met. The helper
`normalize_intent_with_spans` exposes each rewrite for slot/evidence auditing.

## Shadow semantic router

Rebuild the reviewed route examples after the gold suite changes, then calibrate
against a concept-held-out split:

```powershell
$env:PYTHONPATH = 'src'
python tools/nlu/build_route_examples.py
python tools/manifest/refresh_property.py
python tools/nlu/calibrate_router.py --output reports/nlu/nlu-router-calibration.json
```

The checked-in calibration report is evidence for a later model/config decision,
not an automatic threshold update. The current offline hash embedder is useful
for reproducibility and typo tolerance, but is not treated as a learned semantic
quality claim. A learned multilingual model must be compared on the same
concept-held-out data, per language, with latency and abstention metrics before
`mode` can move from `shadow` to `active`.
