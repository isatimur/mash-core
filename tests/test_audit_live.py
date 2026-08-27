"""LIVE audit test — contract DoD item 1 (mash-core leg).

Marked ``live`` so it is excluded from the default run (``-m 'not live'``).
Requires a real key. Run once a key exists, e.g.:

    ANTHROPIC_API_KEY=... poetry run pytest -m live mash-core/tests/test_audit_live.py

It makes ONE real Anthropic call through ``audited_agent_run`` and cross-checks
the audit record's token counts against the provider's own response metadata
(``result.usage()``), which is what DoD item 1 requires and what a mock cannot
supply.
"""

import json
import os

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent

import mash_core.audit as audit
from mash_core.audit import audited_agent_run
from mash_core.model_factory import build_judge_model

pytestmark = pytest.mark.live


class _Out(BaseModel):
    score: int
    note: str


async def test_live_call_audit_matches_provider_metadata(tmp_path, monkeypatch):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("no ANTHROPIC_API_KEY")
    monkeypatch.setenv("LLM_AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_AUDIT_REPO", "mash-core")
    monkeypatch.setenv("AUDIT_WRAPPER_ENABLED", "1")

    model, model_id = build_judge_model()
    agent = Agent(model=model, result_type=_Out,
                  system_prompt="Return score=42 and note='hi'.")
    result = await audited_agent_run(agent, "Score this.", model_id=model_id)
    usage = result.usage()

    rec = json.loads((tmp_path / audit.AUDIT_LOG_NAME).read_text().splitlines()[0])
    assert rec["tokens_in"] == usage.request_tokens
    assert rec["tokens_out"] == usage.response_tokens
    assert rec["model"] == model_id
    assert rec["provider"] == "anthropic"
    assert rec["repo"] == "mash-core"
