# argtype validation bridge

A tiny Node bridge that lets the Python `styx-agent` author validate and
transpile **argtype** by calling the real styx compiler (`@styx-api/core`),
rather than re-implementing (and drifting from) the hand-written grammar.

- `argtype_validate.mjs` — reads argtype on stdin, calls `compile(src, {format:"argtype"})`,
  prints `{ok, errors, warnings, n_nodes}` as JSON. Used by the author's retry loop
  (`src/styx_agent/author/argtype_validator.py`).
- `styx_bridge.mjs` — multi-purpose: `--emit ir|argtype|validate`, `--format
  boutiques|argtype`. Used for IR inspection and boutiques→argtype transpile.

## Setup

```bash
npm install        # in this directory
```

The dependency is a **local file: path** to the sibling styx checkout
(`file:../../../styx/packages/core`), so the author validates against your local —
possibly unreleased — compiler:

```jsonc
// package.json
"@styx-api/core": "file:../../../styx/packages/core"
```

**After changing the compiler, rebuild it** so the bridge picks up the change:

```bash
cd ../../../styx && npm run build -w @styx-api/core
```

To pin a published version instead (portable, but misses unreleased fixes), set the
dependency to a version string (e.g. `"@styx-api/core": "0.8.0"`) and `npm install`.

Override the bridge script path (e.g. to point at a different checkout) with the
`STYX_AGENT_ARGTYPE_BRIDGE` env var.
