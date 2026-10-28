"""A seasonal gas storage facility and its intrinsic value.

The intrinsic value is what the owner locks in today by buying the forward curve for injection
months and selling it for withdrawal months: a linear program over daily injection and withdrawal
(SciPy, HiGHS) with rate limits, inventory bounds, a start and an end inventory and variable
injection and withdrawal costs. Injection and withdrawal rates are constant (no ratchets that
depend on the fill level).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import linprog


@dataclass(frozen=True)
class Facility:
    wgv: float = 1_000_000.0      # working gas volume, MWh (1 TWh)
    inj_days: int = 150           # days to fill from empty at the maximum injection rate
    wd_days: int = 75             # days to empty from full at the maximum withdrawal rate
    inj_cost: float = 0.25        # EUR per MWh injected (variable cost, fuel and network)
    wd_cost: float = 0.10         # EUR per MWh withdrawn
    start_fill: float = 0.0       # fraction of WGV on the first day
    end_fill: float = 0.0         # fraction of WGV required after the last day

    @property
    def inj_rate(self) -> float:
        return self.wgv / self.inj_days

    @property
    def wd_rate(self) -> float:
        return self.wgv / self.wd_days


def discount_factors(n_days: int, rate: float, offset_days: int = 0) -> np.ndarray:
    t = (offset_days + np.arange(n_days)) / 365.0
    return np.exp(-rate * t)


@dataclass
class IntrinsicResult:
    value: float                  # EUR
    schedule: pd.DataFrame        # daily injection, withdrawal, inventory (MWh)


def intrinsic(fac: Facility, fwd: pd.Series, rate: float = 0.0, offset_days: int = 0
              ) -> IntrinsicResult:
    """Optimal static schedule against a daily forward curve (EUR/MWh)."""
    f = fwd.to_numpy(float)
    n = len(f)
    d = discount_factors(n, rate, offset_days)
    # variables x = [inj_0..inj_{n-1}, wd_0..wd_{n-1}]; maximise sum d*(f*(wd - inj) - costs)
    c = np.concatenate([d * (f + fac.inj_cost), -d * (f - fac.wd_cost)])
    cum = sparse.csr_matrix(np.tril(np.ones((n, n))))
    a_inv = sparse.hstack([cum, -cum], format="csr")       # inventory change after each day
    v0 = fac.start_fill * fac.wgv
    a_ub = sparse.vstack([a_inv, -a_inv], format="csr")
    b_ub = np.concatenate([np.full(n, fac.wgv - v0), np.full(n, v0)])
    a_eq = a_inv[-1:]
    b_eq = [fac.end_fill * fac.wgv - v0]
    bounds = [(0, fac.inj_rate)] * n + [(0, fac.wd_rate)] * n
    res = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if res.status != 0:
        raise RuntimeError(f"intrinsic LP failed: {res.message}")
    inj, wd = res.x[:n], res.x[n:]
    sched = pd.DataFrame({"forward": f, "injection": inj, "withdrawal": wd,
                          "inventory": v0 + np.cumsum(inj - wd)}, index=fwd.index)
    return IntrinsicResult(value=-res.fun, schedule=sched)
