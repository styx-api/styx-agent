"""Generic agent loop used by scan and explorer agents."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from typing import cast

import litellm

from styx_agent.telemetry import AgentStat, record_agent
from styx_agent.tools.filesystem import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("STYX_AGENT_MODEL", "neurodesk/kimi-k2.7")
MAX_TURNS = 40
# When this few tool-call turns remain, start telling the model its budget so it
# can prioritize and converge on its own instead of being cut off mid-trace.
TURN_WARNING_THRESHOLD = 5

# Neurodesk hosts an OpenAI-compatible LiteLLM gateway. We expose its models
# under a `neurodesk/` prefix (e.g. `neurodesk/kimi-k2.7`, the default) so a
# model can be selected with --model / STYX_AGENT_MODEL and routed to Neurodesk
# without hijacking the generic OPENAI_* env vars. Set STYX_AGENT_MODEL to any
# other LiteLLM model string (e.g. `litellm_proxy/...`) to use a different provider.
NEURODESK_API_BASE = os.environ.get(
    "NEURODESK_API_BASE", "https://llm.neurodesk.org/openai"
)


def resolve_model(model: str) -> tuple[str, dict]:
    """Map a model string to (litellm_model, extra acompletion kwargs).

    A `neurodesk/<name>` alias is routed to LiteLLM's OpenAI-compatible path
    against the Neurodesk gateway, authenticated with ``NEURODESK_KEY``. Any
    other model string is passed through untouched.
    """
    prefix = "neurodesk/"
    if model.startswith(prefix):
        key = os.environ.get("NEURODESK_KEY")
        if not key:
            raise RuntimeError(
                "NEURODESK_KEY is not set; required for neurodesk/ models"
            )
        return f"openai/{model[len(prefix):]}", {
            "api_base": NEURODESK_API_BASE,
            "api_key": key,
        }
    return model, {}


# Transient errors worth backing off and retrying. Deterministic failures
# (bad request, auth, not-found) are deliberately excluded — retrying those just
# wastes calls. Backing off on these slows our request rate when the endpoint is
# struggling instead of piling on, and rides out brief flukes.
_RETRYABLE_ERRORS = (
    litellm.exceptions.RateLimitError,
    litellm.exceptions.InternalServerError,
    litellm.exceptions.ServiceUnavailableError,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.Timeout,
)
_BACKOFF_CAP_S = 600  # cap backoff at ~10 min so a long outage is polled gently
_DEFAULT_MAX_RETRY_SECONDS = 24 * 3600  # keep retrying a single call for up to this long


_DEFAULT_REQUEST_TIMEOUT_S = 300  # per-call ceiling so a hung socket can't block forever


def _max_retry_seconds() -> float:
    """Total time to keep retrying one call before giving up; ``<= 0`` = forever.

    Generous by default so an unattended overnight/weekend run survives a
    multi-hour endpoint outage. Override with ``STYX_AGENT_MAX_RETRY_SECONDS``.
    """
    return float(os.environ.get("STYX_AGENT_MAX_RETRY_SECONDS", str(_DEFAULT_MAX_RETRY_SECONDS)))


def _request_timeout() -> float:
    """Per-request timeout (seconds); ``<= 0`` disables it.

    Without a timeout a half-closed connection (server gone, client still reading)
    blocks the call forever — and the retry backoff never fires, because it only
    triggers on a raised exception. Bounding each call turns a silent hang into a
    ``litellm.Timeout``, which IS retryable, so the backoff can ride it out. Set
    via ``STYX_AGENT_REQUEST_TIMEOUT``.
    """
    return float(os.environ.get("STYX_AGENT_REQUEST_TIMEOUT", str(_DEFAULT_REQUEST_TIMEOUT_S)))


async def _acompletion(label: str, **kwargs) -> litellm.ModelResponse:
    """``litellm.acompletion`` with jittered exponential backoff on transient errors.

    Retries only transient errors (rate limits, 5xx, timeouts, connection drops),
    never deterministic ones. Backoff grows to a ~10 min cap and keeps going for up
    to ``STYX_AGENT_MAX_RETRY_SECONDS`` (default 24h; 0 = forever), so a single
    multi-hour outage during an unattended run is ridden out rather than failing
    the tool. Each wait slows our request rate while the endpoint struggles; jitter
    desynchronizes concurrent agents. On a permanent outage the window eventually
    elapses and we give up (caller records the tool failed, campaign continues).
    We never stream, so the result is always a ``ModelResponse``.
    """
    window = _max_retry_seconds()
    deadline = None if window <= 0 else time.monotonic() + window
    timeout = _request_timeout()
    if timeout > 0:
        kwargs.setdefault("timeout", timeout)
    attempt = 0
    while True:
        try:
            return cast(litellm.ModelResponse, await litellm.acompletion(**kwargs))
        except _RETRYABLE_ERRORS as e:
            base = min(2 ** attempt * 5, _BACKOFF_CAP_S)
            wait = base + random.uniform(0, base * 0.25)  # jitter desynchronizes retries
            attempt += 1
            if deadline is not None and time.monotonic() + wait > deadline:
                logger.warning(
                    f"[{label}] {type(e).__name__}: retry window exhausted after "
                    f"{attempt} attempt(s), giving up"
                )
                raise
            logger.warning(
                f"[{label}] {type(e).__name__}; backing off {wait:.0f}s (attempt {attempt})"
            )
            await asyncio.sleep(wait)


def _add_usage(response, prompt_tokens: int, completion_tokens: int) -> tuple[int, int]:
    """Accumulate token usage from a completion response (usage may be absent)."""
    usage = getattr(response, "usage", None)
    if usage is not None:
        prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens += getattr(usage, "completion_tokens", 0) or 0
    return prompt_tokens, completion_tokens


_DEFAULT_TOOL_RESULT_BUDGET = 60_000
_ELIDED_MARKER = "[earlier tool output elided to save context]"


def _tool_result_budget() -> int:
    """Char budget for retained tool-result content; 0 disables compaction."""
    return int(os.environ.get("STYX_AGENT_TOOL_RESULT_BUDGET", str(_DEFAULT_TOOL_RESULT_BUDGET)))


def _compact_tool_results(messages: list[dict], budget: int) -> None:
    """Stub old tool-result content so it isn't re-sent every turn.

    Tool results (file reads, greps) otherwise accumulate and get re-billed once
    per remaining turn, making a long tracing run grow ~quadratically in tokens.
    Walking newest-first, the most recent results are kept in full up to
    ``budget`` characters; older ones have their content replaced by a short
    stub. Only ``tool`` messages are touched — the system prompt, the task, and
    the agent's own assistant turns (where its findings live) are untouched, and
    the stub names nothing it can't re-read from disk. Idempotent via the stub
    sentinel; structure-preserving (content is shortened, messages never removed,
    so each result still pairs with its assistant ``tool_call_id``).
    """
    if budget <= 0:
        return
    used = 0
    for msg in reversed(messages):
        if msg.get("role") != "tool":
            continue
        content = msg.get("content") or ""
        if content.startswith(_ELIDED_MARKER):
            continue
        if used + len(content) <= budget:
            used += len(content)
        else:
            msg["content"] = f"{_ELIDED_MARKER} ({len(content)} chars)"


# Token-based tool encoders (notably kimi-k2.7 via the Neurodesk vllm gateway)
# emit tool calls as in-band special tokens. The endpoint's tool parser extracts
# them into `message.tool_calls` — but only when a `tools=` schema is attached and
# the parse succeeds. When it doesn't, the raw `<|tool_call…|>` tokens stay in
# `message.content`; a loop that returns content whenever `tool_calls` is empty
# would then persist that token soup as the agent's report. These guard against it.
_TOOL_CALL_LEAK_MARKERS = ("<|tool_call", "tool_calls_section")


def _looks_like_leaked_tool_call(content: str) -> bool:
    """True if `content` carries raw tool-call tokens that must not become a report."""
    return any(marker in content for marker in _TOOL_CALL_LEAK_MARKERS)


def _strip_leaked_tool_calls(content: str) -> str:
    """Drop any leaked tool-call token block, keeping the prose that precedes it."""
    cut = min(
        (content.find(m) for m in _TOOL_CALL_LEAK_MARKERS if m in content),
        default=len(content),
    )
    return content[:cut].rstrip()


async def _final_report(
    label: str, call_model: str, messages: list[dict], extra_kwargs: dict
) -> tuple[str, int, int]:
    """Coax a clean prose final report, defending against leaked tool-call tokens.

    Keeps `tools` attached with ``tool_choice="none"`` so the endpoint's tool-call
    parser stays engaged (without a schema, kimi's tool tokens leak into content)
    while forbidding further calls. If tokens still leak, re-prompt once, then strip
    them as a last resort. Returns (report, prompt_tokens, completion_tokens).
    """
    prompt_tokens = completion_tokens = 0
    content = ""
    for attempt in range(2):
        response = await _acompletion(
            label,
            model=call_model,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="none",
            max_tokens=16384,
            **extra_kwargs,
        )
        prompt_tokens, completion_tokens = _add_usage(
            response, prompt_tokens, completion_tokens
        )
        if not response.choices:
            return "", prompt_tokens, completion_tokens
        content = response.choices[0].message.content or ""
        if not _looks_like_leaked_tool_call(content):
            return content, prompt_tokens, completion_tokens
        logger.warning(
            f"[{label}] final report leaked tool-call tokens; re-prompting "
            f"(attempt {attempt + 1})"
        )
        messages.append(response.choices[0].message.model_dump())
        messages.append({
            "role": "user",
            "content": (
                "Your previous message contained raw tool-call tokens instead of a "
                "report. You cannot call any tools now. Write the final report as "
                "plain markdown prose only, with no tool-call syntax."
            ),
        })
    return _strip_leaked_tool_calls(content), prompt_tokens, completion_tokens


async def run_agent(
    system_prompt: str,
    user_message: str,
    repo_root: str,
    model: str,
    label: str = "agent",
    max_turns: int = MAX_TURNS,
) -> str:
    """Run an LLM agent loop with filesystem tools until the model stops calling tools."""
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    call_model, extra_kwargs = resolve_model(model)
    budget = _tool_result_budget()
    start = time.monotonic()
    prompt_tokens = completion_tokens = 0

    for turn in range(max_turns):
        logger.info(f"[{label}] turn {turn + 1}/{max_turns}")

        response = await _acompletion(
            label,
            model=call_model,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            max_tokens=16384,
            **extra_kwargs,
        )
        prompt_tokens, completion_tokens = _add_usage(response, prompt_tokens, completion_tokens)

        if not response.choices:
            logger.warning(f"[{label}] empty response, retrying...")
            continue

        choice = response.choices[0]
        message = choice.message

        if not message.tool_calls:
            content = message.content or ""
            # The model meant to call a tool but the endpoint failed to parse its
            # tokens (they leaked into content). Don't persist that as the report —
            # nudge it to retry the call or write a clean report, then continue.
            if _looks_like_leaked_tool_call(content):
                logger.warning(
                    f"[{label}] turn {turn + 1}: tool-call tokens leaked into "
                    "content; nudging and continuing"
                )
                messages.append(message.model_dump())
                messages.append({
                    "role": "user",
                    "content": (
                        "Your previous message contained raw tool-call tokens that "
                        "did not register as a tool call. Either call the tool again "
                        "properly, or, if you are finished, write your final report "
                        "as plain markdown with no tool-call syntax."
                    ),
                })
                continue
            record_agent(AgentStat(
                label, turn + 1, time.monotonic() - start, prompt_tokens, completion_tokens
            ))
            return content

        messages.append(message.model_dump())

        for tool_call in message.tool_calls:
            fn = tool_call.function
            args = json.loads(fn.arguments)
            logger.info(f"[{label}]   {fn.name}({fn.arguments})")
            result = execute_tool(fn.name, args, repo_root)
            logger.debug(f"  Result: {result[:200]}...")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )

        _compact_tool_results(messages, budget)

        remaining = max_turns - turn - 1
        if 0 < remaining <= TURN_WARNING_THRESHOLD:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Budget notice: {remaining} tool-call turn(s) remain before "
                        "you must stop and produce your final report. Prioritize the "
                        "most important remaining lookups and prepare to conclude."
                    ),
                }
            )

    # Turn budget exhausted. Rather than crashing the whole pipeline (which also
    # discards any parallel sibling agent's work), force a best-effort report from
    # what the agent has already gathered. A partial report is far more useful to
    # downstream agents than a hard failure — especially on large packages where
    # exhaustive tracing legitimately exceeds the budget.
    logger.warning(f"[{label}] hit {max_turns}-turn budget; forcing best-effort final report")
    messages.append(
        {
            "role": "user",
            "content": (
                "You have reached the exploration budget and may not call any more "
                "tools. Produce your final report now using everything you have "
                "gathered so far, and list any remaining uncertainties explicitly."
            ),
        }
    )
    report, fp_prompt, fp_completion = await _final_report(
        label, call_model, messages, extra_kwargs
    )
    prompt_tokens += fp_prompt
    completion_tokens += fp_completion
    record_agent(AgentStat(
        label, max_turns, time.monotonic() - start, prompt_tokens, completion_tokens
    ))
    return report
