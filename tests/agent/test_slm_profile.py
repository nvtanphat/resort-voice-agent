from concierge_kiosk.core.settings import Settings


def test_local_slm_candidates_use_3b_then_1_5b():
    cfg = Settings(
        llm_model="qwen2.5:3b",
        llm_fallback_model="qwen2.5:1.5b",
        local_ai_strict_mode=False,
    )
    assert cfg.llm_candidates() == ("qwen2.5:3b", "qwen2.5:1.5b")


def test_strict_local_ai_never_uses_unpinned_fallback():
    cfg = Settings(
        llm_model="qwen2.5:3b",
        llm_fallback_model="qwen2.5:1.5b",
        local_ai_strict_mode=True,
    )
    assert cfg.llm_candidates() == ("qwen2.5:3b",)
