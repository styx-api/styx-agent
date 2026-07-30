# argtype validation bridge

A tiny Node bridge that lets the Python `styx-agent` author validate and
transpile **argtype** by calling the real styx compiler (`@styx-api/core`),
rather than re-implementing (and drifting from) the hand-written grammar.

- `argtype_validate.mjs` — reads argtype on stdin, calls `compile(src, {format:"argtype"})`,
  prints `{ok, errors, warnings, n_nodes}` as JSON. Used by the author's retry loop
  (`src/styx_agent/author/argtype_validator.py`). Note: this confirms the document
  parses and lowers to the Styx IR; it does **not** resolve output-template
  references, so a dangling `` `{name}` `` in `.output(...)` is not caught here.
- `styx_bridge.mjs` — multi-purpose: `--emit ir|argtype|validate`, `--format
  boutiques|argtype`. Used for IR inspection and boutiques→argtype transpile.

## Setup

```bash
npm install        # in this directory — pulls @styx-api/core from npm
```

That's all: the dependency is pinned to a published `@styx-api/core` version, so a
fresh clone needs nothing else. (`node` must be on PATH.)

## Using a local / unreleased compiler build

To validate against a local styx checkout (e.g. unreleased compiler changes), point
the dependency at it and rebuild after each change:

```jsonc
// package.json
"@styx-api/core": "file:../../../styx/packages/core"
```

```bash
cd ../../../styx && npm run build -w @styx-api/core   # after each compiler change
cd -            && npm install
```

When done, restore the pinned version string and `npm install` again.

Override the bridge script path (e.g. to point at a different checkout) with the
`STYX_AGENT_ARGTYPE_BRIDGE` env var.
