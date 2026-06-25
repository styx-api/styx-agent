"""Generic agent loop used by scan and explorer agents."""

from __future__ import annotations

import asyncio
import json
import logging
import os
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


async def _acompletion(label: str, **kwargs) -> litellm.ModelResponse:
    """``litellm.acompletion`` with bounded exponential backoff on rate limits.

    We never request streaming, so the result is always a ``ModelResponse``; the
    cast keeps that knowledge in one place instead of at every call site.
    """
    for attempt in range(5):
        try:
            return cast(litellm.ModelResponse, await litellm.acompletion(**kwargs))
        except litellm.exceptions.RateLimitError:
            wait = min(2 ** attempt * 10, 60)
            logger.warning(f"[{label}] rate limited, waiting {wait}s (attempt {attempt + 1}/5)")
            await asyncio.sleep(wait)
            if attempt == 4:
                raise
    raise RuntimeError(f"[{label}] exhausted completion retries")  # pragma: no cover


def _add_usage(response, prompt_tokens: int, completion_tokens: int) -> tuple[int, int]:
    """Accumulate token usage from a completion response (usage may be absent)."""
    usage = getattr(response, "usage", None)
    if usage is not None:
        prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens += getattr(usage, "completion_tokens", 0) or 0
    return prompt_tokens, completion_tokens


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
            record_agent(AgentStat(
                label, turn + 1, time.monotonic() - start, prompt_tokens, completion_tokens
            ))
            return message.content or ""

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
    response = await _acompletion(
        label,
        model=call_model,
        messages=messages,
        max_tokens=16384,
        **extra_kwargs,
    )
    prompt_tokens, completion_tokens = _add_usage(response, prompt_tokens, completion_tokens)
    record_agent(AgentStat(
        label, max_turns, time.monotonic() - start, prompt_tokens, completion_tokens
    ))
    if response.choices:
        return response.choices[0].message.content or ""
    return ""
