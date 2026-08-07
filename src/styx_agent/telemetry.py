"""Per-agent run telemetry, collected via a contextvar.

The agent loop and the author record their own stats into whatever sink is
active on the current context, so orchestrators can scope collection
(``with collect_agent_stats() as stats:``) without threading a stats object
through every scanner/explorer signature. Outside a collection scope,
recording is a no-op.
"""

from __future__ import annotations

import contextvars
import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentStat:
    """Cost/effort of a single agent run (one scanner/explorer/author pass)."""

    label: str
    turns: int
    seconds: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: The model string this run was actually served by. An artifact directory
    #: is otherwise only labelled by whatever the operator typed, so a
    #: mislabelled one is undetectable after the fact.
    model: str = ""
    #: Every message in the conversation, in order, as it happened.
    #:
    #: Deliberately not part of `to_dict()`: aggregates go into `run.json`,
    #: `results.jsonl` and `meta.json`, and folding a megabyte of transcript
    #: into each of them would make the summaries unreadable and unloadable.
    #: `write_transcripts` puts these somewhere of their own.
    transcript: list[dict] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "model": self.model,
            "turns": self.turns,
            "seconds": round(self.seconds, 2),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "messages": len(self.transcript),
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


def write_transcripts(stats: Sequence[AgentStat], dest: Path) -> list[Path]:
    """Write each agent's full history to ``<dest>/transcripts/<label>.jsonl``.

    JSONL rather than one JSON array so a transcript streams, greps, and can be
    read back a message at a time - these run to megabytes on an outputs trace,
    which is the size at which "just load the file" stops being free.

    A label repeated within one scope (the same agent run twice, as the author's
    retry loop does not do but a caller might) gets a numeric suffix rather than
    overwriting: losing the first of two histories is exactly the failure this
    exists to prevent.
    """
    if not any(stat.transcript for stat in stats):
        return []
    out_dir = dest / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    seen: dict[str, int] = {}
    for stat in stats:
        if not stat.transcript:
            continue
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in stat.label) or "agent"
        seen[safe] = seen.get(safe, 0) + 1
        name = safe if seen[safe] == 1 else f"{safe}.{seen[safe]}"
        path = out_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            # A header row so a transcript is self-describing when it travels
            # apart from the meta.json beside it.
            fh.write(json.dumps({"_meta": stat.to_dict()}, ensure_ascii=False) + "\n")
            for message in stat.transcript:
                fh.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
        written.append(path)
    return written
