"""Filesystem tools exposed to the Explorer LLM via tool use."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# Maximum bytes to return from file reads / grep results
MAX_OUTPUT_BYTES = 30_000


def _truncate(text: str, limit: int = MAX_OUTPUT_BYTES) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n\n... (truncated, {len(text)} bytes total)"
    return text


def list_directory(path: str, repo_root: str) -> str:
    """List files and directories at the given path."""
    full = _resolve(path, repo_root)
    if not full.is_dir():
        return f"Error: {path} is not a directory"
    entries: list[str] = []
    for entry in sorted(full.iterdir()):
        suffix = "/" if entry.is_dir() else ""
        entries.append(f"{entry.name}{suffix}")
    return "\n".join(entries) if entries else "(empty directory)"


def read_file(path: str, repo_root: str, offset: int = 0, limit: int = 500) -> str:
    """Read lines from a file. Returns numbered lines."""
    full = _resolve(path, repo_root)
    if not full.is_file():
        return f"Error: {path} is not a file"
    try:
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return f"Error reading {path}: {e}"
    selected = lines[offset : offset + limit]
    numbered = [f"{i + offset + 1}\t{line}" for i, line in enumerate(selected)]
    result = "\n".join(numbered)
    if offset + limit < len(lines):
        result += f"\n\n... ({len(lines)} lines total, showing {offset+1}-{offset+len(selected)})"
    return _truncate(result)


def read_tail(path: str, repo_root: str, limit: int = 100) -> str:
    """Read the last N lines of a file. Returns numbered lines."""
    full = _resolve(path, repo_root)
    if not full.is_file():
        return f"Error: {path} is not a file"
    try:
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return f"Error reading {path}: {e}"
    start = max(0, len(lines) - limit)
    selected = lines[start:]
    numbered = [f"{i + start + 1}\t{line}" for i, line in enumerate(selected)]
    result = "\n".join(numbered)
    if start > 0:
        result = f"... ({len(lines)} lines total, showing last {len(selected)})\n\n" + result
    return _truncate(result)


def grep(pattern: str, repo_root: str, path: str = ".", glob_pattern: str | None = None) -> str:
    """Search for a regex pattern in files. Returns matching lines with file:line prefixes."""
    full = _resolve(path, repo_root)
    cmd = ["grep", "-rn", "--include", glob_pattern or "*", "-E", pattern, str(full)]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, cwd=repo_root
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return _python_grep(pattern, full, glob_pattern)
    # grep exits 0 for matches, 1 for no matches, >=2 for a real error. A >=2
    # here means the system grep is unusable for this query (e.g. an --include
    # arg it doesn't parse, as on Windows) rather than a genuine miss, so fall
    # back to the pure-Python implementation instead of reporting "No matches".
    if result.returncode >= 2:
        return _python_grep(pattern, full, glob_pattern)
    output = result.stdout
    if not output:
        return "No matches found."
    # Make paths relative to repo root
    output = output.replace(str(repo_root) + os.sep, "")
    output = output.replace(str(repo_root) + "/", "")
    return _truncate(output)


def find_files(pattern: str, repo_root: str, path: str = ".") -> str:
    """Find files matching a glob pattern."""
    full = _resolve(path, repo_root)
    if not full.is_dir():
        return f"Error: {path} is not a directory"
    matches = sorted(str(p.relative_to(repo_root)) for p in full.rglob(pattern) if p.is_file())
    if not matches:
        return "No files found."
    result = "\n".join(matches[:200])
    if len(matches) > 200:
        result += f"\n\n... ({len(matches)} files total, showing first 200)"
    return result


def _resolve(path: str, repo_root: str) -> Path:
    """Resolve a path relative to repo root, preventing directory traversal.

    Rejects paths that escape the repo via ``..``, absolute paths, or symlinks
    that point outside the repo — the source repos we scan are third-party, so
    symlinked escapes are a realistic vector.
    """
    root = Path(repo_root).resolve()
    resolved = (root / path).resolve()
    if resolved != root and not resolved.is_relative_to(root):
        return root
    return resolved


def _python_grep(pattern: str, path: Path, glob_pattern: str | None) -> str:
    """Fallback grep in pure Python."""
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: invalid regex {pattern!r}: {e}"
    results: list[str] = []
    file_pat = glob_pattern or "*"
    for filepath in path.rglob(file_pat):
        if not filepath.is_file():
            continue
        try:
            for i, line in enumerate(
                filepath.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if regex.search(line):
                    rel = filepath.relative_to(path)
                    results.append(f"{rel}:{i}:{line}")
                    if len(results) >= 500:
                        return _truncate("\n".join(results))
        except Exception:
            continue
    return "\n".join(results) if results else "No matches found."


# OpenAI-style tool definitions (used by LiteLLM)
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": (
                "List files and directories at the given path relative to the repository root. "
                "Use this to explore the project structure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to repo root. Use '.' for root.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read lines from a file. Returns numbered lines. "
                "Use offset and limit to read specific sections of large files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to repo root.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (0-based). Default: 0.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read. Default: 500.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search for a regex pattern in files. Returns matching lines with "
                "file:line prefixes. Use glob to filter by file extension."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in, relative to repo root. Default: '.'.",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Glob pattern to filter files (e.g. '*.cpp', '*.h'). Default: all files.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_tail",
            "description": (
                "Read the last N lines of a file. Useful for finding output-writing code "
                "at the end of large source files without needing to know the total line count."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to repo root.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of lines to read from the end. Default: 100.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Find files matching a glob pattern (e.g. '*.cpp', 'CMakeLists.txt').",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to match file names.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in, relative to repo root. Default: '.'.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]


def execute_tool(name: str, args: dict, repo_root: str) -> str:
    """Execute a tool by name and return its result."""
    match name:
        case "list_directory":
            return list_directory(args["path"], repo_root)
        case "read_file":
            return read_file(
                args["path"], repo_root,
                offset=args.get("offset", 0),
                limit=args.get("limit", 500),
            )
        case "grep":
            return grep(
                args["pattern"], repo_root,
                path=args.get("path", "."),
                glob_pattern=args.get("glob"),
            )
        case "read_tail":
            return read_tail(
                args["path"], repo_root,
                limit=args.get("limit", 100),
            )
        case "find_files":
            return find_files(
                args["pattern"], repo_root,
                path=args.get("path", "."),
            )
        case _:
            return f"Unknown tool: {name}"
