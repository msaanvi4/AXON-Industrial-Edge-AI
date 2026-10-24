# Paderborn demonstration excerpts

These are eight unmodified vibration windows (4,096 samples each at 64 kHz)
extracted from four locally acquired Paderborn Bearing DataCenter recordings.
Each file contains windows 0 and 1 with 2,048-sample overlap. `manifest.json`
records original identities, documented label evidence, source, and checksums.

Attribution: Paderborn University, Bearing DataCenter, Chair of Design and Drive
Technology (KAt), https://mb.uni-paderborn.de/en/kat/research/bearing-datacenter
Source licence: Creative Commons Attribution-NonCommercial 4.0 (CC-BY-NC-4.0),
https://creativecommons.org/licenses/by-nc/4.0/
Changes: extraction of these windows and lossless NPZ packaging only.
Retain this attribution and licence when sharing. This data is for noncommercial
demonstration and is not covered by a different software licence in the repo.

The active retrained model was fit on all 32 available bearing identities after
nested grouped cross-validation, so none of these examples is an untouched final
test identity. This tiny subset is for integration checks only. Historical
K003/KI04 results belong to the previous model and must not be presented as
holdout results for the active bundle.
The full local dataset takes priority when its prepared manifest exists.
