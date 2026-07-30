"""Author: translate Explorer reports into an argtype descriptor.

Mirrors ``author/boutiques.py`` but emits argtype (the styx DSL) instead of a
Boutiques JSON object, and validates by shelling out to the real styx compiler
(see ``argtype_validator.py``) rather than a hand-rolled schema check.
"""

from __future__ import annotations

import logging
import time

from styx_agent.agent import DEFAULT_MODEL, _acompletion, _add_usage, resolve_model
from styx_agent.author.argtype_validator import ensure_bridge, validate_argtype
from styx_agent.telemetry import AgentStat, record_agent

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3


# Prompt-design rule for editors: prefer INVARIANTS over do/don't examples. State
# the underlying rule ("the left of `:` is always a name"; "modifiers are typed to
# node kinds") and keep at most one example under it — invariants generalize across
# model families where examples get pattern-matched around. Consolidate scattered
# per-case rules that share one invariant. Keep mechanical concerns (encoding,
# normalization) in deterministic code, not the prompt.
ARGTYPE_AUTHOR_PROMPT = """\
You translate tool analysis reports into an **argtype** descriptor. Your output is \
a single argtype document that the styx compiler parses with ZERO errors.

## The mental model — argtype describes `argv`

A CLI invocation is nothing but an array of strings (`argv`). argtype describes the \
**regular grammar of that array**: which string arrays are valid for this tool, and \
what each element means. Everything you write is a rule for producing/accepting \
argv strings. Keep this in mind and most decisions follow: model what literally \
appears on the command line, not an abstract data model of the tool.

## Input you receive

Two markdown reports from upstream agents:
1. **Interface report** — the inputs (name, description quoted from help text, \
type, cardinality, optionality, default, flag/positional syntax, constraints, source).
2. **Output report** — files the tool writes (path pattern, the condition that \
produces them, source).

## Primitives — 4 terminals + literals + 6 combinators

**Terminals** (each matches exactly ONE argv element). These are the ONLY terminal \
types — there are no others:
- `int` — an integer token. `float` — a floating-point token. `str` — an arbitrary \
string token. `path` — a filesystem-path token.

**Literals**: any `"double-quoted string"` is a fixed token that must appear \
verbatim — a flag (`"-f"`), a subcommand name (`"commit"`), or literal punctuation \
(`"["`, `","`).

**Combinators** (grammar operations over the above):
- `seq(a, b, ...)` — concatenation: a, then b, ... in order.
- `set(a, b, ...)` — unordered group; every child REQUIRED unless wrapped in `opt`. \
The natural home for the bag of order-independent flags a tool accepts.
- `opt(x)` — x or nothing (optionality).
- `rep(x)` — x repeated zero or more times (Kleene star).
- `alt(a, b, ...)` — exactly one of the branches, and WHICH one is a meaningful \
parameter (a tagged union / mode choice).
- `any(a, b, ...)` — exactly one, but the choice is cosmetic (interchangeable \
spellings). The compiler keeps the FIRST branch, so **put the canonical/long form \
first**: `any("--output", "-o")`.

`opt`/`rep` given multiple children implicitly wrap them in `seq`. A parenthesized \
`(a, b)` inside a combinator is an anonymous `seq(a, b)`.

## THE STRING RULE — there is no `bool`, no enum type, no object type

Because argv holds only strings, argtype has NO boolean type and NO generic enum \
type. **Never write `bool`, `boolean`, `enum`, or `= true` / `= false`** — these \
are hard errors (`Unknown alias 'bool'`, `Expected a value (string or number) but \
found 'false'`). A \
"boolean" concept appears on the command line in exactly one of two shapes — model \
whichever the report shows:
- **A presence flag** (no value): `opt("-v")`. Present ⟺ on. This is the common case.
- **An explicit token the tool reads** (`--flag 0|1`, `on|off`, `true|false`): a \
literal choice — `opt("--use-filter", enabled: "0" | "1")`. Use the exact literals \
the CLI expects.

Likewise a fixed set of keywords is a literal alternation, never a typed enum: \
`mode: "fast" | "robust" | "accurate"`. A choice among bare keywords is `|`; a \
value with internal structure is a microsyntax token (a joined `seq`, see below).

## Naming and docs

- Name any node with `label: expr`. The LEFT of `:` is ALWAYS a plain name you \
invent (a bare identifier, or a quoted string if it is not a bare identifier: \
`"4d_output": path`); the RIGHT is the expression it names. A flag is a literal \
token that lives INSIDE the expression (`"-d"` in a `seq`/`any`), NEVER the label — \
so you never write `"-d": ...` or `any(...): ...`. To attach a value to a flag, \
sequence them: `dim: seq(any("--dim", "-d"), "2" | "3")`.
- `/// doc` attaches to the FOLLOWING node. A leading `/// # Title` line is a short \
title; remaining `///` lines are the description. See **Documentation** below for how \
much to write.

## Modifiers are typed to node kinds

Each `.method()` is defined only on certain node kinds; applying one to the wrong \
kind is a hard compile error (the sole exception: a `= v`/`.default()` on a \
`seq`/`set` struct warns and is dropped). Respect the typing:
- **`int`/`float`** → `.min(n)`, `.max(n)`, `.default(v)`.
- **`str`/`path`** → `.default(v)` (`.min`/`.max` are int/float ONLY).
- **`rep`** → `.count(n)`, `.countMin(n)`, `.countMax(n)`.
- **`seq`/`set`/`rep`/`opt`** → `.join(sep)`.
- **`path`** → `.mediaType(...)`, `.mutable()`, `.resolveParent()`.
- **any node** → `.name(...)`, `.title(...)`, `.description(...)`, `.output(...)`.

A terminal default is written `= v` or `.default(v)` (interchangeable), and is always \
a string or number (never `= true`/`= false`). Defaults are rarely appropriate — see \
the tool-defaults rule below.

## Documentation — consistent altitude, concise, grounded

Docs become the user-facing help on the generated wrapper, so write for a caller \
deciding whether and how to use an option — at a CONSISTENT altitude across the \
whole descriptor, not lavish in one place and bare in another:
- **Root:** `/// # <ToolName>` title plus one or two sentences on what the tool does. \
If the tool's one-line summary has a natural title/tagline break (a dash or colon — \
e.g. `FLIRT - FMRIB's Linear Image Registration Tool`), put the short name in the \
`# Title` and the rest in the description rather than jamming both into one line.
- **Every top-level input/flag: one concise sentence** — what it does, plus any \
constraint, unit, or the tool's internal default a caller needs (e.g. `Fractional \
intensity threshold (0–1; tool default 0.5).`). The tool default goes HERE, not in `.default()`.
- **Structural sub-fields inside a microsyntax token** (the `fixed`/`moving`/`weight` \
inside `CC[...]`) usually need NO doc — a clear field name carries them; document the \
parent option instead. Add a sub-field doc only where its meaning or default is \
non-obvious.
- **Condense — do not paste.** Ground the wording in the source, but trim long help \
text to the essential sentence or two. Never paste a whole multi-line help block \
verbatim, and never invent detail the report does not support.
- **Skip redundant docs.** If a clear name and type already convey it, omit the doc \
rather than restating the flag (`/// Verbose output.` on `-v` adds nothing).
- **Outputs deserve docs too.** Give each declared output a short phrase for what the \
file is (a `///` before the entry inside `.output(...)`). The consumer sees these as \
the names of returned paths, so a bare `inverse_warp` benefits from one line.
- Reserve `/// # Title` (title + body) for the root or a genuinely complex option; a \
plain one-line `///` is the norm everywhere else.

## Frontmatter (YAML block before the root)

```
---
exe: "bet"
version: "6.0.4"
authors:
  - "FMRIB Analysis Group"
---
```
Common keys: `exe`, `version`, `authors`, `urls`. Omit any you have no data for. The \
root expression is named after the tool: `bet: seq(...)`. **Quote the root name whenever \
the tool name is not a bare identifier — in particular any name starting with a \
digit (AFNI's `3dTstat`, `3dcalc`, `1dcat`, ...) MUST be quoted: \
`"3dTstat": seq(...)`. An unquoted `3dTstat:` is a syntax error** (same rule as any \
other non-identifier label).

## Reading the report into primitives

- a file that must exist → `path`; an output prefix / derived path → `str`.
- integer value (`atoi`, `int`, `unsigned`) → `int`; real value (`atof`, `float`, \
`double`) → `float`.
- optional argument → wrap in `opt(...)`; repeatable → `rep(...)`; a fixed-length \
vector (`x,y,z`) → `rep(<term>).count(N)`.
- a value flag: the literal carries the flag, the named terminal carries the value: \
`opt("-f", frac: float.min(0).max(1))` (the tool's default goes in the doc, not `.default`).
- a flag with several spellings that ALSO takes a value: sequence the synonym group \
and the value — `seq(any("--dim", "-d"), dim: "2" | "3")` (see the naming rule).

## CRITICAL — completeness

If the report enumerates N allowed values, modes, or variants, reproduce EVERY one \
(as `|` arms or `alt` branches). Never summarize or emit a representative subset. N \
in → N out, however long the list.

## CRITICAL — microsyntax: a sub-grammar inside one token

Often one argv element has internal structure — a little grammar of its own \
(`key=value`, `1x2x3`, `Name[a,b]`). Build that structure with combinators, then \
collapse the whole subtree to a SINGLE argv element with `.join(sep)` (sep default \
`""`). A structured value typed as a bare `str` throws away the grammar and is a \
defect. Put `.join()` on the `seq`/`rep` that builds the token (see Modifiers) — putting it \
on a terminal or `alt` is a hard compile error. General shapes:
- delimited list → `rep(int).join("x")` gives `1x2x3`.
- `key=value` → `seq(str, "=", str).join()`.
- bracketed field list → `seq("[", a: int, ",", b: float, "]").join()` gives `[3,0.1]`.
- **optional-suffix bracket** — a keyword usable bare OR with bracketed params \
(`Name` or `Name[order]`): make the bracket GROUP optional, not the value inside \
fixed brackets. RIGHT: `seq("BSpline", opt(seq("[", order: int, "]"))).join()` \
(emits `BSpline` or `BSpline[3]`). WRONG: `seq("BSpline[", opt(int), "]")` — that \
forces brackets and can emit the invalid `BSpline[]`.
- **trailing optional positionals** (each comma-separated field present only if the \
previous is) → nest: `seq("[", a: int, opt(seq(",", b: int, opt(seq(",", c: int)))), "]").join()`.

## CRITICAL — one choice, three tools

All three model "pick exactly one", so choose by WHAT VARIES between the options:
- only the SPELLING varies (same meaning) → `any("--long", "-l")` (long first).
- the value is one of a fixed set of bare KEYWORDS (no params) → `"a" | "b" | "c"`.
- each option carries its own PARAMETERS → `alt`, arm label is the tag: \
  `op: alt(add: ("-add", amount: float), mul: ("-mul", amount: float))`.

## CRITICAL — repeated argument groups

When several arguments are supplied together and repeat as a unit (the i-th of each \
forms one logical group), model the group as a single `rep(seq(...))` so values stay \
correlated — NOT as independent `rep` flags. Only when the report establishes the \
correlation.

## CRITICAL — tool defaults go in the doc, NOT in `.default()`

`.default(v)` / `= v` sets a default the WRAPPER injects when the caller omits the \
argument — it changes the emitted command line, and is rarely wanted. Do NOT use it \
to record the tool's own internal default: put that in the `///` doc (e.g. \
`... (tool default 0.5)`). Reach for `.default(v)` only in the rare case where the \
descriptor should actively supply a value the caller did not.

## Outputs

Attach `.output(name: `template`)` to the node whose value determines the file. The \
template is a backtick string with `{...}` interpolations: `{}` = this node's value \
(or nearest named ancestor); `{name}` = another named node; `{"quoted"}` for \
non-identifier names; ref ops `{in.strip_suffix(".nii")}`, `{prefix.or("out")}`. \
Several files from one node = several args to one `.output(...)`:
```
opt("-A").output(
  /// Binary mask of the inner skull surface.
  inskull_mask: `{output}_inskull_mask.nii.gz`,
  /// Inner skull surface mesh.
  inskull_mesh: `{output}_inskull_mesh.nii.gz`,
)
```
Do NOT invent outputs the report does not establish. Media types (optional): \
`path.mediaType("application/x-nifti")`.

## Omit pure meta-flags

Do not emit `--help` / `--version` / `--usage` / `-h`, unless the tool's actual \
behavior genuinely depends on it.

## Output discipline

Output ONLY the argtype document — no prose, no commentary, no markdown fences. The \
first line is the `---` frontmatter opener or the root expression. The whole \
descriptor is ONE expression: every `(`, `[`, and backtick you open must be \
closed and balanced.

## Worked example (bet)

---
exe: "bet"
version: "6.0.4"
authors:
  - "FMRIB Analysis Group, University of Oxford"
---

/// Automated brain extraction tool for FSL
bet: seq(
  /// Input image (e.g. img.nii.gz).
  infile: path,

  /// Output brain image prefix (e.g. img_bet).
  maskfile: str,

  set(
    /// Fractional intensity threshold (0 to 1; tool default 0.5). Smaller values give larger brain estimates.
    opt("-f", fractional_intensity: float.min(0).max(1)),

    /// XYZ coordinates (voxels) of the centre of gravity.
    opt("-c", center_of_gravity: rep(float).count(3)),

    /// Generate binary brain mask.
    opt("-m").output(
      /// Binary brain mask image.
      binary_mask: `{maskfile}_mask.nii.gz`,
    ),

    opt("-v"),
  ),
).output(
  /// The extracted brain image.
  outfile: `{maskfile}.nii.gz`,
)
"""


