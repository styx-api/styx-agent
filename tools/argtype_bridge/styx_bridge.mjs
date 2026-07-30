#!/usr/bin/env node
// Multi-purpose styx bridge for offline analysis.
//
// Reads a descriptor from stdin and, depending on --emit, prints to stdout:
//   --emit ir        : {ok, errors, warnings, ir}  — the lowered Styx IR (expr+meta)
//   --emit argtype   : {ok, errors, warnings, source} — argtype text (transpile if
//                      input --format is boutiques; re-emit if argtype)
//   --emit validate  : {ok, errors, warnings, n_nodes} — same as argtype_validate.mjs
//
// --format boutiques|argtype  (default: argtype) selects the input frontend.
//
// Always exits 0 with JSON on stdout so the Python side gets parseable output.

import { compile, generateArgtype } from "@styx-api/core";

function arg(name, def) {
  const i = process.argv.indexOf(name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

function readStdin() {
  return new Promise((resolve, reject) => {
    let buf = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => (buf += c));
    process.stdin.on("end", () => resolve(buf));
    process.stdin.on("error", reject);
  });
}

const normDiag = (d) => {
  const o = { message: d?.message ?? String(d) };
  if (d?.location?.line != null) o.line = d.location.line;
  if (d?.location?.column != null) o.column = d.location.column;
  return o;
};

async function main() {
  const format = arg("--format", "argtype");
  const emit = arg("--emit", "validate");
  const src = await readStdin();
  const filename = format === "boutiques" ? "in.json" : "in.argtype";
  const pr = compile(src, { format, filename });
  const errors = (pr.errors ?? []).map(normDiag);
  const warnings = (pr.warnings ?? []).map(normDiag);
  const base = { ok: errors.length === 0, errors, warnings };

  if (emit === "ir") {
    process.stdout.write(JSON.stringify({ ...base, ir: { expr: pr.expr, meta: pr.meta } }));
  } else if (emit === "argtype") {
    let source = null;
    try {
      const out = generateArgtype(pr.expr, pr.meta);
      source = typeof out === "string" ? out : out?.source ?? null;
      for (const w of out?.warnings ?? []) warnings.push(normDiag(w));
    } catch (e) {
      errors.push({ message: `generateArgtype failed: ${e?.message || e}` });
    }
    process.stdout.write(JSON.stringify({ ...base, ok: errors.length === 0, source }));
  } else {
    process.stdout.write(JSON.stringify({ ...base, n_nodes: countNodes(pr.expr) }));
  }
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

main().catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, errors: [{ message: `bridge crash: ${e?.stack || e}` }], warnings: [] }));
});
