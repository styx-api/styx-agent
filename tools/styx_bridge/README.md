# styx compiler bridge

Node bridge to the real styx compiler (`@styx-api/core`), for the things that
genuinely need a compiler rather than a parser.

`styx_bridge.mjs` — multi-purpose: `--emit ir|argtype|validate`,
`--format boutiques|argtype`. Used for IR inspection and boutiques→argtype
transpile.

```bash
npm install        # in this directory — pulls @styx-api/core from npm
```

## Why this is not in ../argtype_bridge/

The argtype author's retry loop is a grammar gate and validates through
`@argtype/core` alone — see `../argtype_bridge/README.md` for why. Keeping both
scripts in one package would put `@styx-api/core` back into that path's install,
so the separation is the point rather than tidiness.

## Using a local / unreleased compiler build

```jsonc
// package.json
"@styx-api/core": "file:../../../styx/packages/core"
```

```bash
cd ../../../styx && npm run build -w @styx-api/core   # after each compiler change
cd -              && npm install
```

Restore the pinned version string and `npm install` again when done.
