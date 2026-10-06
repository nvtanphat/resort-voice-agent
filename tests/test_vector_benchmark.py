from pathlib import Path

from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag.vectorstore import FaissVectorStore, VectorRecord
from tools.evaluation.evaluate_vector_backends import evaluate


def test_vector_benchmark_marks_self_retrieval_as_non_release_gate(tmp_path: Path):
    db = tmp_path / 'db.sqlite3'
    store = Store(db)
    with store.connection(write=True) as con:
        con.execute("INSERT INTO knowledge(id,revision,property_id,language,title,heading,body,search_text,source,classification,effective_from,embedding,embedding_model) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ('doc-a', 'r1', 'P', 'en', 'A', 'A', 'A', 'A', 's', 'public', '2026-01-01', '[1.0, 0.0]', 'probe'))
    vector_path = tmp_path / 'vectors'
    vector = FaissVectorStore(vector_path, name='P-knowledge')
    vector.upsert([VectorRecord('doc-a::r1', (1.0, 0.0), {
        'doc_id': 'doc-a', 'revision': 'r1', 'property_id': 'P', 'language': 'en',
        'classification': 'public', 'active': 1, 'embedding_model': 'probe',
        'effective_from': '2026-01-01', 'effective_to': '', 'release_version': 1})])
    vector.close()
    report = evaluate(db=db, property_id='P', backend='faiss', vector_path=vector_path,
                      sample_per_language=1)
    assert report['r_at_5'] == 1.0
    assert report['release_gate'] is False
