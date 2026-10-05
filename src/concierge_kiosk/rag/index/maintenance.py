"""Explicit operator-only FTS5 verification and repair."""
from __future__ import annotations
from concierge_kiosk.persistence.sqlite_store import Store

def validate_knowledge_index(store: Store) -> dict[str, int]:
    """Detect missing, stale, mismatched and duplicated FTS rows; read-only."""
    with store.connection() as con:
        documents = con.execute('SELECT count(*) FROM knowledge').fetchone()[0]
        indexed = con.execute('SELECT count(*) FROM knowledge_fts').fetchone()[0]
        bad = con.execute('''
            SELECT count(*) FROM knowledge_fts f
            LEFT JOIN knowledge k ON k.id=f.doc_id AND k.revision=f.revision
            WHERE k.id IS NULL OR k.search_text != f.search_text
        ''').fetchone()[0]
        missing = con.execute('''
            SELECT count(*) FROM knowledge k
            WHERE NOT EXISTS (
                SELECT 1 FROM knowledge_fts f
                WHERE f.doc_id=k.id AND f.revision=k.revision AND f.search_text=k.search_text
            )
        ''').fetchone()[0]
        orphaned_parents = con.execute("""SELECT count(*) FROM knowledge k
            LEFT JOIN knowledge_parents p ON p.id=k.parent_id AND p.revision=k.revision
            AND p.property_id=k.property_id AND p.language=k.language
            WHERE k.parent_id!='' AND (p.id IS NULL OR p.source!=k.source
              OR p.heading!=k.heading OR p.section_id!=k.section_id
              OR p.section_ordinal!=k.section_ordinal OR p.domain!=k.domain
              OR p.classification!=k.classification OR p.active!=k.active)""").fetchone()[0]
    if bad or missing or indexed != documents:
        raise RuntimeError('Knowledge FTS index mismatch; run offline index repair')
    if orphaned_parents:
        raise RuntimeError('Knowledge parent index mismatch; rebuild knowledge from signed snapshot')
    return {'knowledge_chunks': documents, 'indexed_chunks': indexed}


def rebuild_knowledge_index(store: Store) -> dict[str, int]:
    """Explicit local operator repair; never callable from guest API."""
    with store.connection(write=True) as con:
        con.execute('DELETE FROM knowledge_fts')
        con.execute('''INSERT INTO knowledge_fts(doc_id,revision,search_text)
                       SELECT id,revision,search_text FROM knowledge''')
    return validate_knowledge_index(store)
