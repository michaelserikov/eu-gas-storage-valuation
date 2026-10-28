"""Weather-driven storage withdrawal in Germany and fill-level scenarios for the coming winter (v0.2).

Daily net withdrawal from German storage (GWh/d, AGSI+) is regressed on heating degree days of the
same and the previous day, a weekend dummy and month effects, over the heating seasons (15 October
to 31 March) since the end of Russian pipeline supply (winters 2022/23 to 2025/26). Withdrawal is
whatever demand imports and production do not cover, so the intercepts absorb the supply situation
of that regime; the slope on degree days is the weather sensitivity.

Scenarios replay the temperatures of every winter 1991/92 to 2025/26, shifted to today's climate
by the linear warming trend of winter temperatures, through the fitted model, add the residuals of
one of the fitted winters (which carries realistic persistence of non-weather shocks) and cap
withdrawal by the deliverability observed at that fill level.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SEASON_START, SEASON_END = (10, 15), (3, 31)
REGIME_START = 2022          # first winter (season starting in this year) of the post-2022 regime


def season_of(idx: pd.DatetimeIndex) -> np.ndarray:
    return np.where(idx.month >= 7, idx.year, idx.year - 1)


def in_season(idx: pd.DatetimeIndex) -> np.ndarray:
    m, d = idx.month, idx.day
    return ((m == 10) & (d >= SEASON_START[1])) | (m >= 11) | (m <= 3)


def design(hdd: pd.Series) -> pd.DataFrame:
    x = pd.DataFrame({"hdd": hdd, "hdd_lag": hdd.shift(1)}, index=hdd.index)
    x["weekend"] = (hdd.index.dayofweek >= 5).astype(float)
    for m in (10, 11, 12, 1, 2, 3):
        x[f"m{m:02d}"] = (hdd.index.month == m).astype(float)
    return x


def fit(nw: pd.Series, hdd: pd.Series, seasons: list[int]):
    """OLS on the given seasons. Returns (coefficients, residuals by date)."""
    x = design(hdd)
    sel = in_season(x.index) & np.isin(season_of(x.index), seasons)
    df = x[sel].join(nw.rename("nw"), how="inner").dropna()
    beta, *_ = np.linalg.lstsq(df.drop(columns="nw").to_numpy(), df["nw"].to_numpy(), rcond=None)
    beta = pd.Series(beta, index=x.columns)
    resid = df["nw"] - df.drop(columns="nw") @ beta
    return beta, resid


def predict(beta: pd.Series, hdd: pd.Series) -> pd.Series:
    x = design(hdd).fillna({"hdd_lag": hdd.iloc[0]})
    return x @ beta


def deliverability(storage: pd.DataFrame, bins: int = 20, q: float = 0.99) -> pd.Series:
    """Maximum observed withdrawal (GWh/d) by fill level (percent bins), made non-decreasing."""
    df = storage[["full", "withdrawal"]].dropna()
    edges = np.linspace(0, 100, bins + 1)
    cap = df.groupby(pd.cut(df["full"], edges))["withdrawal"].quantile(q)
    cap.index = edges[1:]
    # below the lowest observed fill, fall linearly to zero at an empty storage
    first = cap.first_valid_index()
    low = cap.index <= first
    cap[low] = cap[first] * cap.index[low] / first
    return cap.cummax()


def season_temperatures(t: pd.Series, start_md: tuple[int, int], end_md: tuple[int, int]
                        ) -> dict[int, np.ndarray]:
    """Daily temperatures of each historical season from start_md to end_md (29 Feb dropped)."""
    out = {}
    t = t[~((t.index.month == 2) & (t.index.day == 29))]
    for y in range(t.index[0].year, t.index[-1].year):
        a = pd.Timestamp(year=y, month=start_md[0], day=start_md[1])
        b = pd.Timestamp(year=y + 1, month=end_md[0], day=end_md[1])
        s = t.loc[a:b]
        if len(s) and s.index[0] == a and s.index[-1] == b:
            out[y] = s.to_numpy()
    return out


def warming_shift(t: pd.Series, target_season: int) -> dict[int, float]:
    """Shift per season that moves its winter (Nov-Mar) mean to the linear trend value of the
    target season: removes the warming trend, keeps the year-to-year variability."""
    w = t[in_season(t.index) & (t.index.month != 10)]
    mean = w.groupby(season_of(w.index)).mean()
    mean = mean[w.groupby(season_of(w.index)).size() > 140]
    b = np.polyfit(mean.index, mean.to_numpy(), 1)
    return {int(y): float(np.polyval(b, target_season) - np.polyval(b, y)) for y in mean.index}


def simulate_fill(start_twh: float, wgv_twh: float, nw_gwh: np.ndarray, cap: pd.Series
                  ) -> np.ndarray:
    """Storage path (TWh) under daily net withdrawal, capped by fill-dependent deliverability."""
    out = np.empty(len(nw_gwh))
    v = start_twh
    edges = cap.index.to_numpy()
    for i, w in enumerate(nw_gwh):
        full = 100 * v / wgv_twh
        limit = cap.iloc[min(np.searchsorted(edges, full), len(edges) - 1)]
        w = min(w, limit, 1000 * v)
        v = min(v - w / 1000, wgv_twh)
        out[i] = v
    return out
