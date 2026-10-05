# Dataset layout

The repository has one canonical dataset root: `datasets/`. Runtime code resolves
its files through `src/concierge_kiosk/core/dataset_layout.py`; tools and tests
should use the same resolver instead of property-specific paths.

## Canonical knowledge

```text
datasets/knowledge/canonical/
datasets/knowledge/sources/
datasets/knowledge/manifest.json
```

Facts, entities, aliases, service catalog, planning, map, contacts and
departments are verified property knowledge. Source artifacts and snapshots are
kept beside the canonical records for provenance.

## Synthetic operations

```text
datasets/synthetic/operations/
```

This tree contains prototype policies, staffing, inventory, operational history
and simulation data. It is explicitly non-canonical and must not grant runtime
authority or be presented as an official property fact.

## Evaluation and training

```text
datasets/evaluation/
datasets/training/
```

Evaluation corpora are separate from RAG inputs. The end-to-end production and
hospitality suites, retrieval holdouts and voice-text robustness data are
synthetic evaluation artifacts unless their schema says otherwise.

## Knowledge releases and database

`knowledge/approved/` and `knowledge/compiled/` are derived editorial/runtime
documents. `releases/` contains pinned map, planning and property releases.
`data/concierge.sqlite3` is a runtime artifact; release metadata and evidence in
the database must agree with the compiled knowledge that was loaded.

## Validation

After changing data, run schema validation, semantic validation, the synthetic
and evaluation validators, then the relevant regression tests. Do not create a
second flat dataset tree to make an old tool pass.
