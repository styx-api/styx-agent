# argtype validation bridge

A tiny Node bridge that lets the Python `styx-agent` author validate **argtype**
by calling the argtype parser (`@argtype/core`), rather than re-implementing
(and drifting from) the hand-written grammar.

`argtype_validate.mjs` reads argtype on stdin, runs parse → inline aliases →
resolve annotations → the extension passes (outputs, paths, constraints, media
types) → resolve references, and prints `{ok, errors, warnings, n_nodes}` as
JSON. Used by the author's retry loop (`src/styx_agent/author/argtype_validator.py`).

## Why the parser and not the compiler

This used to call `@styx-api/core`'s `compile()`. That made the author's retry
loop a *deployment* gate wearing a grammar gate's clothes, and it was wrong in
both directions:

- A descriptor whose grammar was right but which the styx compiler could not
  lower yet came back as an error, so the retry loop taught the author to avoid
  legal argtype — writing to one consumer's current capabilities.
- A dangling `` `{name}` `` in `.output(...)` passed, because lowering does not
  resolve templates. It surfaced at codegen instead, far too late.

The parser has neither problem. `resolveReferences` reports that dangling
reference as an error, and nothing here has an opinion about whether any
particular consumer can lower the result.

Extra static checks worth having go into `@argtype/core` where they are
general, or here first and upstream once they have stabilised — not into a
consumer whose policy would then leak back into the author.

The compiler is still used, in `../styx_bridge/`, for IR inspection and the
boutiques path. It is a separate package on purpose: sharing one `package.json`
would put `@styx-api/core` back in the argtype author's install even though
nothing on that path imports it.

## Setup

```bash
npm install        # in this directory — pulls @argtype/core from npm
```

That's all: the dependency is pinned to a published `@argtype/core`, so a fresh
clone needs nothing else. (`node` must be on PATH.)

## Using a local / unreleased parser build

To validate against a local argtype checkout, point the dependency at it and
rebuild after each change:

```jsonc
// package.json
"@argtype/core": "file:../../../styx/packages/core"
```

Restore the published version string and `npm install` again when done.

Override the bridge script path (e.g. to point at a different checkout) with the
`STYX_AGENT_ARGTYPE_BRIDGE` env var.
