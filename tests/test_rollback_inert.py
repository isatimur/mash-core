"""Rollback flag inertness — contract DoD item 4.

With AUDIT_WRAPPER_ENABLED=0 the wrapper must forward to the original call and
touch nothing: no log files, no marker, no directory creation.
"""

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import mash_core.audit as audit
from mash_core.audit import audited_agent_run, record_call


class _Out(BaseModel):
    score: int
    note: str


@pytest.fixture(autouse=True)
def _disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_WRAPPER_ENABLED", "0")
    monkeypatch.setenv("LLM_AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_AUDIT_REPO", "mash-core")
    yield


async def test_disabled_agent_run_still_returns_result(tmp_path):
    agent = Agent(model=TestModel(), result_type=_Out, system_prompt="sys")
    result = await audited_agent_run(agent, "prompt", model_id="claude-sonnet-4-6")
    assert isinstance(result.data, _Out)
    # nothing written
    assert list(tmp_path.iterdir()) == []


def test_disabled_record_call_is_noop(tmp_path):
    record_call(prompt="bob@corp.io", response="x", model_id="claude-sonnet-4-6",
                tokens_in=1, tokens_out=1)
    assert not (tmp_path / audit.AUDIT_LOG_NAME).exists()
    assert not (tmp_path / audit.PII_LOG_NAME).exists()
    assert not (tmp_path / audit.MARKER_NAME).exists()
    assert list(tmp_path.iterdir()) == []
