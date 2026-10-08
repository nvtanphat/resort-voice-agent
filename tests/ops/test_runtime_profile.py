from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from concierge_kiosk.core.runtime_profile import load_runtime_profile
from concierge_kiosk.core.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "config" / "runtime-profiles"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_all_shipped_runtime_profiles_are_pinned_and_valid():
    expected = {"test", "development", "edge", "production"}
    for name in expected:
        path = PROFILES / f"{name}.json"
        sidecar = PROFILES / f"{name}.sha256"
        assert sidecar.read_text(encoding="ascii").strip() == _sha(path)
        profile = load_runtime_profile(str(path), _sha(path))
        assert profile.profile_id == name
        assert profile.schema_version == 1
        rag = profile.budgets["rag"]
        assert 2 <= rag["rerank_top_k"] <= 10
        assert 32 <= rag["rerank_max_length"] <= 512
        assert rag["rerank_input"] in {"context_text", "body"}
        assert 0 <= rag["rerank_fusion_alpha"] <= 1
        assert 0 <= rag["rerank_metadata_bonus"] <= 1
        assert rag["rerank_on_failure"] in {"keep_rrf", "abstain"}


def test_rerank_policy_validation_rejects_out_of_range_values():
    with pytest.raises(ValueError, match="Invalid RAG policy thresholds"):
        Settings(rag_rerank_fusion_alpha=1.5).validate()
    with pytest.raises(ValueError, match="Invalid reranker input field"):
        Settings(rag_rerank_input="title").validate()


def test_runtime_profile_sha_mismatch_fails_closed():
    path = PROFILES / "development.json"
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_runtime_profile(str(path), "0" * 64)


def test_development_embedding_strategy_selects_installed_local_candidate():
    path = PROFILES / "development.json"
    profile = load_runtime_profile(str(path), _sha(path))
    model, manifest = profile.embedding_assets()
    # Development prefers the repo's pinned bge-m3 (served by local Ollama) and
    # falls back to the bundled hash embedder when that manifest is absent.
    assert model in {"ollama://bge-m3", "./models/embeddings/hash-multilingual"}
    assert manifest.endswith((".ollama.manifest.json", "hash-multilingual.manifest.json"))


def test_edge_profile_changes_models_budgets_and_timeouts_without_source_edit():
    path = PROFILES / "edge.json"
    profile = load_runtime_profile(str(path), _sha(path))
    assert profile.models["slm"]["primary_model"] == "qwen2.5:1.5b"
    assert profile.models["slm"]["fallback_model"] == ""
    assert profile.budgets["agent"] == {
        "max_steps": 10,
        "max_wall_time_ms": 15000,
        "max_planner_calls": 6,
        "max_read_calls": 6,
    }
    assert profile.timeouts["agent_planner_seconds"] == 5.0


def test_load_settings_uses_profile_then_env_overrides_in_fresh_process():
    edge = PROFILES / "edge.json"
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "CONCIERGE_ENV": "development",
        "CONCIERGE_RUNTIME_PROFILE_PATH": str(edge),
        "CONCIERGE_RUNTIME_PROFILE_SHA256": _sha(edge),
        "CONCIERGE_LLM_MODEL": "operator-override:1b",
        "CONCIERGE_AGENT_MAX_STEPS": "6",
        "CONCIERGE_AGENT_MAX_PLANNER_CALLS": "4",
        "CONCIERGE_AGENT_MAX_READ_CALLS": "5",
        "CONCIERGE_AGENT_PLANNER_ENABLED": "false",
        "CONCIERGE_STATUS_TOKEN_SECRET": "",
    })
    code = """
import json
from concierge_kiosk.core.settings import load_settings
cfg = load_settings()
print(json.dumps({
  'profile': cfg.runtime_profile_id,
  'model': cfg.llm_model,
  'fallback': cfg.llm_fallback_model,
  'steps': cfg.agent_max_steps,
  'planner_calls': cfg.agent_max_planner_calls,
  'read_calls': cfg.agent_max_read_calls,
  'planner_enabled': cfg.agent_planner_enabled,
  'planner_timeout': cfg.agent_planner_timeout_seconds,
  'status_secret_len': len(cfg.status_token_secret),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, check=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "profile": "edge",
        "model": "operator-override:1b",
        "fallback": "",
        "steps": 6,
        "planner_calls": 4,
        "read_calls": 5,
        "planner_enabled": False,
        "planner_timeout": 5.0,
        "status_secret_len": 42,
    }



def test_named_profile_switch_uses_its_own_pinned_sidecar_in_fresh_process():
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "CONCIERGE_ENV": "development",
        "CONCIERGE_RUNTIME_PROFILE": "edge",
    })
    env.pop("CONCIERGE_RUNTIME_PROFILE_PATH", None)
    env.pop("CONCIERGE_RUNTIME_PROFILE_SHA256", None)
    code = """
from concierge_kiosk.core.settings import load_settings
cfg = load_settings()
print(f"{cfg.runtime_profile_id}|{cfg.llm_model}|{cfg.agent_max_steps}|{cfg.agent_planner_timeout_seconds}")
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "edge|qwen2.5:1.5b|10|5.0"


def test_invalid_boolean_env_override_fails_closed_in_fresh_process():
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "CONCIERGE_ENV": "development",
        "CONCIERGE_RUNTIME_PROFILE": "development",
        "CONCIERGE_AGENT_PLANNER_ENABLED": "sometimes",
    })
    env.pop("CONCIERGE_RUNTIME_PROFILE_PATH", None)
    env.pop("CONCIERGE_RUNTIME_PROFILE_SHA256", None)
    code = "from concierge_kiosk.core.settings import load_settings; load_settings()"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "CONCIERGE_AGENT_PLANNER_ENABLED must be true or false" in result.stderr


def test_nested_pydantic_settings_override_is_applied_in_fresh_process():
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "CONCIERGE_ENV": "development",
        "CONCIERGE_RUNTIME_PROFILE": "development",
        "CONCIERGE_VOICE_SLM_CAPS__REFERENCE": "2.25",
    })
    code = "from concierge_kiosk.core.settings import load_settings; print(load_settings().voice_slm_caps['reference'])"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "2.25"


def test_rerank_env_override_is_applied_in_fresh_process():
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "CONCIERGE_ENV": "development",
        "CONCIERGE_RUNTIME_PROFILE": "development",
        "CONCIERGE_RAG_RERANK_TOP_K": "6",
    })
    code = "from concierge_kiosk.core.settings import load_settings; print(load_settings().rag_rerank_top_k)"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "6"


def test_production_rejects_runtime_profile_environment_override():
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "CONCIERGE_ENV": "production",
        "CONCIERGE_RUNTIME_PROFILE": "production",
        "CONCIERGE_LLM_MODEL": "operator-override:1b",
    })
    code = "from concierge_kiosk.core.settings import load_settings; load_settings()"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "Production forbids environment overrides" in result.stderr

def test_production_profile_is_strict_and_has_no_fallback():
    path = PROFILES / "production.json"
    profile = load_runtime_profile(str(path), _sha(path))
    assert profile.models["slm"]["strict_mode"] is True
    assert profile.models["slm"]["fallback_model"] == ""
    assert profile.features["semantic_require_independent_nli"] is True
    assert profile.models["nli"]["require_manifest"] is True
    assert profile.models["voice"]["incremental_require_manifest"] is True
    assert profile.models["voice"]["final_require_manifest"] is True
