"""_acompletion backs off and retries transient errors, but not deterministic ones."""

from __future__ import annotations

import asyncio

import litellm
import pytest

from styx_agent import agent


async def _noop(_seconds):  # replace asyncio.sleep so backoff is instant in tests
    return None


def test_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(agent.asyncio, "sleep", _noop)
    calls = {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise litellm.exceptions.InternalServerError("boom", model="m", llm_provider="p")
        return "RESP"

    monkeypatch.setattr(agent.litellm, "acompletion", flaky)
    assert asyncio.run(agent._acompletion("t")) == "RESP"
    assert calls["n"] == 3


def test_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(agent.asyncio, "sleep", _noop)
    calls = {"n": 0}

    async def always_down(**kwargs):
        calls["n"] += 1
        raise litellm.exceptions.ServiceUnavailableError("down", model="m", llm_provider="p")

    monkeypatch.setattr(agent.litellm, "acompletion", always_down)
    with pytest.raises(litellm.exceptions.ServiceUnavailableError):
        asyncio.run(agent._acompletion("t"))
    assert calls["n"] == agent._MAX_COMPLETION_ATTEMPTS  # bounded, no infinite hammering


def test_does_not_retry_deterministic_error(monkeypatch):
    monkeypatch.setattr(agent.asyncio, "sleep", _noop)
    calls = {"n": 0}

    async def bad_request(**kwargs):
        calls["n"] += 1
        raise litellm.exceptions.BadRequestError("nope", model="m", llm_provider="p")

    monkeypatch.setattr(agent.litellm, "acompletion", bad_request)
    with pytest.raises(litellm.exceptions.BadRequestError):
        asyncio.run(agent._acompletion("t"))
    assert calls["n"] == 1  # propagated immediately, not retried