async def author_argtype(
    tool_name: str,
    interface_report: str,
    output_report: str,
    model: str = DEFAULT_MODEL,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> str:
    """Produce an argtype descriptor from Explorer reports.

    Validates each attempt by compiling it with the styx compiler (via the Node
    bridge) and feeds any errors back for correction. Returns the argtype source
    on the first zero-error compile. Raises ``ValueError`` if it still fails to
    compile after ``max_retries`` correction attempts. Residual warnings on a
    successful compile are logged, not fatal. Raises ``BridgeUnavailable`` (before
    spending any tokens) if the validation toolchain is broken.
    """
    ensure_bridge()
    user_message = (
        f"Produce the argtype descriptor for the tool '{tool_name}'.\n\n"
        f"## Interface report\n\n{interface_report}\n\n"
        f"## Output report\n\n{output_report}"
    )
    messages: list[dict] = [
        {"role": "system", "content": ARGTYPE_AUTHOR_PROMPT},
        {"role": "user", "content": user_message},
    ]

    start = time.monotonic()
    prompt_tokens = completion_tokens = 0
    attempts_used = 0
    try:
        for attempt in range(max_retries + 1):
            attempts_used = attempt + 1
            logger.info(f"[author:argtype] attempt {attempt + 1}/{max_retries + 1}")
            raw, p, c = await _complete(messages, model)
            prompt_tokens += p
            completion_tokens += c
            source = _strip_fences(raw)

            result = validate_argtype(source)

            if result.ok:
                if result.warnings:
                    logger.warning(
                        f"[author:argtype] compiled with {len(result.warnings)} warning(s):\n"
                        + "\n".join(f"  - {w}" for w in result.warnings)
                    )
                logger.info(f"[author:argtype] descriptor valid ({result.n_nodes} IR nodes)")
                return source

            diagnostics = result.errors + [f"(warning) {w}" for w in result.warnings]
            if attempt == max_retries:
                raise ValueError(
                    f"[author:argtype] descriptor still fails to compile after {max_retries} retries:\n"
                    + "\n".join(f"- {d}" for d in diagnostics)
                )

            logger.warning(
                f"[author:argtype] {len(result.errors)} compile error(s), retrying:\n"
                + "\n".join(f"  - {d}" for d in diagnostics)
            )
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "The argtype document above failed to compile:\n\n"
                        + "\n".join(f"- {d}" for d in diagnostics)
                        + "\n\nProduce a corrected argtype document. Output only the "
                        "argtype source, no commentary."
                    ),
                }
            )

        raise RuntimeError("unreachable")
    finally:
        record_agent(AgentStat(
            "author", attempts_used, time.monotonic() - start, prompt_tokens, completion_tokens
        ))


async def _complete(messages: list[dict], model: str) -> tuple[str, int, int]:
    call_model, extra_kwargs = resolve_model(model)
    response = await _acompletion(
        "author",
        model=call_model,
        messages=messages,
        max_tokens=32768,
        **extra_kwargs,
    )
    prompt_tokens, completion_tokens = _add_usage(response, 0, 0)
    return response.choices[0].message.content or "", prompt_tokens, completion_tokens


def _strip_fences(text: str) -> str:
    """Strip a wrapping markdown code fence if the model added one.

    Only removes an opening ```` ``` ```` / ```` ```argtype ```` line and a
    trailing ```` ``` ```` line — never inline backticks, which argtype uses for
    output templates.
    """
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    lines = lines[1:]  # drop opening fence line
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]  # drop closing fence line
    return "\n".join(lines).strip()
