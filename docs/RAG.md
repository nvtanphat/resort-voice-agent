# RAG and grounding

RAG uses compiled knowledge derived from the canonical dataset:

```text
datasets/knowledge/canonical/
knowledge/approved/furama/
knowledge/compiled/furama/
```

`datasets/synthetic/` and `datasets/evaluation/` are not RAG truth and must not
be silently included in retrieval.

The retrieval pipeline combines lexical FTS, language/property/date filtering,
dense vectors, reciprocal-rank fusion and optional reranking. Citation binding
and conflict/abstention checks run before an answer is exposed to a guest.

Embeddings are release artifacts. The running embedder must match the pinned
model recorded in the runtime database; otherwise dense retrieval is unavailable
and readiness must report that condition.

When knowledge changes, preserve this chain:

```text
source artifact
 -> schema and semantic validation
 -> canonical facts/entities
 -> localized compilation
 -> chunk/index rebuild
 -> release evidence and embedding audit
 -> retrieval and conversation regression tests
```

Evaluation results are measurements of the product/evaluation corpus, not hotel
SLAs or historical property KPIs.
