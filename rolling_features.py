#!/usr/bin/env python3
"""
rolling_features.py

Leak-free rolling feature utilities for team-game logs.

Design goals:
- Works on long, append-forever team-game tables.
- Leak-free by default: all rolling outputs are computed from games strictly BEFORE the current row (shift=1).
- Supports:
  - Unweighted: mean, std, min, max, range, IQR, percentiles (floors/ceilings)
  - Weighted: mean, std, percentiles (approx via replication-free method), plus a weighted "effective N"
  - Trend: slope over last N (unweighted + weighted)
  - Regime shift: mean(last_k) - mean(prev_k) inside last N (unweighted + weighted)

Notes:
- Weighted percentiles are computed using a stable sort + cumulative weights method.
- All functions assume you have a per-row timestamp/order column and group keys (e.g., team_id).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, Dict

import numpy as np
import pandas as pd

EPS = 1e-12


def _to_datetime_utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce")


def _ensure_cols(df: pd.DataFrame, cols: Sequence[str], ctx: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{ctx}: missing columns: {missing}")


def _to_num(df: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _safe_div(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.divide(a, b, out=np.full_like(a, np.nan, dtype=float), where=(b != 0))


@dataclass(frozen=True)
class RollingConfig:
    group_cols: Tuple[str, ...] = ("team_id",)
    order_col: str = "game_datetime_utc"
    shift: int = 1
    window: int = 10
    min_periods_mean: int = 1
    min_periods_std: int = 2
    ddof: int = 0  # ddof=0 is population std; ddof=1 sample std
    prefix: str = "l10_"


# ----------------------------
# Core unweighted rollups
# ----------------------------

def add_unweighted_rollups(
    df: pd.DataFrame,
    metrics: Sequence[str],
    cfg: RollingConfig,
    include_minmax: bool = True,
    include_iqr: bool = True,
    include_percentiles: Optional[Sequence[float]] = (0.20, 0.80),
    include_slope: bool = True,
    include_shift: bool = True,
    shift_k: int = 5,  # last_k vs prev_k inside window
) -> pd.DataFrame:
    """
    Adds leak-free (shifted) rolling statistics for each metric:
      - mean_pre, std_pre
      - min_pre, max_pre, range_pre
      - iqr_pre (p75-p25)
      - percentiles: pXX_pre (e.g., p20_pre, p80_pre)
      - slope_pre: linear trend over last N games (t=1..N)
      - shift_pre: mean(last_k) - mean(prev_k) within last N games

    Column naming (for metric m):
      {prefix}{m}_pre
      {prefix}{m}_std_pre
      {prefix}{m}_min_pre
      {prefix}{m}_max_pre
      {prefix}{m}_range_pre
      {prefix}{m}_iqr_pre
      {prefix}{m}_p20_pre / p80_pre etc
      {prefix}{m}_slope_pre
      {prefix}{m}_shift_pre
    """
    out = df.copy()

    # ensure order col usable
    if cfg.order_col in out.columns:
        out["_ord"] = _to_datetime_utc(out[cfg.order_col])
    else:
        out["_ord"] = np.arange(len(out), dtype=float)

    # ensure metric columns exist
    for m in metrics:
        if m not in out.columns:
            out[m] = np.nan
    out = _to_num(out, metrics)

    out = out.sort_values(list(cfg.group_cols) + ["_ord"])
    g = out.groupby(list(cfg.group_cols), sort=False)

    # base mean/std
    for m in metrics:
        out[f"{cfg.prefix}{m}_pre"] = (
            g[m]
            .apply(lambda x: x.shift(cfg.shift).rolling(cfg.window, min_periods=cfg.min_periods_mean).mean())
            .reset_index(level=list(cfg.group_cols), drop=True)
        )
        out[f"{cfg.prefix}{m}_std_pre"] = (
            g[m]
            .apply(lambda x: x.shift(cfg.shift).rolling(cfg.window, min_periods=cfg.min_periods_std).std(ddof=cfg.ddof))
            .reset_index(level=list(cfg.group_cols), drop=True)
        )

        if include_minmax:
            out[f"{cfg.prefix}{m}_min_pre"] = (
                g[m]
                .apply(lambda x: x.shift(cfg.shift).rolling(cfg.window, min_periods=cfg.min_periods_mean).min())
                .reset_index(level=list(cfg.group_cols), drop=True)
            )
            out[f"{cfg.prefix}{m}_max_pre"] = (
                g[m]
                .apply(lambda x: x.shift(cfg.shift).rolling(cfg.window, min_periods=cfg.min_periods_mean).max())
                .reset_index(level=list(cfg.group_cols), drop=True)
            )
            out[f"{cfg.prefix}{m}_range_pre"] = out[f"{cfg.prefix}{m}_max_pre"] - out[f"{cfg.prefix}{m}_min_pre"]

        if include_iqr:
            def _iqr(x: pd.Series) -> float:
                arr = x.dropna().to_numpy(dtype=float)
                if arr.size == 0:
                    return np.nan
                return float(np.nanpercentile(arr, 75) - np.nanpercentile(arr, 25))

            out[f"{cfg.prefix}{m}_iqr_pre"] = (
                g[m]
                .apply(
                    lambda x: x.shift(cfg.shift)
                    .rolling(cfg.window, min_periods=cfg.min_periods_mean)
                    .apply(_iqr, raw=False)
                )
                .reset_index(level=list(cfg.group_cols), drop=True)
            )

        if include_percentiles:
            for q in include_percentiles:
                qn = int(round(q * 100))
                def _pct(x: pd.Series) -> float:
                    arr = x.dropna().to_numpy(dtype=float)
                    if arr.size == 0:
                        return np.nan
                    return float(np.nanpercentile(arr, q * 100))
                out[f"{cfg.prefix}{m}_p{qn}_pre"] = (
                    g[m]
                    .apply(
                        lambda x: x.shift(cfg.shift)
                        .rolling(cfg.window, min_periods=cfg.min_periods_mean)
                        .apply(_pct, raw=False)
                    )
                    .reset_index(level=list(cfg.group_cols), drop=True)
                )

        if include_slope:
            def _slope(x: pd.Series) -> float:
                arr = x.dropna().to_numpy(dtype=float)
                n = arr.size
                if n < 2:
                    return np.nan
                t = np.arange(1, n + 1, dtype=float)
                t = t - t.mean()
                y = arr - np.nanmean(arr)
                denom = np.sum(t ** 2)
                if denom < EPS:
                    return np.nan
                return float(np.sum(t * y) / denom)

            out[f"{cfg.prefix}{m}_slope_pre"] = (
                g[m]
                .apply(
                    lambda x: x.shift(cfg.shift)
                    .rolling(cfg.window, min_periods=cfg.min_periods_std)
                    .apply(_slope, raw=False)
                )
                .reset_index(level=list(cfg.group_cols), drop=True)
            )

        if include_shift:
            k = int(shift_k)
            if k * 2 > cfg.window:
                # still compute, but last_k/prev_k will overlap or be smaller; caller should tune
                pass

            def _shift(x: pd.Series) -> float:
                arr = x.dropna().to_numpy(dtype=float)
                n = arr.size
                if n < 2:
                    return np.nan
                # take the last 2k elements if available, else use split at mid
                if n >= 2 * k:
                    prev = arr[-2 * k:-k]
                    last = arr[-k:]
                else:
                    mid = n // 2
                    prev = arr[:mid]
                    last = arr[mid:]
                if prev.size == 0 or last.size == 0:
                    return np.nan
                return float(np.nanmean(last) - np.nanmean(prev))

            out[f"{cfg.prefix}{m}_shift_pre"] = (
                g[m]
                .apply(
                    lambda x: x.shift(cfg.shift)
                    .rolling(cfg.window, min_periods=cfg.min_periods_std)
                    .apply(_shift, raw=False)
                )
                .reset_index(level=list(cfg.group_cols), drop=True)
            )

    out = out.drop(columns=["_ord"], errors="ignore")
    return out


# ----------------------------
# Weighted rollups
# ----------------------------

def _weighted_mean_std(values: np.ndarray, weights: np.ndarray) -> Tuple[float, float, float]:
    """
    Returns (wmean, wstd, neff).
    neff = (sum w)^2 / sum(w^2) (effective sample size)
    """
    m = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    v = values[m].astype(float)
    w = weights[m].astype(float)
    if v.size == 0:
        return (np.nan, np.nan, 0.0)
    sw = w.sum()
    if sw <= 0:
        return (np.nan, np.nan, 0.0)
    mu = float(np.sum(w * v) / sw)
    var = float(np.sum(w * (v - mu) ** 2) / sw)
    std = float(np.sqrt(var))
    neff = float((sw ** 2) / (np.sum(w ** 2) + EPS))
    return (mu, std, neff)


def _weighted_percentile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """
    Weighted percentile using cumulative weights over sorted values.
    q in [0,1].
    """
    m = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    v = values[m].astype(float)
    w = weights[m].astype(float)
    if v.size == 0:
        return np.nan
    order = np.argsort(v)
    v = v[order]
    w = w[order]
    cw = np.cumsum(w)
    cutoff = q * cw[-1]
    idx = np.searchsorted(cw, cutoff, side="left")
    idx = min(max(int(idx), 0), v.size - 1)
    return float(v[idx])


def add_weighted_rollups(
    df: pd.DataFrame,
    metrics: Sequence[str],
    weight_col: str,
    cfg: RollingConfig,
    include_percentiles: Optional[Sequence[float]] = (0.20, 0.80),
    include_slope: bool = True,
    include_shift: bool = True,
    shift_k: int = 5,
) -> pd.DataFrame:
    """
    Adds leak-free (shifted) *weighted* rolling statistics for each metric:
      - wmean_pre, wstd_pre, neff_pre
      - weighted percentiles pXX_pre (optional)
      - weighted slope_pre (optional)
      - weighted shift_pre (optional)

    Requires a per-row weight column (already computed per game).
    Weight is also shifted by cfg.shift so the current game's weight isn't used.
    """
    out = df.copy()

    if cfg.order_col in out.columns:
        out["_ord"] = _to_datetime_utc(out[cfg.order_col])
    else:
        out["_ord"] = np.arange(len(out), dtype=float)

    for m in metrics:
        if m not in out.columns:
            out[m] = np.nan
    if weight_col not in out.columns:
        out[weight_col] = np.nan

    out = _to_num(out, list(metrics) + [weight_col])
    out = out.sort_values(list(cfg.group_cols) + ["_ord"])
    g = out.groupby(list(cfg.group_cols), sort=False)

    def _roll_apply_weighted(x: pd.Series, w: pd.Series, func):
        xs = x.shift(cfg.shift)
        ws = w.shift(cfg.shift)
        # rolling apply over aligned arrays
        def _f(window_vals: pd.Series) -> float:
            # window_vals is a slice of xs; grab matching slice of ws by index
            ww = ws.loc[window_vals.index]
            return func(window_vals.to_numpy(dtype=float), ww.to_numpy(dtype=float))
        return xs.rolling(cfg.window, min_periods=cfg.min_periods_mean).apply(_f, raw=False)

    def _roll_apply_weighted_std(x: pd.Series, w: pd.Series, which: str):
        xs = x.shift(cfg.shift)
        ws = w.shift(cfg.shift)

        def _f(window_vals: pd.Series) -> float:
            ww = ws.loc[window_vals.index]
            mu, std, neff = _weighted_mean_std(window_vals.to_numpy(dtype=float), ww.to_numpy(dtype=float))
            if which == "mean":
                return mu
            if which == "std":
                return std
            if which == "neff":
                return neff
            return np.nan
        return xs.rolling(cfg.window, min_periods=cfg.min_periods_mean).apply(_f, raw=False)

    for m in metrics:
        s = g[m]
        w = g[weight_col]

        out[f"{cfg.prefix}{m}_wmean_pre"] = (
            pd.concat([_roll_apply_weighted_std(s.get_group(k), w.get_group(k), "mean") for k in s.groups], axis=0)
            .reindex(out.index)
        )

        out[f"{cfg.prefix}{m}_wstd_pre"] = (
            pd.concat([_roll_apply_weighted_std(s.get_group(k), w.get_group(k), "std") for k in s.groups], axis=0)
            .reindex(out.index)
        )

        out[f"{cfg.prefix}{m}_neff_pre"] = (
            pd.concat([_roll_apply_weighted_std(s.get_group(k), w.get_group(k), "neff") for k in s.groups], axis=0)
            .reindex(out.index)
        )

        if include_percentiles:
            for q in include_percentiles:
                qn = int(round(q * 100))

                def _qfunc(v, ww, q=q):
                    return _weighted_percentile(v, ww, q)

                out[f"{cfg.prefix}{m}_wp{qn}_pre"] = (
                    pd.concat([
                        _roll_apply_weighted(s.get_group(k), w.get_group(k), lambda v, ww, q=q: _weighted_percentile(v, ww, q))
                        for k in s.groups
                    ], axis=0).reindex(out.index)
                )

        if include_slope:
            # weighted slope via weighted least squares on t=1..n within window
            def _wslope(v: np.ndarray, ww: np.ndarray) -> float:
                msk = np.isfinite(v) & np.isfinite(ww) & (ww > 0)
                y = v[msk].astype(float)
                wt = ww[msk].astype(float)
                n = y.size
                if n < 2:
                    return np.nan
                t = np.arange(1, n + 1, dtype=float)
                # WLS slope: cov_w(t,y)/var_w(t)
                sw = wt.sum()
                tbar = np.sum(wt * t) / sw
                ybar = np.sum(wt * y) / sw
                cov = np.sum(wt * (t - tbar) * (y - ybar))
                var = np.sum(wt * (t - tbar) ** 2)
                if var < EPS:
                    return np.nan
                return float(cov / var)

            out[f"{cfg.prefix}{m}_wslope_pre"] = (
                pd.concat([
                    _roll_apply_weighted(s.get_group(k), w.get_group(k), _wslope)
                    for k in s.groups
                ], axis=0).reindex(out.index)
            )

        if include_shift:
            k = int(shift_k)

            def _wshift(v: np.ndarray, ww: np.ndarray) -> float:
                msk = np.isfinite(v) & np.isfinite(ww) & (ww > 0)
                y = v[msk].astype(float)
                wt = ww[msk].astype(float)
                n = y.size
                if n < 2:
                    return np.nan
                if n >= 2 * k:
                    prev_y, prev_w = y[-2 * k:-k], wt[-2 * k:-k]
                    last_y, last_w = y[-k:], wt[-k:]
                else:
                    mid = n // 2
                    prev_y, prev_w = y[:mid], wt[:mid]
                    last_y, last_w = y[mid:], wt[mid:]
                if prev_y.size == 0 or last_y.size == 0:
                    return np.nan
                prev_mu, _, _ = _weighted_mean_std(prev_y, prev_w)
                last_mu, _, _ = _weighted_mean_std(last_y, last_w)
                return float(last_mu - prev_mu)

            out[f"{cfg.prefix}{m}_wshift_pre"] = (
                pd.concat([
                    _roll_apply_weighted(s.get_group(k), w.get_group(k), _wshift)
                    for k in s.groups
                ], axis=0).reindex(out.index)
            )

    out = out.drop(columns=["_ord"], errors="ignore")
    return out


# ----------------------------
# Convenience: floors/ceilings (percentiles) from existing columns
# ----------------------------

def add_floor_ceiling(
    df: pd.DataFrame,
    metric: str,
    cfg: RollingConfig,
    floor_q: float = 0.20,
    ceiling_q: float = 0.80,
) -> pd.DataFrame:
    """
    Adds:
      {prefix}{metric}_floor_pre = p{floor_q}
      {prefix}{metric}_ceiling_pre = p{ceiling_q}
    Uses unweighted percentiles on last-N.
    """
    out = df.copy()
    out = add_unweighted_rollups(
        out,
        metrics=[metric],
        cfg=cfg,
        include_minmax=False,
        include_iqr=False,
        include_percentiles=(floor_q, ceiling_q),
        include_slope=False,
        include_shift=False,
    )
    fq = int(round(floor_q * 100))
    cq = int(round(ceiling_q * 100))
    out[f"{cfg.prefix}{metric}_floor_pre"] = out[f"{cfg.prefix}{metric}_p{fq}_pre"]
    out[f"{cfg.prefix}{metric}_ceiling_pre"] = out[f"{cfg.prefix}{metric}_p{cq}_pre"]
    return out


if __name__ == "__main__":
    # Minimal smoke test
    d = pd.DataFrame({
        "team_id": ["A"] * 12,
        "game_datetime_utc": pd.date_range("2025-11-01", periods=12, freq="D"),
        "netrtg": np.linspace(-5, 6, 12),
        "efg": np.random.RandomState(0).rand(12),
        "w": np.linspace(0.2, 1.0, 12),
    })
    cfg = RollingConfig(group_cols=("team_id",), order_col="game_datetime_utc", window=10, prefix="l10_")
    d2 = add_unweighted_rollups(d, metrics=["netrtg", "efg"], cfg=cfg)
    # Weighted example
    # d3 = add_weighted_rollups(d2, metrics=["netrtg"], weight_col="w", cfg=cfg)
    print(d2.tail(3).to_string(index=False))
