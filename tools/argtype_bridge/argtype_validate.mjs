#!/usr/bin/env node
// Validate an argtype document against the argtype parser.
//
// Reads argtype source from stdin, runs the full parse-and-resolve pipeline from
// `@argtype/core`, and prints a single JSON object to stdout:
//
//     {"ok": bool, "errors": [{message, code, line?, column?}],
//      "warnings": [{message, code, line?, column?}], "n_nodes": int}
//
// `ok` is true iff nothing reported an error-severity diagnostic. Warnings never
// flip `ok` but are returned so the caller can surface them. Any crash prints
// {"ok": false, "errors": [{message: "..."}]} and exits 0 so the Python side
// always gets parseable JSON on stdout.
//
// This deliberately does NOT compile. The author's retry loop is a *grammar*
// gate: what belongs in it is "this descriptor is wrong", not "this consumer
// cannot lower it yet". Validating through the styx compiler conflated the two
// and corrected the author against one consumer's lowering policy - a grammar it
// got right but styx could not lower came back as an error, teaching it to avoid
// legal argtype, while a dangling `{name}` output reference passed, because
// lowering does not resolve templates. The parser has the opposite bias, which
// is the correct one here: `resolveReferences` reports that dangling reference
// as an error, and has no opinion at all about lowering.

import {
  inlineAliases,
  parseArgtype,
  partitionDiagnostics,
  resolveAnnotations,
  resolveConstraints,
  resolveMediaTypes,
  resolveOutputs,
  resolvePaths,
  resolveReferences,
} from "@argtype/core";

function readStdin() {
  return new Promise((resolve, reject) => {
    let buf = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => (buf += c));
    process.stdin.on("end", () => resolve(buf));
    process.stdin.on("error", reject);
  });
}

function normalizeDiag(d) {
  // `Diagnostic` carries `line`/`column` as convenience aliases of
  // `span.start`, so there is nothing to dig out of a nested location.
  const out = { message: d?.message ?? String(d), code: d?.code ?? "" };
  if (typeof d?.line === "number") out.line = d.line;
  if (typeof d?.column === "number") out.column = d.column;
  return out;
}

function countNodes(node) {
  if (!node || typeof node !== "object") return 0;
  let n = 1;
  if (Array.isArray(node.children)) for (const c of node.children) n += countNodes(c);
  return n;
}

/**
 * Every pass, in production order, with their diagnostics concatenated.
 *
 * The extension passes (outputs, paths, constraints, media types) run against
 * the resolved document and are where a misapplied annotation surfaces -
 * `.count()` on something that is not a `rep`, `.resolveParent()` on a `str`.
 * Skipping them would make the gate weaker than the compiler's was, which is
 * not the trade being made here.
 */
function validate(src) {
  const parsed = parseArgtype(src);
  if (!parsed.doc) return { diagnostics: parsed.diagnostics, nodes: 0 };

  const inlined = inlineAliases(parsed.doc);
  const resolved = resolveAnnotations(inlined.doc);
  const doc = resolved.doc;

  const diagnostics = [
    ...parsed.diagnostics,
    ...inlined.diagnostics,
    ...resolved.diagnostics,
    ...resolveOutputs(doc).diagnostics,
    ...resolvePaths(doc).diagnostics,
    ...resolveConstraints(doc).diagnostics,
    ...resolveMediaTypes(doc).diagnostics,
    // Runs on the AST rather than the resolved tree, and is the pass that
    // catches a `.output(...)` template naming a node that does not exist.
    ...resolveReferences(inlined.doc).diagnostics,
  ];
  return { diagnostics, nodes: countNodes(doc.root) };
}

async function main() {
  const src = await readStdin();
  const { diagnostics, nodes } = validate(src);
  const { errors, warnings } = partitionDiagnostics(diagnostics);
  process.stdout.write(
    JSON.stringify({
      ok: errors.length === 0,
      errors: errors.map(normalizeDiag),
      warnings: warnings.map(normalizeDiag),
      n_nodes: nodes,
    }),
  );
}

main().catch((e) => {
  process.stdout.write(
    JSON.stringify({ ok: false, errors: [{ message: `bridge crash: ${e?.stack || e}` }], warnings: [] }),
  );
});
