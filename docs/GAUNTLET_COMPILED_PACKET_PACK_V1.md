# Gauntlet Pack v1 - Compiled Packet Contract Audit

## Scope

Attack surface is compiled packet only:

- manifest structure
- claim bindings
- subject binding
- verifier output contract
- gate script behavior
- admissibility contract

Out of scope:

- proof pack verifier internals
- VendorQ workflow
- broader Assay product claims
- subject semantic truth beyond the layer's stated guarantees

## Test Philosophy

This gauntlet is not trying to prove the compiled packet layer perfect.

It is trying to determine whether the current contract is:

- correctly enforced
- too weak for buyer-facing trust use
- or appropriately limited by design

The central question is contract geometry:

- does the admissible region contain packets that are structurally valid but semantically vacuous?
- if so, are those packets acceptable under the current contract, only under a narrower product promise, or not acceptable at all?

Recommended contract split:

- `A_schema` = machine gate pass
- `A_trust` = sufficient for buyer-facing trust use

The gauntlet should treat those as separate predicates unless the product docs explicitly unify them.

## Headline Question

Can a packet pass the gate while proving nothing useful?

That is the primary failure mode to test.

## Finding Buckets

Every finding must be classified as exactly one:

- `Code bug`
  - Behavior contradicts the stated current contract or fails closed/open incorrectly.
- `Contract gap`
  - Behavior is allowed by the current contract but is too weak or misleading for the intended buyer/compliance use.
- `Accepted limitation`
  - The layer does not and should not prove this property.
- `Out of scope`
  - The finding targets a different layer than compiled packet verification/gating.

## Minimal Formal Semantics

Let:

- `V` = structurally verified packets
- `C` = sufficiently covered packets
- `A` = admissible packets under current policy
- `M` = semantically meaningful packets for the intended buyer use case

The gauntlet is checking whether:

- `A` is too broad relative to `M`
- `V ∩ A` contains vacuous objects
- admissibility is monotone with respect to support quality
- `A_schema` is being mistaken for `A_trust`

Working assumptions:

- `Integrity(packet) ∈ {INTACT, DEGRADED, TAMPERED, INVALID}`
- `Completeness(packet) ∈ {COMPLETE, PARTIAL, INCOMPLETE}`
- `Admissible(packet, policy) ∈ {true, false}`
- `GateStatus(packet) ∈ {PASS, FAIL}`
- `TrustStatus(packet) ∈ {SUFFICIENT, INSUFFICIENT, NOT_APPLICABLE}`

Recommended semantic guardrails:

- improving support should not make a packet less admissible, holding integrity fixed
- replacing `SUPPORTED` claims with weaker statuses should not improve effective standing
- `OUT_OF_SCOPE` should have an explicit rule for how it affects the completeness denominator
- `NON_CLAIM` should not silently inflate evidence coverage

## Claim Universe

A questionnaire item is either evidence-bearing or non-evidence-bearing.

- Evidence-bearing items are eligible for evidentiary support and may be classified as `SUPPORTED`, `PARTIAL`, `UNSUPPORTED`, or `OUT_OF_SCOPE`.
- Non-evidence-bearing items are not eligible for evidentiary support and must be classified as `NON_CLAIM`.
- `OUT_OF_SCOPE` means the item is evidence-bearing in principle, but intentionally excluded from the current packet.
- `NON_CLAIM` means the item is not the kind of statement this packet model can prove.

This distinction exists so completeness and admissibility are computed over the right universe.

## Status Set Properties

For each questionnaire item, status values are intended to be:

- mutually exclusive: exactly one status applies
- collectively exhaustive: every item must receive a status
- operationally decidable: a reviewer should be able to determine the correct status from the packet model and rubric, without inventing new categories

Allowed statuses:

- `SUPPORTED`
- `PARTIAL`
- `UNSUPPORTED`
- `OUT_OF_SCOPE`
- `NON_CLAIM`

## Completeness Denominator

For Pack v1, completeness is computed over all questionnaire items except `NON_CLAIM`.

That means:

- `NON_CLAIM` items are excluded from the denominator because they are not evidence-bearing
- `OUT_OF_SCOPE` items remain in the denominator unless and until the contract explicitly changes
- `SUPPORTED`, `PARTIAL`, and `UNSUPPORTED` all count as evidence-bearing classifications

This choice is deliberate: it prevents `NON_CLAIM` from inflating coverage while ensuring `OUT_OF_SCOPE` cannot silently shrink the evaluation universe without policy acknowledgment.

