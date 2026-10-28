"""Storage value under price uncertainty by least-squares Monte Carlo (Boogert and de Jong 2008).

Inventory lives on a grid whose step is one day of maximum injection, so injecting at full rate
moves up one level and withdrawing at full rate moves down wd_steps levels (the facility's rates
must be in an integer ratio). Each day the owner chooses the move that maximises today's cash flow
plus the expected value of the next day's inventory, and that expectation is estimated by
regressing next day's realised value on polynomials of today's price, across paths and separately
for every inventory level. Only moves that can still reach the required end inventory are allowed.

The regression coefficients come from a backward pass on training paths; the reported value is the
mean cash flow of that policy on independent paths, so it is a lower bound of the true value of the
model up to Monte Carlo error. With zero volatility it reproduces the intrinsic value.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .storage import Facility, discount_factors


@dataclass
class LSMCResult:
    value: float              # EUR, mean over evaluation paths
    std_error: float          # EUR
    mean_inventory: np.ndarray  # MWh after each day, averaged over evaluation paths


def _grid(fac: Facility):
    ratio = fac.inj_days / fac.wd_days
    wd_steps = int(round(ratio))
    if abs(ratio - wd_steps) > 1e-9:
        raise ValueError("inj_days / wd_days must be an integer")
    n_lev = fac.inj_days + 1
    step = fac.wgv / fac.inj_days
    start = int(round(fac.start_fill * fac.inj_days))
    end = int(round(fac.end_fill * fac.inj_days))
    moves = np.arange(-wd_steps, 2)          # -wd_steps .. +1 levels per day
    return n_lev, step, start, end, moves


def _feasible(n_lev: int, end: int, days_left: int, wd_steps: int) -> np.ndarray:
    """Levels from which the end level is reachable in days_left days."""
    lev = np.arange(n_lev)
    return (lev - end <= wd_steps * days_left) & (end - lev <= days_left)


def _basis(s: np.ndarray, f: float, degree: int) -> np.ndarray:
    z = s / f
    return np.vander(z, degree + 1, increasing=True)


def _cash(moves: np.ndarray, step: float, s: np.ndarray, fac: Facility) -> np.ndarray:
    """Cash flow (paths x moves) of each move at price s."""
    vol = moves * step
    inj = np.clip(vol, 0, None)
    wd = np.clip(-vol, 0, None)
    return -(s[:, None] + fac.inj_cost) * inj[None, :] + (s[:, None] - fac.wd_cost) * wd[None, :]


def _padded(cont, ok_next, wd_steps):
    """Continuation values with -inf for unreachable or out-of-range next levels, padded so that
    column l + m + wd_steps holds the value of moving from level l by m."""
    c = np.where(ok_next[None, :], cont, -np.inf)
    n_paths = c.shape[0]
    return np.hstack([np.full((n_paths, wd_steps), -np.inf), c, np.full((n_paths, 1), -np.inf)])


def _decide(cont, cash, n_lev, moves, ok_next):
    """Best move index (paths x levels) given continuation values cont (paths x levels)."""
    wd_steps = -moves[0]
    pad = _padded(cont, ok_next, wd_steps)
    # moving by moves[j] from level l reads column l + j of the padded array: a plain slice
    best_val = cash[:, :1] + pad[:, :n_lev]
    best = np.zeros(best_val.shape, dtype=np.intp)
    for j in range(1, len(moves)):
        cand = cash[:, j:j + 1] + pad[:, j:j + n_lev]
        better = cand > best_val
        best_val = np.where(better, cand, best_val)
        best[better] = j
    return best


def value(fac: Facility, fwd: np.ndarray, train: np.ndarray, test: np.ndarray, rate: float = 0.0,
          offset_days: int = 0, degree: int = 3) -> LSMCResult:
    n_lev, step, start, end, moves = _grid(fac)
    wd_steps = -moves[0]
    n = len(fwd)
    d = discount_factors(n, rate, offset_days)
    lev = np.arange(n_lev)

    # backward pass on training paths: V[p, l] = discounted value from day t on, at level l
    v_next = np.where(lev == end, 0.0, -np.inf)[None, :].repeat(train.shape[0], axis=0)
    betas = [None] * n
    for t in range(n - 1, -1, -1):
        s = train[:, t]
        ok_next = _feasible(n_lev, end, n - 1 - t, wd_steps)
        x = _basis(s, fwd[t], degree)
        y = np.where(np.isfinite(v_next), v_next, 0.0)[:, ok_next]
        beta = np.zeros((x.shape[1], n_lev))
        beta[:, ok_next] = np.linalg.lstsq(x, y, rcond=None)[0]
        betas[t] = beta
        cont = x @ beta
        cash = d[t] * _cash(moves, step, s, fac)
        best = _decide(cont, cash, n_lev, moves, ok_next)
        # realised (not fitted) value of the chosen move, as in Longstaff and Schwartz
        nxt = lev[None, :] + moves[best]
        nxt_c = np.clip(nxt, 0, n_lev - 1)
        v_now = np.take_along_axis(cash, best, axis=1) + np.take_along_axis(v_next, nxt_c, axis=1)
        ok_now = _feasible(n_lev, end, n - t, wd_steps)
        v_now[:, ~ok_now] = -np.inf
        v_next = v_now

    # forward pass on independent paths with the stored policy
    n_paths = test.shape[0]
    level = np.full(n_paths, start)
    total = np.zeros(n_paths)
    inv = np.zeros(n)
    rows = np.arange(n_paths)
    for t in range(n):
        s = test[:, t]
        ok_next = _feasible(n_lev, end, n - 1 - t, wd_steps)
        cont = _basis(s, fwd[t], degree) @ betas[t]
        cash = d[t] * _cash(moves, step, s, fac)
        pad = _padded(cont, ok_next, wd_steps)
        cand = cash + pad[rows[:, None], level[:, None] + moves[None, :] + wd_steps]
        best = cand.argmax(axis=1)
        total += cash[rows, best]
        level = level + moves[best]
        inv[t] = level.mean() * step
    if not np.all(level == end):
        raise RuntimeError("policy did not reach the end inventory")
    return LSMCResult(value=float(total.mean()),
                      std_error=float(total.std(ddof=1) / np.sqrt(n_paths)) if n_paths > 1
                      else 0.0,
                      mean_inventory=inv)
