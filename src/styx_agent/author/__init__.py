"""Author agents: translate Explorer reports into descriptors.

Supports two targets: Boutiques (Styx v1 flavor) and argtype (the styx DSL).
"""

from styx_agent.author.argtype import ARGTYPE_AUTHOR_PROMPT, author_argtype
from styx_agent.author.argtype_validator import ArgtypeValidation, validate_argtype
from styx_agent.author.boutiques import BOUTIQUES_AUTHOR_PROMPT, author_boutiques
from styx_agent.author.validator import SCHEMA_VERSION, ValidationError, validate

# Target name → (author coroutine, output-file extension). The extension is the
# on-disk suffix for the descriptor artifact (boutiques is JSON, argtype is a
# text DSL). Consumers dispatch on this instead of hardcoding "boutiques".
TARGETS: dict[str, tuple] = {
    "boutiques": (author_boutiques, "json"),
    "argtype": (author_argtype, "argtype"),
}

__all__ = [
    "ARGTYPE_AUTHOR_PROMPT",
    "BOUTIQUES_AUTHOR_PROMPT",
    "SCHEMA_VERSION",
    "TARGETS",
    "ArgtypeValidation",
    "ValidationError",
    "author_argtype",
    "author_boutiques",
    "validate",
    "validate_argtype",
]
