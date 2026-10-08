"""Tests whose behaviour was removed with the keyword logic and returns with a rebuild step.

Each entry is ``(node-id prefix, rebuild step)``.  The step names refer to the
rebuild list in plan.md / the Phase B plan:

* B1 facet     - facet taken from the validated AskInfo/Navigate command, mapped to fact types
* B2 follow-up - conversation state (anchor, pending question) in the command prompt
* B3 plan      - Plan command slots and topics
* B5 voice     - spoken-unit/symbol rendering that 26196a6 removed from the voice profile
* B6 accents   - restoring accents on ambiguous accent-less words needs context, not a phrase list

``xfail(strict=True)``: when a step lands, the test XPASSes and the run fails until the
entry is deleted here, so this list can only shrink.  ``COLLECT_IGNORE`` holds modules
that import code the removal deleted; fix the import or delete the test, then drop it.
"""
from __future__ import annotations

REBUILD_PENDING: tuple[tuple[str, str], ...] = (
    ("tests/agent/test_conversation_memory_followups.py::test_cross_language_followup_uses_target_language_map_label", "B2"),
    ("tests/agent/test_conversation_memory_followups.py::test_english_where_is_it_with_question_mark_is_a_followup", "B2"),
    ("tests/agent/test_conversation_memory_followups.py::test_followup_after_map_only_answer_uses_the_place", "B2"),
    ("tests/agent/test_conversation_memory_followups.py::test_where_is_it_after_hours_question_returns_localized_route", "B2"),
    ("tests/agent/test_multilingual_conversation_e2e.py::test_directions_use_guest_wording_and_never_fail_without_evidence", "B2"),
    ("tests/agent/test_multilingual_conversation_e2e.py::test_mixed_language_conversation_stays_grounded", "B2"),
    ("tests/agent/test_multilingual_hours.py::test_p0_4_breakfast_hours_are_recalled_in_guest_language", "B1"),
    ("tests/agent/test_multilingual_hours.py::test_p0_4_pool_close_hours_are_recalled_in_guest_language[en-What time does the pool close?-Schedule]", "B1"),
    ("tests/rag/test_rag_golden_queries.py::RagGoldenQueryTests::test_cafe_hours_rank_correct_fact_for_all_languages_in_hybrid_mode", "B1"),
    ("tests/rag/test_rag_golden_queries.py::test_p0_3_broken_air_conditioner_abstains_instead_of_using_gym_equipment", "B1"),
    ("tests/rag/test_rag_golden_queries.py::test_p0_3_vietnamese_late_checkout_does_not_use_ceiling_height", "B1"),
    ("tests/rag/test_retrieval_recall_safety.py::RetrievalRecallSafetyTests::test_location_question_cannot_use_extension_or_policy_fact", "B1"),
    ("tests/agent/test_no_evidence_recovery.py::test_planning_no_evidence_with_related_topics_returns_recovery_answer", "B3"),
    ("tests/agent/test_service_nlu_recall.py::test_dining_readback_resolves_one_named_restaurant_from_dataset", "B2"),
    ("tests/agent/test_nlu_robustness.py::test_profile_terms_restore_operating_hours_phrase_without_hardcoded_python_vocabulary", "B6"),
    ("tests/voice/test_voice_latency_optimizations.py::test_vietnamese_speech_rendering_normalizes_phone_symbols_units_and_email", "B5"),
)

COLLECT_IGNORE: tuple[str, ...] = (
)