Future revisions may split completeness into classification completeness and support completeness, but Pack v1 uses one completeness axis for simplicity.

## Severity Levels

- `Blocker`
  - invalid or malformed output can pass the gate
  - parser/schema ambiguity can be mistaken for success
  - a packet that violates the current admissibility contract still passes
- `High`
  - a packet passes the gate but is vacuous under the intended buyer/compliance use case
  - the system allows a packet that is technically admissible but commercially misleading
- `Medium`
  - the system fails closed correctly, but diagnostics are too ambiguous to distinguish parse, schema, and policy failure
  - behavior is safe but hard to operationalize
- `Accepted risk`
  - structurally valid `subject_digest` that cannot be semantically validated at this layer
  - authored claim truthfulness beyond structural linkage

## Required Report Fields

For each fixture, fill in:

- `fixture`
- `purpose`
- `verifier_expected_result`
- `gate_expected_result`
- `observed_result`
- `current_contract_status` (`allowed` / `blocked` / `ambiguous`)
- `blocking_scope` (`release-blocking` / `buyer-risk` / `observability-only`)
- `failure_basis` (`parse` / `schema_type` / `integrity` / `completeness` / `policy` / `n/a`)
- `allowed_interpretation` (`allowed and acceptable` / `allowed but undesirable` / `allowed and incompatible with buyer-facing use` / `n/a`)
- `invariant_impact` (free text describing which semantic guardrail was stressed or violated)
- `classification` (`code bug` / `contract gap` / `accepted limitation` / `out of scope`)
- `severity`
- `buyer_facing_implication`
- `recommended_action`

## Fixture Set

Rows 7 through 11 are gate-boundary fixtures.

For those rows, the producer is only being checked for output shape. The gate membrane is the actual target.

| # | Fixture | Purpose | Producer Output Expectation | Gate Expected Result | Risk Tested |
|---|---|---|---|---|---|
| 1 | Empty questionnaire | Test whether a structurally valid but content-empty packet can be admissible. | `INTACT`; completeness likely `INCOMPLETE` or equivalent low-coverage result | Should fail if admissibility requires substantive content; may pass if admissibility only checks integrity + subject + bundle mode | passes the gate, proves nothing |
| 2 | No bindings | Test whether claims without authored evidence linkage can still appear acceptable. | Integrity may still be `INTACT`; completeness should reflect unsupported coverage | Should fail for a meaningful compliance packet; if allowed, likely a contract gap | no authored evidence linkage |
| 3 | All claims `NON_CLAIM` | Test whether a packet made entirely of non-verifiable statements can pass. | Integrity likely `INTACT`; completeness should not imply real support | Likely should fail for buyer-facing compliance use; if pass, likely allowed but undesirable | non-verifiable-only packet |
| 4 | All claims `OUT_OF_SCOPE` | Test the vacuous-but-clean exclusion case. | Structurally valid; completeness may remain non-zero unless exclusions are modeled carefully | Critical contract decision: if pass, report whether that is acceptable for the current product promise | passes the gate, proves nothing |
| 5 | Mix of `OUT_OF_SCOPE` and `UNSUPPORTED` | Test whether exclusions can hide real evidence gaps. | Integrity valid; completeness partial/incomplete | Likely fail or classify as weak packet depending on policy | exclusions masking gaps |
| 6 | One `SUPPORTED`, everything else `OUT_OF_SCOPE` | Test the minimally non-vacuous case. | Integrity `INTACT`; completeness likely partial | May pass under narrow contract; report whether this is technically admissible but too weak for buyer confidence | minimally non-vacuous packet |
| 7 | Valid JSON missing `admissible` | Test gate schema strictness. | Producer emits valid JSON with a missing field; the gate should treat that as a schema failure | Fail closed | schema omission |
| 8 | Valid JSON with `admissible: null` | Test null-handling and type discipline. | Producer emits valid JSON; the gate should reject the null policy field | Fail closed | null handling |
| 9 | Valid JSON with `admissible: ""` | Test empty-string coercion or shell weirdness. | Producer emits valid JSON; the gate should reject the empty string as a policy value | Fail closed | empty-string coercion |
| 10 | Valid JSON with `admissible: "true"` | Test string/boolean confusion. | Producer emits valid JSON; the gate should reject the stringified boolean | Fail closed | string/boolean confusion |
| 11 | Stdout contamination before JSON | Test whether warnings/preamble can break or bypass gate parsing. | Producer emits contaminated output; the gate must not accept a non-JSON preamble as success | Fail closed as parse error; stderr/stdout handling should preserve useful diagnostics | parse boundary contamination |
| 12 | Structurally valid but semantically fake `subject_digest` | Test semantic subject weakness vs true tampering. | Integrity may remain `INTACT` if formatting and signatures are consistent | May pass if the layer only validates structural subject binding | accepted limitation unless semantic subject proof is claimed; report language should say "structural binding intact; semantic referent unproven at this layer" |

