"""Shared validation, windowing, and engineered vibration features."""
from __future__ import annotations

import math
import numpy as np

FEATURE_VERSION = "axon-vibration-features-v2"
LEGACY_FEATURE_VERSION = "axon-vibration-features-v1"


def validate_signal(signal, sampling_frequency_hz: float, *, minimum_samples: int = 16) -> np.ndarray:
    values = np.asarray(signal, dtype=np.float64).reshape(-1)
    if not np.isfinite(sampling_frequency_hz) or sampling_frequency_hz <= 0:
        raise ValueError("sampling_frequency_hz must be a finite positive value")
    if values.size < minimum_samples:
        raise ValueError(f"signal must have at least {minimum_samples} samples")
    if not np.isfinite(values).all():
        raise ValueError("signal contains non-finite values")
    return values


def segment_signal(signal, sampling_frequency_hz: float, window_samples: int, overlap_samples: int) -> list[np.ndarray]:
    values = validate_signal(signal, sampling_frequency_hz, minimum_samples=window_samples)
    if not 0 <= overlap_samples < window_samples:
        raise ValueError("overlap_samples must be non-negative and smaller than window_samples")
    stride = window_samples - overlap_samples
    return [values[offset : offset + window_samples] for offset in range(0, len(values) - window_samples + 1, stride)]


def _spectral_entropy(power: np.ndarray) -> float:
    total = float(power.sum())
    if total <= 0:
        return 0.0
    probabilities = power / total
    probabilities = probabilities[probabilities > 0]
    return float(-(probabilities * np.log2(probabilities)).sum() / math.log2(max(len(power), 2)))


def feature_names() -> tuple[str, ...]:
    stable = (
        "log_rms", "crest_factor", "shape_factor", "impulse_factor", "clearance_factor",
        "skewness", "kurtosis", "envelope_kurtosis", "spectral_centroid_nyquist",
        "spectral_entropy", "spectral_flatness", "dominant_frequency_nyquist",
        "dominant_peak_ratio", "low_frequency_ratio", "high_frequency_ratio",
        "spectral_rolloff_85_nyquist",
    )
    spectral = tuple(f"spectral_log_energy_band_{index:02d}" for index in range(16))
    envelope = tuple(f"envelope_log_energy_band_{index:02d}" for index in range(16))
    return stable + spectral + envelope


def legacy_feature_names() -> tuple[str, ...]:
    return ("mean", "std", "rms", "variance", "peak_amplitude", "peak_to_peak", "crest_factor", "skewness", "kurtosis", "energy", "frequency_energy", "dominant_frequency_hz", "spectral_centroid_hz", "spectral_entropy", "low_band_energy", "high_band_energy")


def _log_band_energy(power: np.ndarray, frequencies: np.ndarray, low_hz: float, high_hz: float, bands: int = 16) -> list[float]:
    total = float(power.sum())
    edges = np.geomspace(low_hz, high_hz, bands + 1)
    values = []
    for lower, upper in zip(edges[:-1], edges[1:]):
        mask = (frequencies >= lower) & (frequencies < upper)
        ratio = float(power[mask].sum()) / total if total > 0 and mask.any() else 0.0
        values.append(float(np.log10(max(ratio, 1e-12))))
    return values


