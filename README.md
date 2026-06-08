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
GEMINI_API_KEY=...
LITELLM_PROXY_API_BASE=...
LITELLM_PROXY_API_KEY=...
```

The model is overridable via `STYX_AGENT_MODEL`; it defaults to
`litellm_proxy/bedrock/us.anthropic.claude-sonnet-4-6`.

## Usage

```bash
# Clone the source repos you want to wrap (registry: scripts/repos.json)
python scripts/clone_repos.py

# Full pipeline for one tool: scan → interface → outputs → author
styx-agent wrap <tool> <repo>

# Or run stages individually
styx-agent scan <repo> [--package fsl] [--refresh]
styx-agent explore <tool> <repo> [--package fsl]
styx-agent author <tool> [--target boutiques] [--max-retries 3]
```

Artifacts land under `--out-root` (default `output/`, gitignored):

    <out_root>/<package>/_strategy/{enumeration,parsing,outputs}.md
    <out_root>/<package>/<tool>/{interface,outputs}.md
    <out_root>/<package>/<tool>/boutiques.json

## Development

```bash
uv run pytest        # tests
uv run ruff check    # lint
uv run pyright       # types
```

See `CLAUDE.md` for architecture details and design decisions, and
`docs/next-up.md` for planned work.

## License

MIT — see [LICENSE](LICENSE).
