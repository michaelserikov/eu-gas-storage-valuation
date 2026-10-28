"""One-factor mean-reverting spot price model consistent with the forward curve.

log S(t) = log F(t) + x(t) - Var[x(t)] / 2, with x an Ornstein-Uhlenbeck process started at zero
on the valuation date: dx = -kappa x dt + sigma dW. Then E[S(t)] = F(t) for every day, so the
simulated prices are centred on the forward curve and only their dispersion and mean reversion
come from the model (Schwartz 1997, one-factor model).

kappa and sigma are estimated from the deseasonalised log front-month price by an AR(1) regression
on trading days. The front-month series moves less than day-ahead gas and carries contract-roll
jumps, so the estimates are indicative and the valuation is reported over a grid of both.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .curve import delivery_month

TRADING_DAYS = 252


@dataclass(frozen=True)
class OUParams:
    kappa: float   # mean reversion per year
    sigma: float   # volatility of x per sqrt(year)

    @property
    def half_life_days(self) -> float:
        return np.log(2) / self.kappa * 365 if self.kappa > 0 else np.inf


def calibrate(px: pd.Series, factors: pd.Series, start: str | None = None, end: str | None = None
              ) -> OUParams:
    """AR(1) on log price minus the seasonal factor of its delivery month and minus a mean."""
    s = px.loc[start:end]
    x = np.log(s.to_numpy()) - factors.reindex(delivery_month(s.index).month).to_numpy()
    x0, x1 = x[:-1], x[1:]
    b, a = np.polyfit(x0, x1, 1)
    resid = x1 - (a + b * x0)
    b = min(b, 1 - 1e-6)
    dt = 1 / TRADING_DAYS
    kappa = -np.log(b) / dt
    # exact discretisation: Var(resid) = sigma^2 (1 - b^2) / (2 kappa)
    sigma = np.sqrt(resid.var(ddof=2) * 2 * kappa / (1 - b ** 2))
    return OUParams(kappa=float(kappa), sigma=float(sigma))


def simulate(fwd: np.ndarray, p: OUParams, n_paths: int, offset_days: int = 0,
             seed: int | None = 0, antithetic: bool = True) -> np.ndarray:
    """Daily spot paths (n_paths x len(fwd)) for calendar days offset_days.. after valuation."""
    rng = np.random.default_rng(seed)
    n = len(fwd)
    dt = 1 / 365
    k = max(p.kappa, 1e-8)
    phi = np.exp(-k * dt)
    step_sd = p.sigma * np.sqrt((1 - phi ** 2) / (2 * k))
    half = (n_paths + 1) // 2 if antithetic else n_paths
    # state on the first storage day after offset_days of evolution
    sd0 = p.sigma * np.sqrt((1 - np.exp(-2 * k * offset_days * dt)) / (2 * k))
    z0 = rng.standard_normal(half)
    z = rng.standard_normal((half, n - 1))
    if antithetic:
        z0 = np.concatenate([z0, -z0])[:n_paths]
        z = np.concatenate([z, -z])[:n_paths]
    x = np.empty((n_paths, n))
    x[:, 0] = sd0 * z0
    for t in range(1, n):
        x[:, t] = phi * x[:, t - 1] + step_sd * z[:, t - 1]
    tt = (offset_days + np.arange(n)) * dt
    var = p.sigma ** 2 * (1 - np.exp(-2 * k * tt)) / (2 * k)
    return fwd[None, :] * np.exp(x - var[None, :] / 2)
