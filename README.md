# styx-agent

An AI agent pipeline that reads a command-line tool's **source code** and
produces a [Styx](https://github.com/styx-api) descriptor for it — automated
descriptor authoring for the Styx/NiWrap neuroimaging ecosystem.

Instead of hand-writing (or hand-patching) a Boutiques/argtype descriptor for
each of ~1900 neuroimaging CLI tools, `styx-agent` points a set of focused,
source-reading agents at a tool's repository and has them derive the typed
interface and declared outputs directly from the code.

## Pipeline

```
source code → Scanner → Explorer → Author (+ Validator) → descriptor
```

- **Scanner** — infers package-level conventions once (which tools ship, how
  args are parsed, how outputs are produced) and caches them per package.
- **Explorer** — per tool, an *interface* agent extracts inputs/constraints and
  an *outputs* agent traces output-file generation, each as a markdown report.
- **Author** — translates the two reports into a descriptor (Boutiques today;
  argtype planned), retrying against a Styx-v1 schema validator on failure.

Agents are orchestrated via [LiteLLM](https://github.com/BerriAI/litellm), so any
provider (Gemini, Claude, OpenAI, …) works.

## Install

```bash
uv sync
```

## Configure

Create a `.env` (gitignored) with your LLM provider credentials:

```bash
NEURODESK_KEY=...            # default provider (https://llm.neurodesk.org)
# Optional — only needed if you point STYX_AGENT_MODEL at another provider:
LITELLM_PROXY_API_BASE=...
LITELLM_PROXY_API_KEY=...
```

The model defaults to `neurodesk/kimi-k2.7` (Neurodesk's OpenAI-compatible
gateway, authenticated with `NEURODESK_KEY`). Override it with `STYX_AGENT_MODEL`
or `--model` — any LiteLLM model string works (e.g. `neurodesk/qwen3`,
`litellm_proxy/bedrock/us.anthropic.claude-sonnet-4-6`).

## Usage

```bash
# Clone the source repos you want to wrap, pinned to match each version's build.
# Sources are read from the NiWrap version.json manifests (sibling ../niwrap checkout).
python scripts/clone_repos.py                 # all packages, default version
python scripts/clone_repos.py --package ants  # one package (+ its ITK dependency)

# Full pipeline for one tool: scan → interface → outputs → author
styx-agent wrap <tool> <repo>

# Campaign: wrap many tools into one timestamped run dir with stats + manifest
styx-agent wrap-all <repo> --tools bet,fast,flirt [--package fsl]
# ...or take the tool list from a file: a newline-delimited .txt, a JSON array/
# descriptor, a NiWrap version manifest (its `apps` array), or a directory of
# NiWrap descriptors. E.g. wrap every app in a NiWrap version:
styx-agent wrap-all <repo> --package ants \
  --tools-file path/to/niwrap/src/niwrap/ants/2.5.3/version.json

# Or run stages individually
styx-agent scan <repo> [--package fsl] [--refresh]
styx-agent explore <tool> <repo> [--package fsl]
styx-agent author <tool> [--target boutiques] [--max-retries 3]
```

Artifacts land under `--out-root` (default `output/`, gitignored):

    <out_root>/<package>/_strategy/{enumeration,parsing,outputs}.md
    <out_root>/<package>/<tool>/{interface,outputs}.md
    <out_root>/<package>/<tool>/boutiques.json

`wrap-all` instead writes a self-contained, timestamped run with stats:

    <out_root>/runs/<timestamp>/run.json        # params, provenance, aggregates
    <out_root>/runs/<timestamp>/results.jsonl   # one row per tool (turns, tokens, validation)
    <out_root>/runs/<timestamp>/<package>/<tool>/...  # artifacts + per-tool meta.json

## Development

```bash
uv run pytest        # tests
uv run ruff check    # lint
uv run pyright       # types
```

Each agent lives in its own subpackage under `src/styx_agent/` (`scanner/`,
`explorer/`, `author/`), with the shared LLM-facing filesystem tools in
`tools/`. The Author emits Boutiques today; argtype is a planned target.

## License

MIT — see [LICENSE](LICENSE).
