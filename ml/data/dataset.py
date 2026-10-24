"""Input validation and chronological partitioning for Axon datasets."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .synthetic import FEATURES


def load_csv(path, column_mapping=None, features=FEATURES):
    data = pd.read_csv(Path(path))
    if column_mapping:
        data = data.rename(columns=column_mapping)
    validate_dataset(data, features)
    data["ts"] = pd.to_datetime(data["ts"], utc=True, errors="raise")
    return data.sort_values("ts").reset_index(drop=True)


def validate_dataset(data, features=FEATURES):
    required = {"ts", "machine_id", *features}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"dataset is missing required columns: {sorted(missing)}")
    if data.empty:
        raise ValueError("dataset is empty")
    for feature in features:
        values = pd.to_numeric(data[feature], errors="coerce")
        if values.isna().any():
            raise ValueError(f"{feature} contains missing or non-numeric values")
        if not values.map(lambda value: abs(value) < 1_000_000).all():
            raise ValueError(f"{feature} contains implausibly large values")


def chronological_split(data, train_fraction=0.6, validation_fraction=0.2):
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("split fractions must be between zero and one")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train and validation fractions must leave a test partition")
    train_end = int(len(data) * train_fraction)
    validation_end = int(len(data) * (train_fraction + validation_fraction))
    return data.iloc[:train_end].copy(), data.iloc[train_end:validation_end].copy(), data.iloc[validation_end:].copy()
