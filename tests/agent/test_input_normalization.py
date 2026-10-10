from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from concierge_kiosk.agent.understanding import normalization
from concierge_kiosk.agent.understanding.intent import normalize_intent_text
from concierge_kiosk.core.domain_profile import default_domain_profile_binding, load_domain_profile


@pytest.mark.parametrize('source, target', [
    ('ko', 'không'), ('khong', 'không'), ('hok', 'không'), ('khum', 'không'),
    ('dc', 'được'), ('đc', 'được'), ('j', 'gì'), ('ntn', 'như thế nào'),
])
def test_short_forms_expand_whole_tokens_with_auditable_edits(source, target):
    result = normalization.normalize_with_spans(source, 'vi')
    assert result.text == target
    assert [(edit.kind, edit.source, edit.replacement, edit.start, edit.end)
            for edit in result.edits] == [('short_form', source, target, 0, len(source))]


@pytest.mark.parametrize('source, canonical', [
    ('ko cần dọn phòng 305', 'không cần dọn phòng 305'),
    ('khum cần thêm khăn', 'không cần thêm khăn'),
    ('dọn phòng đc ko?', 'dọn phòng được không?'),
])
def test_short_forms_preserve_negation_and_question_meaning(source, canonical):
    assert normalize_intent_text(source, 'vi') == normalize_intent_text(canonical, 'vi')


@pytest.mark.parametrize('source', [
    'tôi cần khan tam phòng 305', 'tôi cần khan tắm phòng 305',
])
def test_mixed_accents_restore_catalog_phrases(source):
    result = normalization.normalize_with_spans(source, 'vi')
    assert result.text == 'tôi cần khăn tắm phòng 305'
    assert any(edit.kind == 'accent' and edit.replacement == 'khăn tắm' for edit in result.edits)


def test_expansion_does_not_disable_restoration_of_unaccented_source():
    source = 'ho boi mo cua luc may gio'
    value = normalize_intent_text(source + ' dc ko', 'vi')
    assert value == normalize_intent_text(source, 'vi') + ' được không'
    assert 'hồ bơi' in value


@pytest.mark.parametrize('source', [
    'à đổi sang 8 giờ nha', 'k dk vs', 'koko adc',
    'ko@example.com https://example.com/ko/j www.example.com/dc',
    'abc305 6am 3h 200usd 18:30', 'www.towles.com towles@example.com aaa305',
])
def test_correct_text_ambiguous_forms_and_identifiers_are_preserved(source):
    assert normalization.normalize_with_spans(source, 'vi').text == source


def test_typed_accents_outrank_catalog_spelling(monkeypatch):
    monkeypatch.setattr(normalization, '_mixed_phrase_matcher', lambda language: (
        normalization.re.compile(r'khan tam'), {'khan tam': 'khăn tắm'}))
    assert normalization.normalize_with_spans('khán tam', 'vi').text == 'khán tam'


@pytest.mark.parametrize('language', ['vi', 'en'])
def test_protected_tokens_do_not_enter_fuzzy_or_repeat_correction(language):
    source = 'towles@example.com https://example.com/towles aaa305'
    assert normalization.normalize_with_spans(source, language).text == source


@pytest.mark.parametrize('language', ['en', 'zh', 'ko', None])
def test_vietnamese_short_forms_do_not_affect_other_languages(language):
    assert normalization.normalize_with_spans('ko dc j', language).text == 'ko dc j'


def test_disabled_normalization_does_not_expand_forms(monkeypatch):
    monkeypatch.setattr(normalization, 'NORMALIZATION', {**normalization.NORMALIZATION, 'enabled': False})
    assert normalization.normalize_with_spans('ko dc', 'vi').text == 'ko dc'


@pytest.mark.parametrize('mutation', [
    lambda forms: forms.pop('short_forms'),
    lambda forms: forms.update(short_forms=[]),
    lambda forms: forms['short_forms']['vi'].update(ko=7),
    lambda forms: forms['short_forms']['vi'].update(ko='   '),
    lambda forms: forms['short_forms'].pop('vi'),
    lambda forms: forms['short_forms']['vi'].update({'KO': 'không'}),
    lambda forms: forms['short_forms']['vi'].update({'ko dc': 'không được'}),
])
def test_short_form_config_rejects_missing_or_invalid_values(tmp_path, mutation):
    path, _ = default_domain_profile_binding()
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    mutation(payload['nlu']['normalization'])
    target = tmp_path / 'domain.json'
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        load_domain_profile(target, checksum)


def test_short_form_config_loads_from_pinned_profile():
    path, checksum = default_domain_profile_binding()
    profile = load_domain_profile(path, checksum)
    assert profile.nlu.normalization['short_forms']['vi']['ko'] == 'không'
