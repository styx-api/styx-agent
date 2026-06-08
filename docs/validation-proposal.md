# Validating Agent-Generated Descriptors at Scale

*Lab discussion draft. The mechanisms below are our current best thinking, not a settled plan — what we most want from this session is pushback on gaps and alternative approaches we haven't considered. Each H2 is a slide; bullets are slide content; prose below is speaker notes.*

---

## The goal

Three overlapping objectives:

1. **Replace** the ~1900 existing NiWrap descriptors with more accurate, validated ones.
2. **Fill gaps** — tools inside covered packages with no descriptor yet.
3. **Expand** to entirely new packages.

Automatic at scale, not bespoke per tool.

NiWrap descriptors today are a mix of hand-writing and one-shot LLM generation, with real bugs (phantom parameters in `3dTstat`, nonexistent `.off` mesh outputs in `bet`). The agent pipeline has to solve all three objectives to be worth the investment.

---

## Current state

- **Pipeline:** Scanner → Explorer → Author (Boutiques + schema validator + retry).
- **Runner:** `styx-agent wrap <tool> <repo>`.
- **Missing:** benchmark, runtime validation, improvement-over-time signal.

---

## Why benchmarking this is hard

- Manual inspection suggests agent output >> NiWrap. But "seems better" isn't defensible.
- **Only 1/3 of the target scope has a reference at all** (replacements). Gap-filling and new packages have nothing to diff against.
- NiWrap itself has bugs → noisy reference even where it exists.
- Help text and source are already Explorer inputs → circular validation.
- Human labels are a peer signal, not ground truth — same blind spots as the agent.

**Takeaway:** validation must be **intrinsic** — measure the descriptor against the tool itself, not against an oracle.

---

## What the benchmark is for

Not just a one-shot quality check. It needs to support:

- **Quality gate at scale.** Automated pass/fail per descriptor.
- **Improvement over time.** Catch regressions from prompt changes and model upgrades.
- **Model comparison across agent roles.** Flash vs. Pro vs. Sonnet vs. Haiku — different models may be the right fit for Scanner, Explorer, Author, Validator individually.
- **Cost/quality tradeoff.** Cheapest model that hits the quality bar per role wins.

This use case — not just QA — is why the benchmark has to be **repeatable, cheap, and automated**. One-shot manual labeling doesn't support any of it.

---

## Three intrinsic checks (current proposal)

Best-guess mechanisms — each covers a distinct failure mode. **What else should be on this list?**

| Mechanism | Catches | Cost |
|---|---|---|
| **Evidence fields** | Fabrication, miscitation | Cheap, static |
| **Behavioral probing** | Runtime mismatch | Interface: moderate / Output: high |
| **Synthetic repos** | Capability gaps | Cheap, dev loop |

None sufficient alone. Together they triangulate quality with no reference. But the three-mechanism shape is a hypothesis — a better framing or a fourth check we're missing could change the plan.

---

## Evidence fields — static grounding

- Every claim carries a source reference: `file:line`.
- Validator checks: citation resolves, cited code supports the claim.
- Fabrication becomes cheaply detectable: "no citation = made up".
- Descriptors become self-documenting for humans.

**Blind spots:**
- **Completeness.** Audits claims *made*, not claims *missing*. Detecting a flag the Author forgot needs independent enumeration.
- **Diffuse evidence.** Microsyntax, macro/template expansions, and logic spread across tokenizer + parser + dispatch can't be pinned to a single `file:line`. Multi-reference evidence helps but the judge has to trace across it — more expensive, lower accuracy.

Per-claim LLM judging is the one place the verification-vs-generation asymmetry really works — narrow context, single question, high accuracy — but only for claims that exist and whose evidence is local.

---

## Behavioral probing — runtime truth

One probe per claim. Sparse, not exhaustive. **Two cost regimes:**

**Interface probes — cheap.** Short-timeout execution (5–10s). Tool doesn't need to finish.
- Flag exists → invoke, confirm accepted.
- Required → omit, confirm failure.
- Type constraint → pass invalid, confirm rejection.

**Output probes — expensive.** Tool must **run to completion** to verify a file was produced. Seconds to many minutes per probe. Where the infra cost concentrates.

**Completeness check:** rule-based parse of `tool --help`, compare flag list to descriptor. Mechanical, no LLM. Covers ~60–70% of claims for free and — importantly — surfaces missing claims that evidence fields can't.

Linear in descriptor size, not configuration space. Output-heavy descriptors drive runtime; output probing may need to be sampled.

---

## Synthetic repos — capability + dev loop

- Fake tools with ground truth **known by construction**.
- Targeted capability tests: can the agent handle macro-expanded flags, subcommands, microsyntax?
- Fast iteration: prompt change → suite runs in seconds.
- Regression corpus grows from real failures.
- Also gives Scanner a ground truth it otherwise lacks.

Risk: overfitting. Hold some synthetics as eval, keep adding new ones from real failures, cross-check against real-repo performance.

---

## Where validation breaks down

**Diffing fails when multiple encodings are equally valid:**

- **Subcommand nesting** — flat-with-prefix vs. truly nested; both correct.
- **Microsyntax** (`-t Affine[0.1]`, `-kernel gaussian 5.0`) — template string vs. compound type vs. enum-with-params.

**What still works:** per-subcommand behavioral probing; executing the concrete microsyntax string.

**Separately — new packages.** Scanner is the entry point; its robustness caps the "automatic at scale" promise.

**Rule:** report metrics per tier. Don't average.

---

## Pragmatic choices

- **Descriptions dropped from automated score.** No verification asymmetry vs. generation.
- **Sparse probing, not exhaustive.** Test the descriptor, not the tool.
- **Evidence-first changes Authoring.** Grounded generation beats pattern-matched output.
- **No headline accuracy number.** Stratified reporting only. Honest but harder to compress.

---

## Proposed sequence

1. Extend Author with evidence fields per claim.
2. Static validator: per-claim LLM judge confirming citation support.
3. Synthetic test corpus (~20 cases across tiers).
4. Behavioral harness: minimal inputs per file type + sparse probing.
5. Gap detection *inside* existing NiWrap descriptors: missing arguments, missing outputs, flattened subcommands and microsyntax the agent recovers.
6. Stratified benchmark report across tiers, packages, coverage categories.
7. Full sweep across *all* registered packages so prompts don't overfit one package's conventions; triage; feed failures into synthetic corpus.
8. Expansion track: onboard 2–3 new packages as Scanner stress test.

---

## Open questions — and where we want input

**Specific uncertainties in the current plan:**
- How far do we trust the judge on hard tools where Author also fails?
- Behavioral harness now, or scale evidence-only first and add runtime later?
- Output probes require full tool runs — sample them or budget for universal coverage?
- How to measure description quality without automated scoring?
- Integration path: automated PRs into NiWrap, side-by-side tree, or wait for argtype?

**What we most want from this session:**
- Validation mechanisms we haven't considered — is there a fourth check?
- Alternative framings of the benchmark problem — is "per-claim probing" the right shape at all?
- Failure modes we're likely to miss in the current design.
- Prior work in adjacent fields (program synthesis, API schema inference, compiler testing) that we should borrow from.

**Decision for today:** green-light starting with evidence fields + static validator, or iterate on scope first?
