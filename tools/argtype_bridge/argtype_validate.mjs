#!/usr/bin/env node
// Validate an argtype document with the real styx compiler.
//
// Reads argtype source from stdin, compiles it via @styx-api/core's `compile()`
// with the argtype frontend, and prints a single JSON object to stdout:
//
//     {"ok": bool, "errors": [{message, line?, column?}],
//      "warnings": [{message, line?, column?}], "n_nodes": int}
//
// `ok` is true iff the compiler reported zero errors. Warnings never flip `ok`
// but are returned so the caller can surface them (e.g. undeclared extensions).
// Any crash prints {"ok": false, "errors": [{message: "..."}]} and exits 0 so
// the Python side always gets parseable JSON on stdout.

import { compile } from "@styx-api/core";

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
  const out = { message: d?.message ?? String(d) };
  if (d?.location) {
    if (typeof d.location.line === "number") out.line = d.location.line;
    if (typeof d.location.column === "number") out.column = d.location.column;
  }
  return out;
}

function countNodes(expr) {
  if (!expr || typeof expr !== "object") return 0;
  let n = 1;
  const a = expr.attrs ?? {};
  if (Array.isArray(a.nodes)) for (const c of a.nodes) n += countNodes(c);
  if (Array.isArray(a.alts)) for (const c of a.alts) n += countNodes(c);
  if (a.node) n += countNodes(a.node);
  return n;
}

async function main() {
  const src = await readStdin();
  const res = compile(src, { format: "argtype", filename: "descriptor.argtype" });
  const errors = (res.errors ?? []).map(normalizeDiag);
  const warnings = (res.warnings ?? []).map(normalizeDiag);
  process.stdout.write(
    JSON.stringify({
      ok: errors.length === 0,
      errors,
      warnings,
      n_nodes: countNodes(res.expr),
    }),
  );
}

main().catch((e) => {
  process.stdout.write(
    JSON.stringify({ ok: false, errors: [{ message: `bridge crash: ${e?.stack || e}` }], warnings: [] }),
  );
});
