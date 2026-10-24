# Active Paderborn bearing model

AXON active model trained from the supplied Paderborn Bearing DataCenter archive. It keeps the existing edge contract: 4,096 vibration samples at 64 kHz, 16 legacy engineered features, and binary classes `healthy` / `other_bearing_damage`.

## Evaluation

The report uses nested five-fold stratified group cross-validation. Each outer fold holds out complete physical bearings; inner grouped folds select hyperparameters. The final model is fit on all 32 labeled bearing identities. The primary selection metric gives each bearing equal weight within each class.

- Out-of-fold window accuracy: 91.86%
- Out-of-fold macro F1: 0.8678
- Out-of-fold window balanced accuracy: 87.28%
- Mean per-bearing, class-balanced accuracy: 87.28%
- Healthy recall: 79.98%; damage recall: 94.59%
- Fixed old-RF settings on the same folds: 86.72% mean per-bearing, class-balanced accuracy
- Selected configuration: 300-tree Random Forest, `min_samples_leaf=4`, 80% row subsampling, bearing-equal sample weights; selected in four of five outer folds

This is a modest improvement on the primary group-balanced metric, with a small reduction in ordinary window accuracy and damage recall versus the fixed baseline. K002 remains a failed healthy-bearing holdout and KI05 has low damaged-bearing recall. The full per-bearing results and matched comparison to the prior artifact are in `evaluation.json`. Treat probabilities as uncalibrated and retain technician confirmation. These are laboratory cross-validation results, not field accuracy.

## Dataset provenance

`dataset_manifest.json` records the labels taken from the supplied bearing fact sheets, per-recording checksums, selected recording IDs, exclusion counts, and dataset attribution. The raw 9.69 GB archive is not included. Preparation used four recordings per available bearing/operating-condition pair (512 recordings; one unsupported MAT file excluded), 4,096-sample windows with 2,048 overlap, and the named `vibration_1` channel. All windows from one physical bearing stayed together in the outer and inner CV folds.

The source dataset is CC-BY-NC-4.0. Retain Paderborn attribution and the noncommercial restriction.
