"""Author: translate Explorer reports into a Boutiques (Styx v1) descriptor."""

from __future__ import annotations

import json
import logging
import re
import time

from styx_agent.agent import DEFAULT_MODEL, _acompletion, _add_usage, resolve_model
from styx_agent.author.validator import SCHEMA_VERSION, validate
from styx_agent.telemetry import AgentStat, record_agent

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3


BOUTIQUES_AUTHOR_PROMPT = """\
You translate tool analysis reports into a Boutiques descriptor (Styx v1 \
flavor). Your output is a single JSON object that passes strict schema \
validation and semantic checks.

## Input you receive

Two markdown reports produced by upstream Explorer agents:

1. **Interface report** — inputs the tool accepts, each with name, \
description (quoted from source help text), type, cardinality, optionality, \
default, syntax (flag/positional), constraints, and a source snippet.
2. **Output report** — files the tool writes, each with path pattern, the \
condition under which the file is produced, and a source snippet.

## Descriptor shape (Styx v1)

**The runtime injects `name` and `schema-version`. Do NOT emit those two \
fields. Emit ONLY the fields listed below — any other top-level field is \
rejected.**

Top-level required fields:
- `description` — one-paragraph plain description of what the tool does.
- `command-line` — a template string with `[UPPERCASE_KEYS]` placeholders, \
beginning with the tool name. Each top-level input's `value-key` MUST appear \
in this template exactly once; every `[KEY]` in the template MUST match an \
input's `value-key`.
- `inputs` — array of input objects (see below).

Top-level optional fields:
- `output-files` — array of output file objects (see below).
- `stdout-output`, `stderr-output` — if the tool emits structured text to \
stdout/stderr that downstream callers should capture.

DO NOT emit: `container-image`, `tool-version`, `tags`, `tests`, \
`environment-variables`, `custom`, `online-platform-urls`, `error-codes`, \
`suggested-resources`, `invocation-schema`. These are rejected by the \
Styx v1 schema (`extra="forbid"`).

## Input objects

Required fields on every input:
- `id` — alphanumeric + underscore, e.g. `"input_file"`. Used to generate \
variable names.
- `name` — human-readable label.
- `value-key` — placeholder string matching `^\\[[A-Z0-9_]+\\]$`. All caps, \
in square brackets, e.g. `"[INPUT_FILE]"`.
- `type` — one of: `"File"`, `"String"`, `"Number"`, `"Flag"` (or a \
SubCommandType object / list of SubCommandTypes — see Subcommands below).

Optional:
- `description` — quote verbatim help text from the interface report when \
available. Use this to document the tool's internal default values too.
- `optional` — default `false`. Set `true` if the user can omit the argument.
- `command-line-flag` — e.g. `"--input-image"` (or `"-i"`). REQUIRED for \
`type: "Flag"`. Omit for purely positional args. **Always prefer the long \
`--flag` form when the interface report documents one** — long flags are \
unambiguous, whereas short single-letter flags are easy to misassign (tools \
reuse or collide on letters, e.g. the same letter bound to two options). Use \
a short flag only when the option has no long form.
- `command-line-flag-separator` — e.g. `"="` for `--flag=value`. Defaults \
to a space.
- `value-choices` — list of permitted values (for String or integer \
Number inputs).
- `list` — `true` for repeatable/multi-value inputs.
- `list-separator`, `min-list-entries`, `max-list-entries` — list \
metadata. Only with `list: true`.
- `minimum`, `maximum` — bounds for Number inputs.
- `integer` — `true` for int Numbers, `false` for float Numbers. Required \
on Number inputs.
- `media-types` — File inputs ONLY. Array of media-type strings naming the \
file format(s) the input accepts (see "Media types" below). Optional metadata.

**CRITICAL RULE on `default-value`:** Do NOT set `default-value` on optional \
inputs to document the tool's internal default. `default-value` means \
"override the wrapper-generated default" and is rarely appropriate. Put the \
tool's internal default in the `description` instead (e.g. \
`"Shrink factor (tool default: 4)"`).

**CRITICAL RULE on completeness:** If the interface report enumerates multiple \
allowed values, modes, formats, or sub-command variants for an input, reproduce \
EVERY one in the descriptor (as `value-choices` entries or SubCommandType \
variants) — never summarize, abbreviate, or emit a representative subset. If the \
report lists N variants, the descriptor MUST contain all N, however long the list.

**CRITICAL RULE on microsyntax — model structured values, don't flatten them.** An argument's value often has internal structure (a small regular grammar), not free text. Whenever the report documents such structure, reproduce it with the descriptor's structural constructs; never collapse it to a bare `String` (a structured value typed as `String` is a defect — it discards the validation/typing the wrapper exists to provide). Map the grammar to the format:
- **Sequence of fields** (e.g. `[a,b,c]`, `a:b`): a SubCommand whose `command-line` encodes the literal delimiters, with one positional input per field — each typed, `optional: true` if it can be omitted (put its default in the field `description`).
- **Alternation** — the value is one of several named, parameterized forms (e.g. `Foo[...]`, `Bar[...]`): a `type` that is a list of SubCommandTypes, one variant per form, each with its own `command-line` and inputs.
- **Repetition** — a delimiter-separated list (e.g. `4x2x1`): `list: true` with the matching `list-separator`; repetition may nest inside a sequence field.

Use `value-choices` ONLY for a simple enum — a choice among bare literal keywords with NO internal parameters. The moment a choice carries its own bracketed/delimited sub-fields (`Keyword[param,...]`), it is alternation → model it as a SubCommand variant. Reserve plain `String` for genuinely free-form values (paths, arbitrary text).

**CRITICAL RULE on repeated argument groups.** When the report's control-flow section indicates several distinct arguments are supplied together and repeat as a unit (a stage / block — the i-th occurrence of each forms one logical group), model the group as a single repeated SubCommand (`list: true`) whose inputs are those arguments (each keeping its own flag) — NOT as independent `list: true` flags, which loses which value pairs with which. Only do this when the report establishes the correlation; genuinely independent repeatable flags stay separate.

## Type mapping from Explorer reports

Interface-report Type field → Boutiques:
- Report says "file path" / "input image filename" / C++ `std::string` used \
as a path → `"File"` if the file must exist; `"String"` if it's an output \
prefix or derived path.
- Report says `atoi`, `strtol`, `unsigned int`, `int` → `"Number"` with \
`"integer": true`.
- Report says `atof`, `strtod`, `float`, `double` → `"Number"` with \
`"integer": false`.
- Report says "boolean flag" with no argument → `"Flag"` (requires \
`command-line-flag`).
- Report says "enum" / "string (enum: a, b, c)" → `"String"` with \
`"value-choices": ["a", "b", "c"]`.
- Report says "vector" / "list of N values" / "x/y/z" → base type with \
`"list": true` and `min-list-entries` / `max-list-entries`.

## Output file objects

Required:
- `id`, `name`, `path-template`. The `path-template` is a string that can \
reference input `value-key`s (e.g. `"[OUTPUT_PREFIX]_mask.nii.gz"`).

Optional:
- `description`.
- `path-template-stripped-extensions` — list of extensions stripped from a \
referenced File input's value BEFORE substitution, e.g. `[".nii.gz", ".nii"]`.
- `path-template-fallback` — fallback string used if a referenced input is \
optional and unset.
- `media-types` — array of media-type strings naming the format(s) of the \
produced file (see "Media types" below). Optional metadata.

**Do NOT put `optional` on an output-file.** Styx-v1 does not allow it. If \
an output is conditionally produced, model the condition by placing the \
output-file inside the relevant SubCommand branch's `output-files`.

Every `path-template` must be unique across output-files. Paths must NOT \
contain `< > : " \\ | ? *`.

## Media types (optional metadata)

File inputs and output-files MAY carry a `media-types` array describing the \
file format(s), for downstream consumers. It is pure metadata — it NEVER \
affects the command line. Infer it from the file extensions in the reports; \
OMIT the field entirely when the format is unknown or generic. Prefer an IANA \
type where one exists, otherwise an `application/x-<format>` vendor string. \
Common conventions:
- `.nii` / `.nii.gz` → `["application/x-nifti"]`
- `.gii` → `["application/x-gifti"]`; `.mgz` / `.mgh` → `["application/x-mgh"]`
- `.vtk` → `["model/vtk"]`
- `+orig.HEAD` → `["application/x-afni-head"]`; `+orig.BRIK` → `["application/x-afni-brik"]`
- `.1D` / `.txt` → `["text/plain"]`; `.tsv` → `["text/tab-separated-values"]`; \
`.json` → `["application/json"]`
- `.mat` → `["application/x-matlab-data"]`; `.h5` / `.hdf5` → `["application/x-hdf5"]`

## Subcommands — two distinct uses

**A) Mode selection.** If a flag chooses a mode with DIFFERENT downstream \
args per mode (e.g. `--method atlas <atlas_file>` vs \
`--method deep <model_name>`), use a SubCommand union:

```json
{
  "id": "method",
  "value-key": "[METHOD]",
  "type": [
    { "id": "atlas", "command-line": "atlas [ATLAS_FILE]", "inputs": [...] },
    { "id": "deep",  "command-line": "deep [MODEL_NAME]",  "inputs": [...] }
  ]
}
```

Don't use SubCommand for simple enums — that's `value-choices` on a String.

**B) Micro-syntax** (the one that's easy to miss). When a SINGLE argument \
has internal structure — literal brackets, commas, or `x`-separated values \
— you model that structure with a SubCommand, NOT by typing the whole thing \
as a String. Common patterns:

- `--flag [valueA, valueB]` — literal brackets + literal comma
- `--flag [listA, valueB]` where listA is `x`-separated (e.g. \
`100x50x25`) — ANTs-style multi-resolution syntax
- `--flag value1` OR `--flag [value1, value2]` — variable-arity flag; use a \
SubCommand UNION with one branch per arity

**Encoding the literal punctuation:**

- Outer `[...]` literal brackets: wrap the SubCommand's `command-line` in \
extra brackets. `"command-line": "[[VALUE_A][VALUE_B]]"` renders as \
`[a_rendered][b_rendered]`.
- Literal `,` between values: on the SECOND input, set \
`"command-line-flag": ","` AND `"command-line-flag-separator": ""`. The \
"flag" is a bare comma with no space.
- `x`-separated list: type the input as `"list": true, \
"list-separator": "x"`.

**Worked example:** ANTs' `--output` accepts either `file.nii.gz` OR \
`[corrected.nii.gz,bias.nii.gz]`. Model as a SubCommand union, with each \
branch declaring its own `output-files`:

```json
{
  "id": "output",
  "name": "Output",
  "value-key": "[OUTPUT_MODE]",
  "command-line-flag": "--output",
  "type": [
    {
      "id": "single_output",
      "command-line": "[CORRECTED_PATH]",
      "inputs": [
        { "id": "corrected_path", "name": "Corrected image path",
          "type": "String", "value-key": "[CORRECTED_PATH]" }
      ],
      "output-files": [
        { "id": "corrected_image", "name": "Corrected image",
          "path-template": "[CORRECTED_PATH]" }
      ]
    },
    {
      "id": "corrected_plus_bias",
      "command-line": "[[CORRECTED_PATH][BIAS_PATH]]",
      "inputs": [
        { "id": "corrected_path", "name": "Corrected image path",
          "type": "String", "value-key": "[CORRECTED_PATH]" },
        { "id": "bias_path", "name": "Bias field path",
          "type": "String", "value-key": "[BIAS_PATH]",
          "command-line-flag": ",", "command-line-flag-separator": "" }
      ],
      "output-files": [
        { "id": "corrected_image", "name": "Corrected image",
          "path-template": "[CORRECTED_PATH]" },
        { "id": "bias_image", "name": "Bias field image",
          "path-template": "[BIAS_PATH]" }
      ]
    }
  ]
}
```

**Worked example (micro-syntax without union):** ANTs' \
`--convergence [100x50x25,0.0001]` always has bracket + x-list + comma + \
float. Single SubCommand (no union needed, only one shape):

```json
{
  "id": "convergence",
  "value-key": "[CONVERGENCE]",
  "command-line-flag": "--convergence",
  "optional": true,
  "type": {
    "id": "convergence_parts",
    "command-line": "[[ITERATIONS][THRESHOLD]]",
    "inputs": [
      { "id": "iterations", "name": "Iterations per resolution",
        "type": "Number", "integer": true,
        "list": true, "list-separator": "x",
        "value-key": "[ITERATIONS]" },
      { "id": "threshold", "name": "Convergence threshold",
        "type": "Number", "integer": false,
        "command-line-flag": ",", "command-line-flag-separator": "",
        "value-key": "[THRESHOLD]",
        "optional": true }
    ]
  }
}
```

**Rule of thumb:** if the interface report shows a syntax with literal \
brackets, commas, or `x`-separated values, reach for SubCommand. Typing it \
as a plain String loses the structure and is rarely correct.

## Output discipline

Output ONLY the JSON descriptor. No prose, no markdown fences, no \
commentary. The first character of your response must be `{` and the last \
must be `}`.

## Simple example

For a hypothetical `image_convert` tool (remember: do NOT emit `name` or \
`schema-version` — the runtime adds those):

{
  "description": "Converts between image formats.",
  "command-line": "image_convert [INPUT] [OUTPUT] [COMPRESSION]",
  "inputs": [
    {
      "id": "input_file",
      "name": "Input image",
      "description": "DICOM image to convert.",
      "type": "File",
      "value-key": "[INPUT]",
      "media-types": ["application/dicom"]
    },
    {
      "id": "output_file",
      "name": "Output path",
      "description": "Where to write the converted image.",
      "type": "String",
      "value-key": "[OUTPUT]"
    },
    {
      "id": "compression",
      "name": "Compression level",
      "description": "Level 0-9 (tool default: 6).",
      "type": "Number",
      "integer": true,
      "minimum": 0,
      "maximum": 9,
      "command-line-flag": "-c",
      "value-key": "[COMPRESSION]",
      "optional": true
    }
  ],
  "output-files": [
    {
      "id": "converted_image",
      "name": "Converted image",
      "path-template": "[OUTPUT].nii.gz",
      "media-types": ["application/x-nifti"]
    }
  ]
}
"""