## Additional Fixture

| # | Fixture | Purpose | Producer Output Expectation | Gate Expected Result | Risk Tested |
|---|---|---|---|---|---|
| 13 | Valid packet, `admissible: true`, `completeness: PARTIAL`, mostly `UNSUPPORTED` | Test whether the current admissibility contract is too permissive when there is real but weak evidence coverage. | Producer emits a structurally valid packet with weak but non-vacuous coverage | Decide whether this is admissible, merely buyer-risk, or blocked under the current contract | weak-but-not-empty packet |

## Monotonicity Fixtures

These are paired checks. They exist to catch scoring inversions and denominator laundering.

| # | Fixture | Purpose | Producer Output Expectation | Gate Expected Result | Risk Tested |
|---|---|---|---|---|---|
| 14 | Support monotonicity pair | Compare a packet with one `SUPPORTED` claim against the same packet with one additional `UNSUPPORTED` claim upgraded to `SUPPORTED`. | The stronger packet should not be less admissible than the weaker one, holding integrity fixed | PASS/FAIL should not regress when support improves | support monotonicity |
| 15 | Weakening monotonicity pair | Compare a packet with one `SUPPORTED` claim against the same packet weakened to `UNSUPPORTED` or `NON_CLAIM`. | The weakened packet should not gain standing relative to the stronger one | PASS/FAIL should not improve when support weakens | weakening monotonicity |
| 16 | Denominator-control pair | Compare many `UNSUPPORTED` claims against the same claims relabeled `OUT_OF_SCOPE`. | Any admissibility improvement must be explainable by explicit exclusion rules, not silent denominator laundering | Any change in gate result must be attributable to documented policy semantics | denominator laundering |

## Decision Rules

If the result is `allowed`, the report must also say whether it is:

- allowed and acceptable
- allowed but undesirable
- allowed and incompatible with buyer-facing compliance use

If the result is `blocked`, the report must also say whether it is blocked due to:

- parse failure
- schema/type failure
- integrity failure
- completeness weakness
- policy/admissibility failure

At the end of the gauntlet, emit one final contract verdict:

- `Verdict A`
  - Current compiled packet contract is structurally safe and semantically narrow, but not sufficient as a standalone buyer-facing trust artifact.
- `Verdict B`
  - Current compiled packet contract is internally coherent and acceptable for its stated narrow promise, provided product language explicitly disclaims semantic sufficiency.
- `Verdict C`
  - Current compiled packet contract is structurally coherent but semantically too permissive for its stated product promise and should be narrowed before buyer-facing use.
- `Verdict D`
  - Current compiled packet membrane has implementation or schema-boundary unsafety that is release-blocking regardless of product positioning.

## Non-Negotiable Rule

Do not collapse `allowed but undesirable` into `bug`.

That category is where product policy decisions live.

## Output Template

### Fixture: `<name>`

- Purpose:
- Verifier expected result:
- Gate expected result:
- Observed result:
- Current contract status: `allowed` | `blocked` | `ambiguous`
- Blocking scope: `release-blocking` | `buyer-risk` | `observability-only`
- Gate status: `PASS` | `FAIL`
- Trust status: `SUFFICIENT` | `INSUFFICIENT` | `NOT_APPLICABLE`
- Failure basis: `parse` | `schema_type` | `integrity` | `completeness` | `policy` | `n/a`
- Allowed interpretation: `allowed and acceptable` | `allowed but undesirable` | `allowed and incompatible with buyer-facing use` | `n/a`
- Invariant impact:
- Classification: `code bug` | `contract gap` | `accepted limitation` | `out of scope`
- Severity: `blocker` | `high` | `medium` | `accepted risk`
- Buyer-facing implication:
- Recommended action:

## Pack v1 Goal

Determine whether compiled packets can pass structurally and policy-wise while remaining too weak to support a buyer-facing trust decision, and classify each such case as bug, contract gap, or accepted limitation.
