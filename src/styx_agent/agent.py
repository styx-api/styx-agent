"""Generic agent loop used by scan and explorer agents."""

from __future__ import annotations

import asyncio
import json
import logging
import os

import litellm

from styx_agent.tools.filesystem import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get(
    "STYX_AGENT_MODEL", "litellm_proxy/bedrock/us.anthropic.claude-sonnet-4-6"
)
MAX_TURNS = 40


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

    for turn in range(max_turns):
        logger.info(f"[{label}] turn {turn + 1}/{max_turns}")

        for attempt in range(5):
            try:
                response = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    max_tokens=16384,
                )
                break
            except litellm.exceptions.RateLimitError:
                wait = min(2 ** attempt * 10, 60)
                logger.warning(f"[{label}] rate limited, waiting {wait}s (attempt {attempt + 1}/5)")
                await asyncio.sleep(wait)
                if attempt == 4:
                    raise

        if not response.choices:
            logger.warning(f"[{label}] empty response, retrying...")
            continue

        choice = response.choices[0]
        message = choice.message

        if not message.tool_calls:
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

    raise RuntimeError(f"[{label}] exceeded {max_turns} turns without producing a result")
