"""v0.1: TTF seasonality, then the intrinsic and extrinsic value of a seasonal storage for the next
storage year (1 April to 31 March), valued on the last trading day in the data.

Writes results/*.csv, results/summary.json and figures/*.png. Usage: python -m gasstor.run
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import backtest, curve, lsmc, plots, prices, spot
from .storage import Facility, intrinsic

ROOT = Path(__file__).resolve().parents[2]
RAW, RES, FIG = ROOT / "data" / "raw", ROOT / "results", ROOT / "figures"
N_PATHS = 8000       # base case
N_SENS = 2000        # sensitivity runs
RATE = 0.025          # discount rate per year


def per_mwh(x: float, fac: Facility) -> float:
    return round(x / fac.wgv, 3)


def value_case(fac, fwd_daily, ou, offset, seed=0, n_paths=N_SENS):
    f = fwd_daily.to_numpy()
    intr = intrinsic(fac, fwd_daily, RATE, offset).value
    train = spot.simulate(f, ou, n_paths, offset, seed=seed)
    test = spot.simulate(f, ou, n_paths, offset, seed=seed + 1)
    res = lsmc.value(fac, f, train, test, RATE, offset)
    return {"intrinsic": per_mwh(intr, fac), "total": per_mwh(res.value, fac),
            "extrinsic": per_mwh(res.value - intr, fac), "std_error": per_mwh(res.std_error, fac)}


def main() -> None:
    RES.mkdir(exist_ok=True)
    FIG.mkdir(exist_ok=True)
    px = prices.load(RAW)
    val_date = px.index[-1]
    anchor_month = curve.delivery_month(pd.DatetimeIndex([val_date]))[0]
    anchor = float(px.iloc[-1])

    # seasonality and realised spreads
    factors = curve.seasonal_factors(px)
    factors_all = curve.seasonal_factors(px, exclude_crisis=False)
    pd.DataFrame({"factor_excl_crisis": np.exp(factors), "factor_all": np.exp(factors_all)}
                 ).round(4).to_csv(RES / "seasonal_factors.csv", index_label="delivery_month")
    spreads = curve.realised_seasonal_spread(px).round(2)
    spreads.to_csv(RES / "realised_seasonal_spread.csv")

    # spot model
    windows = {"2017-2021 (pre-crisis)": ("2017", "2021-08"),
               "2023-2026 (post-crisis)": ("2023-04", None)}
    ou = {k: spot.calibrate(px, factors, *w) for k, w in windows.items()}
    pd.DataFrame({k: {"kappa": v.kappa, "sigma": v.sigma, "half_life_days": v.half_life_days}
                  for k, v in ou.items()}).T.round(3).to_csv(RES / "ou_calibration.csv")
    base_ou = ou["2023-2026 (post-crisis)"]

    # storage year
    year = (val_date + pd.offsets.DateOffset(months=6)).year
    start, end = pd.Timestamp(f"{year}-04-01"), pd.Timestamp(f"{year + 1}-04-01")
    months = pd.period_range(start, end - pd.Timedelta(days=1), freq="M")
    offset = (start - val_date).days
    fwd_m = curve.forward_curve(anchor, anchor_month, months, factors)
    fwd_m.round(2).to_csv(RES / "forward_curve.csv", index_label="month")
    fwd_d = curve.daily_from_monthly(fwd_m, start, end)

    fac = Facility()
    intr = intrinsic(fac, fwd_d, RATE, offset)
    intr.schedule.round(1).to_csv(RES / "intrinsic_schedule.csv", index_label="date")
    base = value_case(fac, fwd_d, base_ou, offset, n_paths=N_PATHS)

    # sensitivity: volatility and mean reversion
    rows = []
    for sigma in (0.25, 0.5, 0.75, 1.0):
        for kappa in (1.0, 3.0, 10.0):
            r = value_case(fac, fwd_d, spot.OUParams(kappa, sigma), offset)
            rows.append({"sigma": sigma, "kappa": kappa, **r})
    pd.DataFrame(rows).to_csv(RES / "sensitivity_vol.csv", index=False)

    # sensitivity: depth of the seasonal shape (0 = flat curve, 1 = estimated shape)
    rows = []
    for scale in (0.0, 0.25, 0.5, 1.0):
        fm = curve.forward_curve(anchor, anchor_month, months, factors * scale)
        fd = curve.daily_from_monthly(fm, start, end)
        winter = fm[fm.index.month.isin([10, 11, 12, 1, 2, 3])].mean()
        summer = fm[fm.index.month.isin([4, 5, 6, 7, 8, 9])].mean()
        rows.append({"shape_scale": scale, "winter_minus_summer": round(winter - summer, 2),
                     **value_case(fac, fd, base_ou, offset)})
    pd.DataFrame(rows).to_csv(RES / "sensitivity_spread.csv", index=False)

    # facility flexibility: seasonal versus fast-cycle storage of the same volume
    rows = []
    for name, inj, wd in (("seasonal 150/75 days", 150, 75), ("medium 60/30 days", 60, 30),
                          ("fast-cycle 20/10 days", 20, 10)):
        r = value_case(replace(fac, inj_days=inj, wd_days=wd), fwd_d, base_ou, offset)
        rows.append({"facility": name, **r})
    pd.DataFrame(rows).to_csv(RES / "sensitivity_facility.csv", index=False)

    # backtest on past gas years (seasonal factors need about two years of history first)
    bt = backtest.run(fac, px, range(2019, val_date.year if val_date.month >= 4 else val_date.year - 1),
                      n_paths=N_SENS)
    bt.to_csv(RES / "backtest_gas_years.csv")

    summary = {
        "valuation_date": str(val_date.date()), "anchor_month": str(anchor_month),
        "anchor_price": round(anchor, 2), "storage_year": f"{start.date()} to {end.date()}",
        "facility": asdict(fac), "discount_rate": RATE, "paths": N_PATHS, "paths_sensitivity": N_SENS,
        "ou_base": {"kappa": round(base_ou.kappa, 3), "sigma": round(base_ou.sigma, 3)},
        "value_eur_per_mwh_wgv": base,
        "backtest_sum_eur_per_mwh_wgv": bt[["perfect_foresight", "calendar_rule", "lsmc_policy"]]
        .sum().round(2).to_dict(),
    }
    (RES / "summary.json").write_text(json.dumps(summary, indent=2))
    plots.all_figures(px, factors, spreads, fwd_m, intr.schedule, FIG)
    plots.backtest_figure(bt, FIG)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
