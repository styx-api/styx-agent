"""Author agents: translate Explorer reports into descriptors.

Currently supports Boutiques (Styx v1 flavor). Future: argtype.
"""

from styx_ai.author.boutiques import BOUTIQUES_AUTHOR_PROMPT, author_boutiques
from styx_ai.author.validator import SCHEMA_VERSION, ValidationError, validate

__all__ = [
    "BOUTIQUES_AUTHOR_PROMPT",
    "SCHEMA_VERSION",
    "ValidationError",
    "author_boutiques",
    "validate",
]
