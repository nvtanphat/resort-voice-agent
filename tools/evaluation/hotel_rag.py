"""Evaluate a *separate operator-attested* hotel corpus and adjudicated QA labels.

No demo corpus or synthetic fixture may be substituted for field evidence. This
measures retrieval/abstention against operator-supplied labels, not independent
certification of the hotel's approval or factual/semantic answer correctness.
Do not place real guest prompts in source control or emitted evaluation reports.

python -m tools.evaluation.hotel_rag --corpus /secure/hotel/approved-md \
 --approval /secure/hotel/approval.json --cases /secure/hotel/reviewed.jsonl \
 --report /secure/reports/hotel-rag.json --min-per-language 20 --top-k 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import tempfile
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag import LocalEmbedder, LocalReranker, ingest_text, retrieve, validate_knowledge_index
from concierge_kiosk.rag.citations import bind_citations
from concierge_kiosk.rag.retrieval import abstention_answer

LANGUAGES = ('vi', 'en', 'zh', 'ko')

def corpus_digest(files: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.relative_to(root).as_posix().encode('utf-8'))
        digest.update(b'\0')
        digest.update(file.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def checked_inputs(corpus: Path, approval_path: Path, cases: Path, *, min_per_language: int):
    if min_per_language < 1:
        raise ValueError('Minimum per-language cases must be positive')
    corpus = corpus.resolve()
    if not corpus.is_dir() or approval_path.is_symlink() or cases.is_symlink():
        raise ValueError('External corpus and ordinary approval/label files required')
    files = sorted(corpus.rglob('*.md'))
    if not files or any(p.is_symlink() or not p.is_file() for p in files):
        raise ValueError('No ordinary markdown knowledge files available')
    checksum = corpus_digest(files, corpus)
    approval = json.loads(approval_path.read_text(encoding='utf-8'))
    property_id = approval.get('property_id')
    when = datetime.fromisoformat(str(approval.get('approved_at_utc', '')).replace('Z','+00:00'))
    if (approval.get('approval_status') != 'approved' or approval.get('source') != 'hotel_authorized'
            or not isinstance(property_id, str) or not property_id.strip() or property_id == 'UNCONFIGURED'
            or not str(approval.get('reviewed_by', '')).strip() or when.tzinfo is None
            or when.utcoffset() != timezone.utc.utcoffset(when)
            or approval.get('knowledge_release_sha256') != checksum):
        raise ValueError('Hotel operator attestation and exact corpus digest are required')
    rows = []
    identifiers = set()
    counts = defaultdict(int)
    for number, line in enumerate(cases.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        case_id = row.get('case_id')
        language = row.get('language')
        refs = row.get('expected_source_ids')
        abstain = row.get('expected_abstain')
        if (not isinstance(case_id, str) or len(case_id) < 4 or case_id in identifiers
                or language not in LANGUAGES or row.get('property_id') != property_id
                or row.get('knowledge_release_sha256') != checksum
                or row.get('review_status') != 'human_adjudicated'
                or not isinstance(row.get('query'), str) or not 0 < len(row['query']) <= 500
                or type(abstain) is not bool or not isinstance(refs, list)
                or any(not isinstance(s, str) or not s for s in refs) or len(refs) != len(set(refs))
                or (abstain and refs) or (not abstain and not refs)):
            raise ValueError(f'Invalid reviewed case {number} or release mismatch')
        identifiers.add(case_id)
        counts[language] += 1
        rows.append(row)
    if any(counts[lang] < min_per_language for lang in LANGUAGES):
        raise ValueError('Insufficient independently reviewed labels for all four languages')
    return files, checksum, property_id, rows


def evaluate(corpus: Path, approval: Path, cases: Path, *, min_per_language: int = 20,
             top_k: int = 5, embedding_model_path: str = '', embedding_manifest_path: str = '',
             reranker_model_path: str = '', reranker_manifest_path: str = '') -> dict:
    effective_date = date.today().isoformat()
    if not 1 <= top_k <= 10 or (reranker_model_path and not embedding_model_path):
        raise ValueError('Invalid retrieval mode or top_k')
    files, checksum, property_id, rows = checked_inputs(corpus, approval, cases,
                                                         min_per_language=min_per_language)
    embedder = LocalEmbedder(embedding_model_path, embedding_manifest_path) if embedding_model_path else None
    reranker = LocalReranker(reranker_model_path, reranker_manifest_path) if reranker_model_path else None
    with tempfile.TemporaryDirectory(prefix='hotel-rag-field-') as directory:
        store = Store(Path(directory)/'eval.sqlite3')
        for file in files:
            ingest_text(store, file.read_text(encoding='utf-8'), property_id=property_id,
                        embedder=embedder)
        validate_knowledge_index(store)
        group = defaultdict(lambda: {'cases':0,'positive':0,'hits':0,'reciprocal_rank_sum':0.0,
                                     'negative':0,'abstained':0,'citation_exact':0,'authorization_safe':0})
        latencies, failures = [], []
        for row in rows:
            start = time.perf_counter()
            result = retrieve(store, property_id=property_id, language=row['language'],
                              query=row['query'], top_k=top_k, embedder=embedder,
                              reranker=reranker, mode='hybrid' if embedder else 'lexical', effective_date=effective_date)
            latencies.append((time.perf_counter()-start)*1000)
            ranked_ids = [source['source_id'] for source in result.sources]
            ids = set(ranked_ids)
            group_row = group[row['language']]
            group_row['cases'] += 1
            binding = bind_citations(store, property_id=property_id, language=row['language'],
                                     answer=result.answer, sources=result.sources,
                                     effective_date=effective_date)
            if row['expected_abstain']:
                group_row['negative'] += 1
                if not ids:
                    group_row['abstained'] += 1
                    if not binding.citations and result.answer == abstention_answer(row['language']):
                        group_row['authorization_safe'] += 1
                else:
                    failures.append(row['case_id'])
            else:
                group_row['positive'] += 1
                expected=set(row['expected_source_ids'])
                hit=bool(ids.intersection(expected))
                if hit:
                    group_row['hits'] += 1
                    first_rank = next(index for index, source_id in enumerate(ranked_ids, 1)
                                      if source_id in expected)
                    group_row['reciprocal_rank_sum'] += 1.0 / first_rank
                else:
                    failures.append(row['case_id'])
                citations=binding.citations
                if (hit and citations and all(c.get('source_id') in expected and c.get('quote')
                                              for c in citations)):
                    group_row['citation_exact'] += 1
                    group_row['authorization_safe'] += 1
        counts = {key: sum(item[key] for item in group.values())
                  for key in ('cases','positive','hits','negative','abstained','citation_exact','authorization_safe')}
        reciprocal_rank_sum = sum(item['reciprocal_rank_sum'] for item in group.values())
        recall = counts['hits']/counts['positive'] if counts['positive'] else 0.0
        mrr = reciprocal_rank_sum/counts['positive'] if counts['positive'] else 0.0
        citation_exactness = counts['citation_exact']/counts['positive'] if counts['positive'] else 0.0
        authorization = counts['authorization_safe']/counts['cases'] if counts['cases'] else 0.0
        acceptance = {
            'recall_at_5_gte_0_90': top_k == 5 and recall >= 0.90,
            'citation_exactness_eq_1_00': citation_exactness == 1.0,
            'unsupported_claim_authorization_eq_1_00': authorization == 1.0,
        }
        latencies.sort()
        return {'type':'operator_attested_field_retrieval_not_hotel_certification',
                'knowledge_release_sha256':checksum,
                'cases_manifest_sha256':hashlib.sha256(cases.read_bytes()).hexdigest(),
                'mode':'hybrid' if embedder else 'lexical',
                'reranked':bool(reranker), 'top_k':top_k,
                'document_count':len(files), 'case_count':counts['cases'],
                'positive_cases':counts['positive'], 'negative_cases':counts['negative'],
                'hit_rate_at_k':round(recall,6),
                'recall_at_5':round(recall,6) if top_k == 5 else None,
                'mrr_at_k':round(mrr,6),
                'mrr_at_5':round(mrr,6) if top_k == 5 else None,
                'embedding_model':embedder.model_name if embedder else None,
                'citation_exactness':round(citation_exactness,6),
                'unsupported_claim_authorization':round(authorization,6),
                'acceptance':acceptance, 'passed':all(acceptance.values()),
                'abstention_accuracy':round(counts['abstained']/counts['negative'],6) if counts['negative'] else None,
                'latency_p50_ms':round(statistics.median(latencies),2),
                'latency_p95_ms':round(latencies[math.ceil(.95*len(latencies))-1],2),
                'per_language':{lang:dict(group[lang]) for lang in LANGUAGES},
                'failure_case_ids':failures,
                'note':'Operator attestation is not cryptographic approval verification. Human semantic answer review is separate; do not claim LLM answer correctness.'}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('corpus','approval','cases','report'):
        p.add_argument('--'+key, type=Path, required=True)
    p.add_argument('--min-per-language', type=int, default=20)
    p.add_argument('--top-k', type=int, default=5)
    p.add_argument('--embedding-model-path', default='')
    p.add_argument('--embedding-manifest-path', default='')
    p.add_argument('--reranker-model-path', default='')
    p.add_argument('--reranker-manifest-path', default='')
    args = p.parse_args()
    report = evaluate(args.corpus, args.approval, args.cases,
                      min_per_language=args.min_per_language, top_k=args.top_k,
                      embedding_model_path=args.embedding_model_path,
                      embedding_manifest_path=args.embedding_manifest_path,
                      reranker_model_path=args.reranker_model_path,
                      reranker_manifest_path=args.reranker_manifest_path)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('type','case_count','recall_at_5','mrr_at_5','citation_exactness','unsupported_claim_authorization','passed','failure_case_ids')},indent=2))
    return 0 if report['passed'] else 2


if __name__=='__main__':
    raise SystemExit(main())
