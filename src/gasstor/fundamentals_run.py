"""v0.2: German storage withdrawal model, out-of-sample check and fill scenarios to 31 March.

Needs AGSI_KEY in the environment. Writes results/fundamentals/ and figures/storage_*.png.
Usage: python -m gasstor.fundamentals_run
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import agsi, weather
from . import fundamentals as fm

ROOT = Path(__file__).resolve().parents[2]
RAW, RES, FIG = ROOT / "data" / "raw", ROOT / "results" / "fundamentals", ROOT / "figures"
THRESHOLDS = (30, 20, 10)     # fill levels (%) for the probability table


def held_out(storage, hdd, cap, seasons):
    """Leave one winter out: fit on the others, run the held-out winter with its real weather
    from its real fill on 15 October, compare daily net withdrawal and the 31 March fill."""
    rows = []
    for s in seasons:
        beta, _ = fm.fit(storage["netWithdrawal"], hdd, [x for x in seasons if x != s])
        days = pd.date_range(f"{s}-10-15", f"{s + 1}-03-31")
        obs = storage["netWithdrawal"].reindex(days)
        pred = fm.predict(beta, hdd.reindex(pd.date_range(f"{s}-10-14", days[-1]))).reindex(days)
        # no-weather benchmark: mean net withdrawal by calendar month of the other winters
        other = storage[fm.in_season(storage.index) & np.isin(fm.season_of(storage.index),
                                                             [x for x in seasons if x != s])]
        clim = other["netWithdrawal"].groupby(other.index.month).mean()
        bench = pd.Series(clim.reindex(days.month).to_numpy(), index=days)
        wgv = storage["workingGasVolume"].loc[days[0]]
        v0 = storage["gasInStorage"].loc[days[0] - pd.Timedelta(days=1)]
        path = fm.simulate_fill(v0, wgv, pred.to_numpy(), cap)
        path_b = fm.simulate_fill(v0, wgv, bench.to_numpy(), cap)
        rows.append({
            "winter": f"{s}/{str(s + 1)[-2:]}",
            "mae_gwh_d": round(float((pred - obs).abs().mean()), 1),
            "mae_no_weather_gwh_d": round(float((bench - obs).abs().mean()), 1),
            "fill_31_march_actual": round(float(storage["full"].loc[days[-1]]), 1),
            "fill_31_march_model": round(100 * path[-1] / wgv, 1),
            "fill_31_march_no_weather": round(100 * path_b[-1] / wgv, 1),
        })
    return pd.DataFrame(rows).set_index("winter")


def main() -> None:
    RES.mkdir(parents=True, exist_ok=True)
    storage = agsi.load(RAW, "de")
    t = weather.load(RAW)
    hdd = weather.hdd(t)
    last = storage.index[-1]
    seasons = list(range(fm.REGIME_START, fm.season_of(pd.DatetimeIndex([last]))[0]))

    cap = fm.deliverability(storage[storage.index.year >= 2017])
    cap.rename("max_withdrawal_gwh_d").to_csv(RES / "deliverability.csv", index_label="fill_upper_pct")
    beta, resid = fm.fit(storage["netWithdrawal"], hdd, seasons)
    beta.round(2).to_csv(RES / "coefficients.csv", header=["gwh_per_day"])
    ho = held_out(storage, hdd, cap, seasons)
    ho.to_csv(RES / "held_out_winters.csv")

    # scenarios from the day after the last storage report to 31 March
    start = last + pd.Timedelta(days=1)
    season = int(fm.season_of(pd.DatetimeIndex([start]))[0])
    end = pd.Timestamp(f"{season + 1}-03-31")
    days = pd.date_range(start, end)
    days = days[~((days.month == 2) & (days.day == 29))]
    temps = fm.season_temperatures(t, (start.month, start.day - 1), (3, 31))   # one lag day
    shift = fm.warming_shift(t, season)
    v0, wgv = storage["gasInStorage"].iloc[-1], storage["workingGasVolume"].iloc[-1]

    res_sets = {}
    for s in seasons:
        r = resid[(resid.index >= pd.Timestamp(f"{s}-{start.month}-{start.day}"))
                  & (resid.index <= pd.Timestamp(f"{s + 1}-03-31"))]
        r = r[~((r.index.month == 2) & (r.index.day == 29))].to_numpy()
        res_sets[s] = np.pad(r, (0, max(0, len(days) - len(r))))[:len(days)]

    feb1 = int(np.flatnonzero((days.month == 2) & (days.day == 1))[0])
    rows, paths = [], {}
    for y, tt in temps.items():
        if y not in shift:
            continue
        for adj_name, adj in (("today's climate", shift[y]), ("as observed", 0.0)):
            h = weather.hdd(pd.Series(tt + adj, index=pd.date_range(days[0] - pd.Timedelta(days=1),
                                                                     periods=len(tt))))
            base = fm.predict(beta, h).to_numpy()[1:]
            for s, r in res_sets.items():
                p = 100 * fm.simulate_fill(v0, wgv, base + r, cap) / wgv
                rows.append({"weather_year": f"{y}/{str(y + 1)[-2:]}", "climate": adj_name,
                             "residuals_from": f"{s}/{str(s + 1)[-2:]}",
                             "winter_mean_t": round(float(np.mean(tt[1:] + adj)), 2),
                             "fill_1_feb": round(p[feb1], 1), "fill_31_march": round(p[-1], 1),
                             "min_fill": round(p.min(), 1)})
                if adj_name == "today's climate":
                    paths[(y, s)] = p
    sc = pd.DataFrame(rows)
    sc.to_csv(RES / "scenarios.csv", index=False)

    summ = {}
    for name, g in sc.groupby("climate"):
        d = {"n": len(g)}
        for col in ("fill_1_feb", "fill_31_march"):
            d[col] = {f"p{q}": round(float(g[col].quantile(q / 100)), 1) for q in (5, 25, 50, 75, 95)}
        d["prob_31_march_below"] = {f"{x}%": round(float((g["fill_31_march"] < x).mean()), 3)
                                    for x in THRESHOLDS}
        summ[name] = d
    coldest = (sc[sc["climate"] == "today's climate"].groupby("weather_year")
               .agg(winter_mean_t=("winter_mean_t", "first"), fill_31_march=("fill_31_march", "median"))
               .sort_values("winter_mean_t").head(5))
    summary = {"start_day": str(start.date()), "start_fill_pct": round(100 * v0 / wgv, 2),
               "wgv_twh": round(float(wgv), 1), "fit_winters": [f"{s}/{s + 1}" for s in seasons],
               "hdd_slope_gwh_d": round(float(beta["hdd"] + beta["hdd_lag"]), 1),
               "scenarios": summ, "coldest_5_today": coldest.round(2).reset_index().to_dict("records"),
               "held_out_mae_gwh_d": round(float(ho["mae_gwh_d"].mean()), 1),
               "held_out_mae_no_weather_gwh_d": round(float(ho["mae_no_weather_gwh_d"].mean()), 1)}
    (RES / "summary.json").write_text(json.dumps(summary, indent=2))
    figure(storage, paths, days, FIG)
    print(json.dumps(summary, indent=2))
    print(ho)


def figure(storage, paths, days, out: Path) -> None:
    arr = np.array(list(paths.values()))
    fig, ax = plt.subplots(figsize=(8.5, 4))
    for s in range(2017, int(fm.season_of(pd.DatetimeIndex([days[0]]))[0])):
        h = storage["full"].loc[f"{s}-10-01":f"{s + 1}-03-31"]
        x = h.index - pd.DateOffset(years=s - days[0].year)
        ax.plot(x, h.to_numpy(), color="#bdbdbd", lw=0.8, label="past winters 2017/18 on" if s == 2017 else None)
    obs = storage["full"].loc[f"{days[0].year}-10-01":]
    ax.plot(obs.index, obs.to_numpy(), color="k", lw=1.5, label="this winter, observed")
    ax.fill_between(days, np.percentile(arr, 5, axis=0), np.percentile(arr, 95, axis=0),
                    color="#1f4e79", alpha=0.25, label="scenarios 5-95%")
    ax.plot(days, np.median(arr, axis=0), color="#1f4e79", lw=1.5, label="scenario median")
    ax.set(ylabel="German storage fill (%)", ylim=(0, 100),
           title="German gas storage: 35 weather years in today's climate")
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(out / "storage_fill_scenarios.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
