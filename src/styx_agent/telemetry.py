"""Per-agent run telemetry, collected via a contextvar.

The agent loop and the author record their own stats into whatever sink is
active on the current context, so orchestrators can scope collection
(``with collect_agent_stats() as stats:``) without threading a stats object
through every scanner/explorer signature. Outside a collection scope,
recording is a no-op.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class AgentStat:
    """Cost/effort of a single agent run (one scanner/explorer/author pass)."""

    label: str
    turns: int
    seconds: float
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "turns": self.turns,
            "seconds": round(self.seconds, 2),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


_sink: contextvars.ContextVar[list[AgentStat] | None] = contextvars.ContextVar(
    "styx_agent_stat_sink", default=None
)


def record_agent(stat: AgentStat) -> None:
    """Append an agent's stats to the active sink, if any (else no-op)."""
    sink = _sink.get()
    if sink is not None:
        sink.append(stat)


@contextmanager
def collect_agent_stats() -> Iterator[list[AgentStat]]:
    """Collect every AgentStat recorded within this scope into a fresh list."""
    stats: list[AgentStat] = []
    token = _sink.set(stats)
    try:
        yield stats
    finally:
        _sink.reset(token)