def _envelope(signal: np.ndarray) -> np.ndarray:
    """Magnitude of the analytic signal, using NumPy's FFT (no edge SciPy dependency)."""
    spectrum = np.fft.fft(signal)
    multiplier = np.zeros(len(signal), dtype=np.float64)
    multiplier[0] = 1.0
    if len(signal) % 2 == 0:
        multiplier[1 : len(signal) // 2] = 2.0
        multiplier[len(signal) // 2] = 1.0
    else:
        multiplier[1 : (len(signal) + 1) // 2] = 2.0
    return np.abs(np.fft.ifft(spectrum * multiplier))


def engineered_features(window, sampling_frequency_hz: float) -> dict[str, float]:
    values = validate_signal(window, sampling_frequency_hz)
    centered = values - values.mean()
    rms = float(np.sqrt(np.mean(values ** 2)))
    std = float(values.std())
    peak = float(np.max(np.abs(values)))
    mean_abs = float(np.mean(np.abs(centered)))
    mean_sqrt_abs_sq = float(np.mean(np.sqrt(np.abs(centered))) ** 2)
    if std == 0:
        skewness = kurtosis = 0.0
    else:
        normalized = centered / std
        skewness = float(np.mean(normalized ** 3))
        kurtosis = float(np.mean(normalized ** 4) - 3.0)
    envelope = _envelope(centered)
    envelope_centered = envelope - envelope.mean()
    envelope_std = float(envelope.std())
    envelope_kurtosis = float(np.mean((envelope_centered / envelope_std) ** 4) - 3.0) if envelope_std else 0.0
    spectrum = np.fft.rfft(centered, n=len(centered) * 2)
    power = np.abs(spectrum) ** 2
    frequencies = np.fft.rfftfreq(len(centered) * 2, d=1.0 / sampling_frequency_hz)
    non_dc = power.copy()
    if non_dc.size:
        non_dc[0] = 0
    dominant = float(frequencies[int(np.argmax(non_dc))]) if non_dc.any() else 0.0
    total_power = float(power.sum())
    nyquist = sampling_frequency_hz / 2.0
    centroid = float(np.dot(frequencies, power) / total_power) / nyquist if total_power else 0.0
    probabilities = power / total_power if total_power else np.zeros_like(power)
    positive = probabilities[probabilities > 0]
    entropy = float(-(positive * np.log2(positive)).sum() / math.log2(max(len(power), 2))) if len(positive) else 0.0
    flatness = float(np.exp(np.mean(np.log(np.maximum(power[1:], 1e-30)))) / np.mean(power[1:])) if len(power) > 1 and np.mean(power[1:]) else 0.0
    cumulative = np.cumsum(power)
    rolloff = float(frequencies[min(int(np.searchsorted(cumulative, cumulative[-1] * 0.85)), len(frequencies) - 1)] / nyquist) if cumulative.size and cumulative[-1] else 0.0
    envelope_spectrum = np.fft.rfft(envelope_centered, n=len(envelope_centered) * 2)
    envelope_power = np.abs(envelope_spectrum) ** 2
    envelope_frequencies = np.fft.rfftfreq(len(envelope_centered) * 2, d=1.0 / sampling_frequency_hz)
    low_ratio = float(power[(frequencies >= 0) & (frequencies < 1000)].sum() / total_power) if total_power else 0.0
    high_ratio = float(power[frequencies >= 8000].sum() / total_power) if total_power else 0.0
    result = {
        # Display-only diagnostic values retained for the existing dashboard.
        "rms": rms,
        "peak_amplitude": peak,
        "dominant_frequency_hz": dominant,
        "log_rms": float(np.log1p(rms)),
        "crest_factor": peak / rms if rms else 0.0,
        "shape_factor": rms / mean_abs if mean_abs else 0.0,
        "impulse_factor": peak / mean_abs if mean_abs else 0.0,
        "clearance_factor": peak / mean_sqrt_abs_sq if mean_sqrt_abs_sq else 0.0,
        "skewness": skewness,
        "kurtosis": kurtosis,
        "envelope_kurtosis": envelope_kurtosis,
        "spectral_centroid_nyquist": centroid,
        "spectral_entropy": entropy,
        "spectral_flatness": flatness,
        "dominant_frequency_nyquist": dominant / nyquist,
        "dominant_peak_ratio": float(non_dc.max() / total_power) if total_power else 0.0,
        "low_frequency_ratio": low_ratio,
        "high_frequency_ratio": high_ratio,
        "spectral_rolloff_85_nyquist": rolloff,
    }
    result.update(dict(zip(feature_names()[16:32], _log_band_energy(power, frequencies, 30.0, nyquist + 1.0))))
    result.update(dict(zip(feature_names()[32:], _log_band_energy(envelope_power, envelope_frequencies, 10.0, sampling_frequency_hz / 4.0))))
    if not all(np.isfinite(value) for value in result.values()):
        raise ValueError("feature extraction produced non-finite values")
    return result


def feature_vector(window, sampling_frequency_hz: float) -> np.ndarray:
    features = engineered_features(window, sampling_frequency_hz)
    return np.asarray([features[name] for name in feature_names()], dtype=np.float64)


def legacy_feature_vector(window, sampling_frequency_hz: float) -> np.ndarray:
    """Feature order used by the original packaged model, retained for rollback."""
    values = validate_signal(window, sampling_frequency_hz)
    centered = values - values.mean()
    rms = float(np.sqrt(np.mean(values ** 2)))
    std = float(values.std())
    peak = float(np.max(np.abs(values)))
    if std:
        normalized = centered / std
        skewness = float(np.mean(normalized ** 3))
        kurtosis = float(np.mean(normalized ** 4) - 3.0)
    else:
        skewness = kurtosis = 0.0
    power = np.abs(np.fft.rfft(centered)) ** 2
    frequencies = np.fft.rfftfreq(len(values), d=1.0 / sampling_frequency_hz)
    non_dc = power.copy()
    if len(non_dc):
        non_dc[0] = 0
    dominant = float(frequencies[int(np.argmax(non_dc))]) if non_dc.any() else 0.0
    total = float(power.sum())
    centroid = float(np.dot(frequencies, power) / total) if total else 0.0
    split = max(1, len(power) // 4)
    return np.asarray([
        float(values.mean()), std, rms, float(values.var()), peak,
        float(values.max() - values.min()), peak / rms if rms else 0.0,
        skewness, kurtosis, float(np.sum(values ** 2)), total, dominant,
        centroid, _spectral_entropy(power), float(power[:split].sum()),
        float(power[split:].sum()),
    ], dtype=np.float64)
