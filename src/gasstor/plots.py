"""Figures for the README."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

MONTHS = "JFMAMJJASOND"


def all_figures(px, factors, spreads, fwd_m, schedule, out: Path) -> None:
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.8))
    px.plot(ax=ax[0], lw=0.8, color="#1f4e79")
    ax[0].set(title="TTF front month, daily close", ylabel="EUR/MWh", xlabel="")
    ax[1].bar(range(1, 13), np.exp(factors), color="#1f4e79")
    ax[1].axhline(1, color="k", lw=0.6)
    ax[1].set(title="Seasonal factor by delivery month (excl. 2021-09 to 2023-03)",
              xticks=range(1, 13), xticklabels=list(MONTHS), ylim=(0.7, 1.3))
    fig.tight_layout()
    fig.savefig(out / "ttf_seasonality.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 3.5))
    colors = ["#2e7d32" if s > 0 else "#c62828" for s in spreads["spread"]]
    ax.bar(spreads.index.astype(str), spreads["spread"], color=colors)
    ax.axhline(0, color="k", lw=0.6)
    ax.set(title="Realised winter minus summer price, by gas year", ylabel="EUR/MWh")
    fig.tight_layout()
    fig.savefig(out / "realised_seasonal_spread.png", dpi=130)
    plt.close(fig)

    fig, ax1 = plt.subplots(figsize=(8, 3.6))
    ax1.plot(schedule.index, schedule["inventory"] / schedule["inventory"].max() * 100,
             color="#1f4e79", label="inventory (intrinsic)")
    ax1.set(ylabel="% of working gas volume", ylim=(0, 105))
    ax2 = ax1.twinx()
    ax2.step(schedule.index, schedule["forward"], where="post", color="#c62828", label="forward")
    ax2.set(ylabel="EUR/MWh")
    ax1.set_title("Intrinsic schedule against the illustrative forward curve")
    fig.legend(loc="upper left", bbox_to_anchor=(0.08, 0.88), frameon=False)
    fig.tight_layout()
    fig.savefig(out / "intrinsic_schedule.png", dpi=130)
    plt.close(fig)


def backtest_figure(bt: pd.DataFrame, out: Path) -> None:
    cols = {"perfect_foresight": ("perfect foresight", "#9e9e9e"),
            "calendar_rule": ("calendar rule", "#c62828"),
            "lsmc_policy": ("LSMC policy", "#1f4e79")}
    x = np.arange(len(bt))
    fig, ax = plt.subplots(figsize=(8, 3.8))
    for i, (c, (label, color)) in enumerate(cols.items()):
        ax.bar(x + (i - 1) * 0.27, bt[c], width=0.27, label=label, color=color)
    ax.axhline(0, color="k", lw=0.6)
    ax.set(xticks=x, xticklabels=bt.index, ylabel="EUR per MWh of working gas volume",
           title="Unhedged storage trading on realised TTF prices, by gas year")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out / "backtest_gas_years.png", dpi=130)
    plt.close(fig)