async def author_boutiques(
    tool_name: str,
    interface_report: str,
    output_report: str,
    model: str = DEFAULT_MODEL,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> str:
    """Produce a Boutiques (Styx v1) descriptor from Explorer reports.

    Returns canonical JSON (pretty-printed, alias-keyed, ``null`` fields
    omitted). Raises ``ValueError`` if validation still fails after
    ``max_retries`` correction attempts.
    """
    user_message = (
        f"Produce the Boutiques descriptor for the tool '{tool_name}'.\n\n"
        f"## Interface report\n\n{interface_report}\n\n"
        f"## Output report\n\n{output_report}"
    )
    messages: list[dict] = [
        {"role": "system", "content": BOUTIQUES_AUTHOR_PROMPT},
        {"role": "user", "content": user_message},
    ]

    start = time.monotonic()
    prompt_tokens = completion_tokens = 0
    attempts_used = 0
    try:
        for attempt in range(max_retries + 1):
            attempts_used = attempt + 1
            logger.info(f"[author] attempt {attempt + 1}/{max_retries + 1}")
            raw, p, c = await _complete(messages, model)
            prompt_tokens += p
            completion_tokens += c
            json_text = _extract_json(raw)

            data = None  # bound for all paths; only read when errors_fmt is empty
            try:
                data = json.loads(json_text)
            except json.JSONDecodeError as e:
                errors_fmt = [f"output is not valid JSON: {e}"]
            else:
                if not isinstance(data, dict):
                    errors_fmt = ["descriptor must be a JSON object"]
                else:
                    data["name"] = tool_name
                    data["schema-version"] = SCHEMA_VERSION
                    errors = validate(data)
                    errors_fmt = [e.format() for e in errors]

            if not errors_fmt:
                logger.info("[author] descriptor valid")
                return json.dumps(data, indent=2)

            if attempt == max_retries:
                raise ValueError(
                    f"[author] descriptor still invalid after {max_retries} retries:\n"
                    + "\n".join(f"- {e}" for e in errors_fmt)
                )

            logger.warning(
                f"[author] {len(errors_fmt)} error(s), retrying:\n"
                + "\n".join(f"  - {e}" for e in errors_fmt)
            )
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "The descriptor above failed validation:\n\n"
                        + "\n".join(f"- {e}" for e in errors_fmt)
                        + "\n\nProduce a corrected descriptor. Output only JSON, "
                        "no commentary."
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


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def _extract_json(text: str) -> str:
    """Strip optional markdown fences so pydantic gets just the JSON body."""
    text = text.strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text)
    return text.strip()
