"""Retrieval scores are candidates, never permission to answer unrelated facets."""
from concierge_kiosk.rag.grounding.relevance import answerable
from concierge_kiosk.agent.understanding.commands import Command, validate_commands


def test_dense_score_cannot_certify_unrelated_compound_question():
    source = {'title': 'Montgomerie Links', 'content': 'Travel time from Da Nang: 10 minutes.',
              'fact_type': 'travel_time', 'dense_similarity': 0.99, 'language': 'en'}
    assert not answerable({}, 'Weather in Da Nang and airfare to Hanoi?', source,
                          language='en', dense_threshold=0.1)


def test_matching_entity_label_cannot_certify_an_unrepresented_aspect():
    source = {'title': 'Mountain View Golf', 'content': 'Mountain View Golf travel time: 10 minutes.',
              'fact_type': 'travel_time_min', 'dense_similarity': 0.99, 'language': 'en'}
    assert not answerable({}, 'Weather at Mountain View Golf?', source, language='en', dense_threshold=0.1)


def test_wrong_fact_type_fails_even_with_exact_subject_and_high_score():
    source = {'title': 'Pool', 'content': 'Pool capacity: 30 guests.',
              'fact_type': 'capacity', 'dense_similarity': 0.99}
    assert not answerable({'fact_types': ['opening_hours']}, 'Pool hours?', source,
                          language='en', dense_threshold=0.1)


def test_valid_hours_keep_recall():
    source = {'title': 'Pool', 'content': 'Pool opening hours: 06:00 - 22:00.',
              'fact_type': 'opening_hours'}
    assert answerable({'fact_types': ['opening_hours']}, 'Pool opening hours?', source,
                      language='en')


def test_cross_language_entity_and_facet_identity_preserve_recall():
    source = {'entity_id': 'pool', 'fact_type': 'opening_hours', 'language': 'en',
              'title': 'Swimming Pool', 'content': 'Opening hours: 06:00 - 22:00.'}
    assert answerable({'entity_ids': ['pool'], 'fact_types': ['opening_hours']},
                       '游泳池几点开门？', source, language='zh')
    assert not answerable({'entity_ids': ['spa'], 'fact_types': ['opening_hours']},
                           'Spa hours?', source, language='en')


def test_model_cannot_rewrite_a_read_into_an_unasked_topic():
    query = 'Weather in Da Nang and airfare to Hanoi?'
    commands = validate_commands((Command('AskInfo', query='Montgomerie Links travel time'),), query=query)
    assert commands[0].query == query


def test_composed_citations_point_at_their_own_final_claims():
    from concierge_kiosk.rag.grounding.citations import rebase_citations
    answer = 'Pool opens at 06:00.\nSpa opens at 09:00.'
    citations = [{'citation_id': 'C1', 'claim': claim, 'claim_start': 0, 'claim_end': len(claim),
                  'claim_spans': [{'claim_start': 0, 'claim_end': len(claim)}]}
                 for claim in answer.split('\n')]
    result = rebase_citations(answer, citations)
    assert [c['citation_id'] for c in result] == ['C1', 'C2']
    for citation in result:
        assert answer[citation['claim_start']:citation['claim_end']] == citation['claim']
        span = citation['claim_spans'][0]
        assert answer[span['claim_start']:span['claim_end']] == citation['claim']


def test_compound_reads_only_keep_each_questions_own_evidence(tmp_path, shipped_db, understand, monkeypatch):
    import shutil
    from fastapi.testclient import TestClient
    from concierge_kiosk.main import create_app
    from concierge_kiosk.core.settings import Settings
    from concierge_kiosk.application.conversation import answers
    from concierge_kiosk.rag.retrieval.policy import Retrieval

    shutil.copyfile(shipped_db, tmp_path / 'edge.sqlite3')
    real_retrieve = answers.retrieve
    seen = []
    def retrieve(*args, **kwargs):
        result = real_retrieve(*args, **kwargs)
        seen.append(kwargs['query'])
        unrelated = {'title': 'Montgomerie Links', 'heading': 'Travel time',
                     'content': 'Travel time from Da Nang: 10 minutes.', 'fact_type': 'travel_time',
                     'source_id': 'golf', 'chunk_id': 'golf', 'revision': 'r1', 'language': 'en',
                     'dense_similarity': 0.99}
        if 'Weather' in kwargs['query'] or 'airfare' in kwargs['query']:
            return Retrieval('hybrid', [unrelated], unrelated['content'])
        result.sources.append(unrelated)
        return result
    monkeypatch.setattr(answers, 'retrieve', retrieve)
    for query in ('What time does the swimming pool open? and Weather in Da Nang?',
                  'Weather in Da Nang? and airfare to Hanoi?'):
        clauses = query.split(' and ')
        understand(query, *(Command('AskInfo', query=clause,
            facet='hours' if 'pool' in clause else None) for clause in clauses))
    app = create_app(Settings(db_path=tmp_path / 'edge.sqlite3', property_id='FURAMA_DANANG',
                              property_timezone='Asia/Ho_Chi_Minh', environment='test'))
    with TestClient(app) as client:
        session = client.post('/api/session').json()
        headers = {'X-CSRF-Token': session['csrf_token']}
        for index, query in enumerate(('What time does the swimming pool open? and Weather in Da Nang?',
                                       'Weather in Da Nang? and airfare to Hanoi?')):
            response = client.post('/api/ask', headers=headers, json={'query': query, 'language': 'en'})
            assert response.status_code == 200, response.text
            body = response.json()
            assert 'Montgomerie' not in body['answer'] and '10 minutes' not in body['answer']
            assert all(s['source_id'] != 'golf' for s in body.get('sources', []))
            if index == 0:
                assert body['citations'], body
                assert '06:00' in body['answer'], body
                assert any(t['status'] == 'unavailable' for t in body['task_progress']), (body, seen)
                assert 'verified information' in body['answer'].lower(), body['answer']
            else:
                assert not body['citations'], body
