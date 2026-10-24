# AXON bearing model training audit

## Scope and verified inputs

The audit inspected the Paderborn reader and preparation pipeline, signal feature contract, model training and bundle loading, the edge inference path, replay API, previous artifact metadata/evaluation, and the supplied archive's bearing fact sheets. The original prepared arrays and source recordings used to create the prior artifact were not present, so its exact source data and old reported metrics cannot be independently reproduced from repository contents.

The prior active bundle metadata did confirm the documented split: training identities K001, KA04, KA15, KA16, KI16; validation K002 and KI14; test K003 and KI04. It also confirmed 7,481 training, 2,986 validation, and 2,981 test windows. The stored test report records 95.3036% accuracy, macro F1 0.9529, damaged-class recall 90.59%, and 140 missed damaged windows. These are claims from that artifact's saved report, not independently verified ground truth. The split did keep identities disjoint, but had only one healthy physical bearing in each partition. Its overlapping windows therefore do not represent thousands of independent test examples.

The supplied Paderborn archive contains profile sheets for 32 physical bearings: six healthy identities (K001–K006) and 26 bearings with documented damage. Labels are sourced from the bearing profile sheets, not inferred from vibration. The binary label intentionally collapses all documented damage into `other_bearing_damage`; it does not predict defect type or location. The full mapping and profile evidence are in `config/paderborn.labels.json`.

## Preparation

The archive is read as a ZIP without full extraction. Preparation selected up to four valid MAT recordings per bearing and operating-condition pair, for 512 recordings and 63,765 windows (4,096 samples, 2,048 overlap, `vibration_1`). One unsupported MAT recording was excluded after a parser error; files beyond the per-pair cap were not parsed. The generated local manifest records source-member checksums, selection and exclusion counts, and label provenance. The archive itself and prepared arrays are not checked in, and no synthetic samples were generated. Paderborn data carries CC-BY-NC-4.0 attribution and noncommercial restrictions.

## Split redesign and training

Training uses nested stratified grouped cross-validation. All windows from a physical bearing remain in a single outer fold; inner grouped folds select among the fixed RF baseline, class-balanced RF variants with larger leaves, and healthy-class weighting. Selection uses mean per-bearing, class-balanced accuracy so each bearing contributes equally within its class. The chosen final model is then fit on all 32 identities. The edge contract remains the existing 16 legacy features, 4,096 samples at 64 kHz, and the two documented classes. A 48-feature experimental contract was evaluated and rejected because its grouped CV performance was worse.

The final active artifact is `axon-bearing-rf-40b0a17c59ef` (300-tree RF, `min_samples_leaf=4`, 80% row subsampling, equalized physical-bearing weights). Nested outer-fold results:

- Window accuracy: 91.856%; macro F1: 0.8678; window balanced accuracy: 87.283%.
- Primary mean per-bearing, class-balanced accuracy: 87.279%; fixed old RF settings on the same folds: 86.723% (+0.56 percentage points for the selected model).
- Healthy recall: 79.98%; damaged recall: 94.59%.
- At the existing 0.70 decision threshold, 94.84% of windows receive a decisive result; damage recall among decisive predictions is 91.40%.
- Training took about 27 minutes on this host, within the requested three-hour limit.

This is a modest improvement on the selected primary metric, not a blanket improvement: ordinary accuracy and damaged recall are slightly below the same-fold fixed baseline. Per-bearing predictions and the same-fold baseline are in `ml/artifacts/bearing/active/evaluation.json`.

## Failure analysis and limitations

The hard identity failures matter more than the aggregate score. K002, a held-out healthy identity, had zero window accuracy; KI05 had only 11.3% accuracy in its held-out fold. The prior artifact's four validation/test identities (K002, K003, KI04, KI14) also performed worse under the retrained model's matched out-of-fold predictions (74.97% accuracy and balanced accuracy versus 78.56% for the prior stored predictions), driven by K002. This matched comparison is limited to those identities and their available windows, and is not an independent new test.

The final bundle is fitted on every available bearing identity after cross-validation. No physical-bearing identities remain untouched for a final test, and there are only six independent healthy identities in the archive. Results are laboratory cross-validation, not field accuracy. Probabilities are uncalibrated; the existing confidence ceiling is a UI rule, not calibration. A future release should reserve new bearing identities, especially healthy bearings, before final training and evaluate on those identities once. Technician confirmation remains appropriate.

## Runtime and reproduction

The active bundle checksum, scikit-learn version, feature order, and 4,096-sample/64 kHz contract are checked by the bundle loader. The archive-backed API replay reads the selected ZIP member, verifies its source checksum, and then uses the same active feature/model path as edge inference. The final audit also ran the focused bearing tests and replay integration against a prepared archive.

Recreate the local preparation by copying `config/paderborn.archive.example.json` to `ml/data/paderborn/local.json`, setting the archive path and output directory, then run:

```powershell
python -m ml.data.prepare --config ml/data/paderborn/local.json
python -m ml.training.train_random_forest --prepared ml/data/paderborn/prepared-r4/windows.npz --output ml/artifacts/bearing/candidate --feature-version axon-vibration-features-v1
```

Do not overwrite the active bundle with a new run until its complete grouped evaluation and identity-level failures have been reviewed.
