# Train, validation, and sealed-test partitions

E2H partition manifests bind three complete, disjoint optimizer dataset splits to one exact canonical dataset SHA-256. They also carry a separate public dataset commitment derived only from stable example identifiers, inputs, input fields, and output field names. The public commitment excludes output values and all metadata so it can be included in optimizer-facing artifacts without turning low-entropy sealed labels into an offline hash-guessing target.

The manifest identity changes whenever the private dataset digest, public dataset commitment, split membership, or manifest metadata changes. A separate public partition commitment binds the public dataset commitment and split membership while excluding the private digest and metadata.

## Safety properties

- Every dataset example must be assigned exactly once.
- Train, validation, and sealed-test identifiers must be non-empty and disjoint.
- Unknown, duplicated, or unassigned identifiers are rejected.
- Partition verification requires a labelled dataset with one consistent output signature.
- The evaluator verifies both the complete private dataset digest and the label-free public dataset commitment.
- Train and validation exports include inputs and outputs.
- Sealed-test exports include only sorted input values and input-field markers. Dataset and example metadata are omitted with the labels.
- Optimizer-facing exports, predictions, and reports contain only public dataset and partition commitments; they never contain the complete labelled-dataset digest.
- Sealed predictions must cover exactly the sealed-test identifiers.
- Evaluation returns only aggregate total, correct count, and score. Expected outputs and per-example correctness are never returned.

The partition layer does not train an optimizer, execute a model, fetch remote data, or deserialize executable programs.

## CLI

```bash
e2h partition validate \
  examples/optimizer/partition.yaml \
  examples/optimizer/dataset.yaml

e2h partition export \
  examples/optimizer/partition.yaml \
  examples/optimizer/dataset.yaml \
  --partition train \
  --output .e2h/train.json

e2h partition export \
  examples/optimizer/partition.yaml \
  examples/optimizer/dataset.yaml \
  --partition sealed_test \
  --output .e2h/sealed-inputs.json

e2h partition evaluate \
  examples/optimizer/partition.yaml \
  examples/optimizer/dataset.yaml \
  examples/optimizer/sealed-predictions.yaml \
  --output .e2h/sealed-report.json
```

The focused `e2h-partition` entry point exposes the same commands. `digest` prints the private canonical manifest identity and is intended for evaluator-side use. `schema` emits schemas for manifests, predictions, exports, verification documents, and aggregate evaluation reports.

## Operational boundary

The labelled source dataset and partition manifest are evaluator-side artifacts. Optimizers should receive only the exported train and validation records plus the label-free sealed-test export. A sealed prediction document may then be evaluated by a process that has access to the labelled source without disclosing labels or label-dependent digests in the result.

Aggregate scoring is not a privacy mechanism against repeated adaptive submissions. An evaluator that permits many carefully varied prediction documents can become a correctness oracle even when each response is aggregate-only. Production evaluators should bind submissions to a candidate, limit or pre-register evaluation attempts, and avoid exposing reports to an optimizer that can adaptively probe the sealed set.
