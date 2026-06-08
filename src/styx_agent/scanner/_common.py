"""Shared constants for scan agents."""

SCAN_PREAMBLE = """\
## Context

You are analyzing the source tree of a software package that ships one or \
more command-line tools. Each tool:

- reads **inputs** at invocation — command-line flags, positional arguments, \
input file paths, etc.
- produces **outputs** at completion — files written to disk, and sometimes \
stdout/stderr text.

Downstream agents will analyze each tool individually to extract its \
interface and outputs. **Your job is to document package-wide conventions \
once, so those downstream agents don't need to rediscover them every run.**

Focus exclusively on user-facing CLI tool patterns. Ignore library-internal \
code, test harnesses, CI configuration, example/demo programs, and build \
system plumbing — unless they directly inform how real tools are structured.

## Output discipline

Output ONLY the requested markdown document. Do not preface it with narrative \
("Okay, I have now gathered enough..."), commentary about your process, or \
notes about what you explored. The first line of your response must be a \
`###` subsection header. No trailing commentary either.
"""
