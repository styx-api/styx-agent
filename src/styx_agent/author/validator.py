"""Hand-rolled validator for Styx-v1 Boutiques descriptors.

The Boutiques/Styx-v1 schema is frozen; after this project we move to argtype.
This validator replaces pydantic + its Union-error noise with targeted,
LLM-actionable error messages covering both structural rules and semantic
checks. Every error carries an optional ``hint`` suitable for feeding back
into the author loop as correction context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Regex and constants
# ---------------------------------------------------------------------------

ID_PATTERN = re.compile(r"^[0-9_a-zA-Z]+$")
VALUE_KEY_PATTERN = re.compile(r"^\[[A-Z0-9_]+\]$")
_COMMAND_KEY_FIND = re.compile(r"\[([A-Z0-9_]+)\]")
_PATH_TEMPLATE_INVALID = re.compile(r"[<>:\"|?*]")

SCHEMA_VERSION = "0.5+styx"
PRIMITIVE_TYPES = {"File", "String", "Number", "Flag"}

_BASE_INPUT_FIELDS = {"id", "name", "description", "value-key", "type", "optional"}
_FLAGGED_FIELDS = {"command-line-flag", "command-line-flag-separator"}
_LIST_FIELDS = {"list", "list-separator", "min-list-entries", "max-list-entries"}
_TYPE_FIELDS: dict[str, set[str]] = {
    "File": {"mutable", "resolve-parent", "media-types"},
    "String": {"value-choices", "default-value"},
    "Number": {"integer", "minimum", "maximum", "value-choices", "default-value"},
    "Flag": {"command-line-flag", "default-value"},
}

_OUTPUT_REQUIRED = {"id", "path-template"}
_OUTPUT_ALLOWED = {
    "id", "name", "description", "path-template",
    "path-template-stripped-extensions", "path-template-fallback",
    "media-types",
}

_SUBCOMMAND_ALLOWED = {"id", "name", "description", "command-line", "inputs", "output-files"}
_SUBCOMMAND_REQUIRED = {"id", "command-line"}

# Fields the author (LLM) is expected to produce.
_TOP_REQUIRED = {"description", "command-line", "inputs"}
_TOP_OPTIONAL = {"output-files", "stdout-output", "stderr-output"}

# Fields injected or populated outside the author's scope but still
# legitimate Styx-v1 fields. The validator accepts them; the prompt
# does not invite the model to emit them.
_TOP_EXTERNAL = {"name", "schema-version", "author", "url"}

_TOP_ALLOWED = _TOP_REQUIRED | _TOP_OPTIONAL | _TOP_EXTERNAL

_STDIO_ALLOWED = {"id", "name", "description"}


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

@dataclass
class ValidationError:
    path: str
    message: str
    hint: str | None = None

    def format(self) -> str:
        base = f"{self.path}: {self.message}" if self.path else self.message
        if self.hint:
            return f"{base}\n    hint: {self.hint}"
        return base


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def validate(data: Any) -> list[ValidationError]:
    """Validate a descriptor dict; return a list of errors (empty = valid)."""
    errors: list[ValidationError] = []
    if not isinstance(data, dict):
        errors.append(ValidationError("", "descriptor must be a JSON object"))
        return errors

    _check_top_level(data, errors)
    _check_inputs(data.get("inputs"), errors, "inputs")
    _check_output_files(data.get("output-files"), errors, "output-files")
    _check_stdio_output(data.get("stdout-output"), errors, "stdout-output")
    _check_stdio_output(data.get("stderr-output"), errors, "stderr-output")
    _check_local_command_line(data, errors, path_prefix="", require_name_prefix=True)

    return errors


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def _check_top_level(data: dict, errors: list[ValidationError]) -> None:
    for req in _TOP_REQUIRED | {"name", "schema-version"}:
        if req not in data:
            errors.append(ValidationError(req, f"missing required top-level field '{req}'"))

    schema_ver = data.get("schema-version")
    if schema_ver is not None and schema_ver != SCHEMA_VERSION:
        errors.append(ValidationError(
            "schema-version",
            f"must be the literal string {SCHEMA_VERSION!r}, got {schema_ver!r}",
        ))

    name = data.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        errors.append(ValidationError("name", "must be a non-empty string"))

    cl = data.get("command-line")
    if cl is not None and (not isinstance(cl, str) or not cl.strip()):
        errors.append(ValidationError("command-line", "must be a non-empty string"))

    inputs = data.get("inputs")
    if inputs is not None and not isinstance(inputs, list):
        errors.append(ValidationError("inputs", "must be an array"))

    for key in sorted(set(data) - _TOP_ALLOWED):
        errors.append(ValidationError(
            key,
            f"top-level field '{key}' is not permitted in Styx-v1",
            hint=f"Allowed top-level fields: {sorted(_TOP_REQUIRED | _TOP_OPTIONAL)} (emitted by you) plus name/schema-version (injected automatically).",
        ))


# ---------------------------------------------------------------------------
# Inputs (recursive for SubCommand)
# ---------------------------------------------------------------------------

def _check_inputs(inputs: Any, errors: list[ValidationError], path: str) -> None:
    if inputs is None:
        return
    if not isinstance(inputs, list):
        errors.append(ValidationError(path, "must be an array"))
        return
    for i, inp in enumerate(inputs):
        _check_input(inp, errors, f"{path}[{i}]")


def _check_input(inp: Any, errors: list[ValidationError], path: str) -> None:
    if not isinstance(inp, dict):
        errors.append(ValidationError(path, "input must be an object"))
        return

    for req in ("id", "name", "value-key", "type"):
        if req not in inp:
            errors.append(ValidationError(f"{path}.{req}", f"missing required field '{req}'"))

    id_val = inp.get("id")
    if id_val is not None and not (isinstance(id_val, str) and ID_PATTERN.match(id_val)):
        errors.append(ValidationError(
            f"{path}.id",
            f"must match {ID_PATTERN.pattern} (alphanumeric + underscore, non-empty), got {id_val!r}",
        ))

    vk = inp.get("value-key")
    if vk is not None and not (isinstance(vk, str) and VALUE_KEY_PATTERN.match(vk)):
        errors.append(ValidationError(
            f"{path}.value-key",
            f"must match {VALUE_KEY_PATTERN.pattern} ([UPPER_CASE_UNDERSCORES] in brackets), got {vk!r}",
        ))

    type_val = inp.get("type")

    if isinstance(type_val, dict):
        _check_input_subcommand(inp, type_val, errors, path, union=False)
    elif isinstance(type_val, list):
        _check_input_subcommand(inp, type_val, errors, path, union=True)
    elif isinstance(type_val, str):
        if type_val not in PRIMITIVE_TYPES:
            errors.append(ValidationError(
                f"{path}.type",
                f"invalid type {type_val!r}",
                hint=f"Valid primitive types: {sorted(PRIMITIVE_TYPES)}. Or a SubCommandType object / list for union.",
            ))
            return
        _check_input_primitive(inp, type_val, errors, path)
    elif type_val is not None:
        errors.append(ValidationError(
            f"{path}.type",
            f"type must be a string, object, or array; got {type(type_val).__name__}",
        ))


def _check_input_primitive(
    inp: dict, type_val: str, errors: list[ValidationError], path: str
) -> None:
    allowed = _BASE_INPUT_FIELDS | _TYPE_FIELDS[type_val]
    is_flagged = "command-line-flag" in inp
    is_list = inp.get("list") is True
    if is_flagged:
        allowed |= _FLAGGED_FIELDS
    if is_list:
        allowed |= _LIST_FIELDS

    if type_val == "Flag" and "command-line-flag" not in inp:
        errors.append(ValidationError(
            path,
            "Flag inputs require 'command-line-flag'",
            hint="Add e.g. \"command-line-flag\": \"-v\".",
        ))
    if type_val == "Flag" and is_list:
        errors.append(ValidationError(
            path,
            "Flag type cannot be a list",
        ))
    if "list" in inp and inp["list"] is not True:
        errors.append(ValidationError(
            f"{path}.list",
            "if present, 'list' must be literal true",
            hint="Omit the field when the input is not a list.",
        ))

    if inp.get("optional") and "default-value" in inp:
        errors.append(ValidationError(
            path,
            "optional inputs should not set 'default-value'",
            hint="'default-value' is for overriding wrapper defaults, not documenting the tool's internal default. Put the tool's default in 'description' instead.",
        ))

    mn = inp.get("minimum")
    mx = inp.get("maximum")
    if isinstance(mn, (int, float)) and isinstance(mx, (int, float)) and mn > mx:
        errors.append(ValidationError(path, f"minimum ({mn}) > maximum ({mx})"))

    n1 = inp.get("min-list-entries")
    n2 = inp.get("max-list-entries")
    if isinstance(n1, (int, float)) and isinstance(n2, (int, float)) and n1 > n2:
        errors.append(ValidationError(
            path, f"min-list-entries ({n1}) > max-list-entries ({n2})"
        ))

    if type_val == "File":
        _check_media_types(inp, errors, path)

    for key in sorted(set(inp) - allowed):
        errors.append(ValidationError(
            f"{path}.{key}",
            f"field '{key}' not allowed for type {type_val!r}",
            hint=_field_hint(key, type_val, is_flagged, is_list),
        ))


def _field_hint(key: str, type_val: str, is_flagged: bool, is_list: bool) -> str | None:
    if key in _LIST_FIELDS and not is_list:
        return "Set 'list: true' first to use list-related fields."
    if key in _FLAGGED_FIELDS and not is_flagged:
        return "This field requires 'command-line-flag' to also be set."
    if key == "integer" and type_val != "Number":
        return "'integer' is only valid on Number inputs."
    if key in {"mutable", "resolve-parent", "media-types"} and type_val != "File":
        return f"'{key}' is only valid on File inputs."
    if key == "value-choices" and type_val not in ("String", "Number"):
        return "'value-choices' is valid on String or Number inputs only."
    return None


def _check_media_types(obj: dict, errors: list[ValidationError], path: str) -> None:
    """Validate the optional ``media-types`` field on File inputs / output-files.

    It is metadata describing the file format(s) the input accepts or the output
    produces, for downstream consumers (it does not affect the command line).
    """
    mt = obj.get("media-types")
    if mt is None:
        return
    if not isinstance(mt, list) or not mt:
        errors.append(ValidationError(
            f"{path}.media-types",
            "if present, 'media-types' must be a non-empty array of strings",
            hint='e.g. ["application/x-nifti", "application/gzip"]. Omit it entirely if unknown.',
        ))
        return
    for j, m in enumerate(mt):
        if not isinstance(m, str) or not m.strip():
            errors.append(ValidationError(
                f"{path}.media-types[{j}]",
                f"each media type must be a non-empty string, got {m!r}",
            ))


def _check_input_subcommand(
    inp: dict, type_val: Any, errors: list[ValidationError], path: str, union: bool
) -> None:
    allowed = set(_BASE_INPUT_FIELDS)
    is_flagged = "command-line-flag" in inp
    is_list = inp.get("list") is True
    if is_flagged:
        allowed |= _FLAGGED_FIELDS
    if is_list:
        allowed |= _LIST_FIELDS
    for key in sorted(set(inp) - allowed):
        errors.append(ValidationError(
            f"{path}.{key}",
            f"field '{key}' not allowed on a SubCommand-typed input",
        ))

    if union:
        if not isinstance(type_val, list):
            return
        if len(type_val) < 2:
            errors.append(ValidationError(
                f"{path}.type",
                "SubCommand union must have at least 2 branches",
                hint="For a single branch, set 'type' to the SubCommandType object (not wrapped in a list).",
            ))
        for i, branch in enumerate(type_val):
            _check_subcommand(branch, errors, f"{path}.type[{i}]")
    else:
        _check_subcommand(type_val, errors, f"{path}.type")


def _check_subcommand(sc: Any, errors: list[ValidationError], path: str) -> None:
    if not isinstance(sc, dict):
        errors.append(ValidationError(path, "SubCommand must be an object"))
        return

    for req in _SUBCOMMAND_REQUIRED:
        if req not in sc:
            errors.append(ValidationError(f"{path}.{req}", f"missing required field '{req}'"))

    id_val = sc.get("id")
    if id_val is not None and not (isinstance(id_val, str) and ID_PATTERN.match(id_val)):
        errors.append(ValidationError(
            f"{path}.id",
            f"must match {ID_PATTERN.pattern}, got {id_val!r}",
        ))

    for key in sorted(set(sc) - _SUBCOMMAND_ALLOWED):
        errors.append(ValidationError(f"{path}.{key}", f"field '{key}' not allowed on SubCommand"))

    if "inputs" in sc:
        _check_inputs(sc["inputs"], errors, f"{path}.inputs")
    if "output-files" in sc:
        _check_output_files(sc["output-files"], errors, f"{path}.output-files")

    _check_local_command_line(sc, errors, path_prefix=path, require_name_prefix=False)


# ---------------------------------------------------------------------------
# Output files
# ---------------------------------------------------------------------------

def _check_output_files(outputs: Any, errors: list[ValidationError], path: str) -> None:
    if outputs is None:
        return
    if not isinstance(outputs, list):
        errors.append(ValidationError(path, "must be an array"))
        return
    seen: dict[str, list[str]] = {}
    for i, out in enumerate(outputs):
        _check_output_file(out, errors, f"{path}[{i}]")
        if isinstance(out, dict):
            template = out.get("path-template")
            if isinstance(template, str):
                seen.setdefault(template, []).append(str(out.get("id", "<unknown>")))
    for template, ids in seen.items():
        if len(ids) > 1:
            errors.append(ValidationError(
                path,
                f"duplicate path-template {template!r} used by: {ids}",
                hint="Each output file must have a unique path-template.",
            ))


def _check_output_file(out: Any, errors: list[ValidationError], path: str) -> None:
    if not isinstance(out, dict):
        errors.append(ValidationError(path, "output file must be an object"))
        return

    for req in _OUTPUT_REQUIRED:
        if req not in out:
            errors.append(ValidationError(f"{path}.{req}", f"missing required field '{req}'"))

    id_val = out.get("id")
    if id_val is not None and not (isinstance(id_val, str) and ID_PATTERN.match(id_val)):
        errors.append(ValidationError(
            f"{path}.id",
            f"must match {ID_PATTERN.pattern}, got {id_val!r}",
        ))

    template = out.get("path-template")
    if template is not None:
        if not isinstance(template, str) or not template:
            errors.append(ValidationError(f"{path}.path-template", "must be a non-empty string"))
        elif _PATH_TEMPLATE_INVALID.search(template):
            errors.append(ValidationError(
                f"{path}.path-template",
                "contains invalid characters (forbidden: < > : \" | ? *)",
            ))

    if "optional" in out:
        errors.append(ValidationError(
            f"{path}.optional",
            "Output files do not accept 'optional' in Styx-v1",
            hint="If the file is conditionally produced, move it inside the relevant SubCommand branch's 'output-files'.",
        ))

    _check_media_types(out, errors, path)

    for key in sorted(set(out) - _OUTPUT_ALLOWED - {"optional"}):
        errors.append(ValidationError(f"{path}.{key}", f"field '{key}' not allowed on output-files"))


# ---------------------------------------------------------------------------
# stdout-output / stderr-output
# ---------------------------------------------------------------------------

def _check_stdio_output(stdio: Any, errors: list[ValidationError], path: str) -> None:
    if stdio is None:
        return
    if not isinstance(stdio, dict):
        errors.append(ValidationError(path, "must be an object"))
        return
    if "id" not in stdio:
        errors.append(ValidationError(f"{path}.id", "missing required field 'id'"))
    id_val = stdio.get("id")
    if id_val is not None and not (isinstance(id_val, str) and ID_PATTERN.match(id_val)):
        errors.append(ValidationError(
            f"{path}.id",
            f"must match {ID_PATTERN.pattern}, got {id_val!r}",
        ))
    for key in sorted(set(stdio) - _STDIO_ALLOWED):
        errors.append(ValidationError(f"{path}.{key}", f"field '{key}' not allowed"))


# ---------------------------------------------------------------------------
# Command-line semantics (used at top level and per SubCommand)
# ---------------------------------------------------------------------------

def _check_local_command_line(
    context: dict,
    errors: list[ValidationError],
    path_prefix: str,
    require_name_prefix: bool,
) -> None:
    command_line = context.get("command-line")
    if not isinstance(command_line, str):
        return
    inputs = context.get("inputs")
    if not isinstance(inputs, list):
        inputs = []

    cl_path = f"{path_prefix}.command-line" if path_prefix else "command-line"
    inputs_path = f"{path_prefix}.inputs" if path_prefix else "inputs"

    if require_name_prefix:
        name = context.get("name")
        if isinstance(name, str) and name:
            if not (command_line == name or command_line.startswith(f"{name} ")):
                errors.append(ValidationError(
                    cl_path,
                    f"must start with the tool name {name!r}",
                ))

    direct_value_keys: list[str] = []
    for inp in inputs:
        if isinstance(inp, dict):
            vk = inp.get("value-key")
            if isinstance(vk, str):
                direct_value_keys.append(vk)
    defined = set(direct_value_keys)

    for vk in direct_value_keys:
        if vk not in command_line:
            errors.append(ValidationError(
                cl_path,
                f"input value-key {vk!r} is not referenced in command-line",
                hint="Every input at this level must have its value-key appear in the command-line template.",
            ))

    referenced = {f"[{m}]" for m in _COMMAND_KEY_FIND.findall(command_line)}
    for missing in sorted(referenced - defined):
        errors.append(ValidationError(
            cl_path,
            f"command-line references undefined value-key {missing!r}",
            hint="Every [KEY] in the template must correspond to a direct-level input's value-key.",
        ))

    all_occurrences = _COMMAND_KEY_FIND.findall(command_line)
    seen_occ = set()
    dupes = set()
    for k in all_occurrences:
        if k in seen_occ:
            dupes.add(f"[{k}]")
        seen_occ.add(k)
    for d in sorted(dupes):
        errors.append(ValidationError(cl_path, f"value-key {d!r} appears more than once"))

    key_to_ids: dict[str, list[str]] = {}
    for inp in inputs:
        if isinstance(inp, dict):
            vk = inp.get("value-key")
            if isinstance(vk, str):
                key_to_ids.setdefault(vk, []).append(str(inp.get("id", "<unknown>")))
    for vk, ids in key_to_ids.items():
        if len(ids) > 1:
            errors.append(ValidationError(
                inputs_path,
                f"multiple inputs share value-key {vk!r}: {ids}",
                hint="If you intended variants of the same slot, model them as a SubCommand union.",
            ))
