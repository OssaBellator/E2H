# Statistical promotion gates and rollback metadata

E2H promotion artifacts turn paired evaluation evidence into a deterministic, content-addressed decision. They do not deploy a variant, call a model, or mutate a runtime. A deployment system may consume a passing promotion receipt only after independently verifying the expected policy, proposal, and variant identities.

## Evidence boundary

`VariantPredictionDocument` binds one variant's predictions to the public dataset and partition commitments. The evaluator retains the labelled dataset and compares baseline and candidate predictions on the same examples.

`compare_variant_predictions` emits only a paired contingency table:

- both variants correct
- baseline only correct
- candidate only correct
- neither correct

The report contains scores and public commitments, but no expected outputs, prediction values, example identifiers, private dataset digest, private partition digest, or per-example correctness.

Validation and sealed-test reports use the same aggregate shape. Every report in one proposal must share the same dataset and partition identities and public commitments. Sealed prediction attempts still need operational controls: pre-register submissions, bind each submission to a candidate digest, limit attempts, and prevent adaptive probing of aggregate scores.

## Statistical gate

Each `PromotionGateRule` applies independently to either `validation` or `sealed_test`. A rule can require:

- a minimum paired sample count
- a minimum candidate score
- a minimum absolute score improvement
- a minimum number of discordant pairs
- a maximum exact one-sided McNemar p-value

For discordant outcomes, E2H computes the exact binomial tail

`P(X >= candidate_only_correct)` where `X ~ Binomial(discordant_pairs, 0.5)`.

Score, improvement, and p-value thresholds are compared using exact rational values derived from the aggregate counts. A candidate must beat the baseline and satisfy every threshold in every declared rule. Missing evidence, undeclared evidence, digest mismatches, variant mismatches, incomplete predictions, extra predictions, and incorrect output signatures are rejected.

The committed one-example policy is only a reproducible fixture. Production policies should use meaningful sample sizes, predeclared thresholds, and an alpha appropriate to the decision risk. Repeated candidate testing and multiple comparisons require controls outside this schema, such as candidate budgets, correction procedures, or a fresh sealed set.

## Promotion receipt

`evaluate_promotion` produces a self-verifying `PromotionDecision` that embeds the exact policy and proposal, including aggregate evidence. Loading the decision recomputes its policy and proposal digests, every statistical check, and the final outcome. A rejected or internally inconsistent decision cannot be materialized.

`materialize_promotion` requires a `RollbackPlan` that:

- references the exact promotion decision digest
- names the promoted candidate as the active variant
- names the exact baseline variant as the rollback target
- contains at least one observable trigger

The resulting `PromotionReceipt` embeds the verified decision, records the policy and proposal digests, and embeds the rollback plan and its digest. Loading the receipt revalidates both chains. Any change to the decision, target, locator, thresholds, or triggers invalidates the materialized artifacts.

## Rollback records

A rollback trigger declares a metric, comparison operator, threshold, minimum sample count, and observation window. Supported operators are `lt`, `lte`, `gt`, and `gte`.

`record_rollback` creates an auditable `RollbackEvent` only when:

- the trigger exists in the embedded plan
- the minimum sample count is met
- the observation window matches the declared trigger window
- the finite observed value satisfies the declared comparison
- the receipt still validates against its embedded rollback digest

The event records the exact promoted and target variant digests, actor, trigger, value, sample count, observation window, and timezone-aware timestamp. When `--window-seconds` is omitted, the command records the trigger's declared window; supplying a different window is rejected. E2H records evidence; the surrounding deployment controller performs the actual traffic or configuration change.

## CLI

Create paired evaluator-side evidence:

```bash
e2h promotion compare \
  examples/optimizer/partition.yaml \
  examples/optimizer/dataset.yaml \
  examples/optimizer/promotion-baseline-validation.yaml \
  examples/optimizer/promotion-candidate-validation.yaml \
  --evidence-id optimizer-validation \
  --output validation-evidence.json
```

Evaluate a proposal and require a passing decision:

```bash
e2h promotion evaluate \
  examples/optimizer/promotion-policy.yaml \
  examples/optimizer/promotion-proposal.yaml \
  --require-promotion \
  --output promotion-decision.json
```

Materialize the promotion with its rollback plan:

```bash
e2h promotion materialize \
  promotion-decision.json \
  examples/optimizer/promotion-rollback.yaml \
  --output promotion-receipt.json
```

Record a declared rollback trigger:

```bash
e2h promotion rollback \
  promotion-receipt.json \
  --trigger pass-rate-regression \
  --value 0.90 \
  --samples 20 \
  --window-seconds 3600 \
  --event-id rollback-001 \
  --actor deployment-controller \
  --occurred-at 2026-08-07T00:00:00+00:00 \
  --output rollback-event.json
```

Use `e2h-promotion` for the focused entry point. `schema` emits JSON Schema for predictions, evidence, policies, proposals, decisions, rollback plans, receipts, and events. `digest` prints canonical artifact identities.
