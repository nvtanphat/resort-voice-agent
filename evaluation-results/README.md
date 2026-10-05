# Evaluation Results

This directory contains **environment-specific measurements**, not canonical Furama facts.

- `production-http-smoke-local.json` / `production-http-smoke-score.json`: production evaluation release-backed local smoke measurement.
- `hospitality-probe-{vi,en,ko,zh}.json`: Hospitality direct-style deterministic hospitality probes by language.
- `hospitality-gap-summary.json`: merged hospitality deterministic coverage/gap summary.
- `emergency-regex-gate.json`: 200-case emergency regex gate across VI/EN/KO/ZH and 10 surface styles.
- `product-gap-backlog.json`: prioritized product-gap groups derived from the deterministic direct probe.

For the hospitality evaluation probe, semantic generation and model-intent fallback were deliberately disabled because Ollama is unavailable in this environment. Therefore the Hospitality direct probe measures **deterministic coverage only** and must not be reported as full model-assisted product accuracy or as a Furama KPI.

Current local deterministic baseline after the hospitality evaluation safety hardening:

- 236 direct context-independent cases
- 104 passed (44.07%)
- VI 26/59, EN 25/59, KO 27/59, ZH 26/59
- emergency regex gate: 200/200 (50 per language)

The relatively low direct-set score is intentional as a gap-finding benchmark: The hospitality evaluation is broader and more hospitality-realistic than the production evaluation release smoke suite. It should not be made easier merely to increase the headline score.
