"""Backtest: what would the storage have earned in each past gas year (April to March)?

The realised TTF front-month path, filled forward to calendar days, stands in for the spot price.
For each gas year three strategies trade the same facility on that path:

- perfect foresight: the intrinsic LP on the realised prices, an upper bound no strategy reaches;
- calendar rule: inject at the full rate from 1 April until full, withdraw at the full rate from
  1 December until empty, the textbook summer-to-winter trade;
- LSMC policy: on 31 March the forward curve is the last price scaled by seasonal factors and the
  spot model is calibrated on the previous three years, both estimated only from data before that
  day; the policy is trained on simulated paths and then executed day by day on the realised path.

All three hold the gas unhedged, so the results measure trading the physical storage against spot,
not the value locked in with futures on 1 April.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import curve, lsmc, spot
from .storage import Facility, intrinsic


def calendar_rule(fac: Facility, p: pd.Series, withdraw_from: str = "12-01") -> float:
    v, total = fac.start_fill * fac.wgv, 0.0
    start_wd = pd.Timestamp(f"{p.index[0].year}-{withdraw_from}")
    for day, price in p.items():
        if day < start_wd:
            q = min(fac.inj_rate, fac.wgv - v)
            total -= (price + fac.inj_cost) * q
            v += q
        else:
            q = min(fac.wd_rate, v)
            total += (price - fac.wd_cost) * q
            v -= q
    if v > 1e-6:
        raise RuntimeError("calendar rule did not empty the storage")
    return total


def gas_year(fac: Facility, px: pd.Series, year: int, n_paths: int = 4000, seed: int = 0) -> dict:
    start, end = pd.Timestamp(f"{year}-04-01"), pd.Timestamp(f"{year + 1}-04-01")
    days = pd.date_range(start, end, freq="D", inclusive="left")
    realised = px.reindex(px.index.union(days)).ffill().reindex(days)
    hist = px[px.index < start]

    factors = curve.seasonal_factors(hist)
    ou = spot.calibrate(hist, factors, start=str(start - pd.DateOffset(years=3)))
    anchor_month = curve.delivery_month(pd.DatetimeIndex([hist.index[-1]]))[0]
    months = pd.period_range(start, end - pd.Timedelta(days=1), freq="M")
    fwd_m = curve.forward_curve(float(hist.iloc[-1]), anchor_month, months, factors)
    fwd = curve.daily_from_monthly(fwd_m, start, end).to_numpy()

    train = spot.simulate(fwd, ou, n_paths, offset_days=1, seed=seed)
    policy = lsmc.value(fac, fwd, train, realised.to_numpy()[None, :]).value
    winter = fwd_m[fwd_m.index.month.isin([10, 11, 12, 1, 2, 3])].mean()
    summer = fwd_m[fwd_m.index.month.isin([4, 5, 6, 7, 8, 9])].mean()
    return {
        "gas_year": f"{year}/{str(year + 1)[-2:]}",
        "price_31_march": round(float(hist.iloc[-1]), 2),
        "curve_winter_minus_summer": round(winter - summer, 2),
        "kappa": round(ou.kappa, 2), "sigma": round(ou.sigma, 2),
        "intrinsic_on_curve": intrinsic(fac, pd.Series(fwd, index=days)).value / fac.wgv,
        "perfect_foresight": intrinsic(fac, realised).value / fac.wgv,
        "calendar_rule": calendar_rule(fac, realised) / fac.wgv,
        "lsmc_policy": policy / fac.wgv,
    }


def run(fac: Facility, px: pd.Series, years: range, n_paths: int = 4000) -> pd.DataFrame:
    return pd.DataFrame([gas_year(fac, px, y, n_paths) for y in years]).set_index("gas_year").round(3)
