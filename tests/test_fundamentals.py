import numpy as np
import pandas as pd
import pytest

from gasstor import fundamentals as fm
from gasstor.weather import hdd


def test_fit_recovers_degree_day_slope():
    rng = np.random.default_rng(0)
    idx = pd.date_range("2022-07-01", "2025-06-30")
    t = pd.Series(8 - 8 * np.cos(2 * np.pi * (idx.dayofyear - 20) / 365) * -1, index=idx)
    t = t + rng.normal(0, 3, len(idx))
    h = hdd(t)
    nw = 150 * h + 40 * h.shift(1).fillna(0) - 1200 + rng.normal(0, 50, len(idx))
    beta, _ = fm.fit(nw, h, [2022, 2023, 2024])
    assert beta["hdd"] == pytest.approx(150, rel=0.05)
    assert beta["hdd_lag"] == pytest.approx(40, rel=0.2)


def test_hdd_convention():
    h = hdd(pd.Series([20.0, 15.0, 14.9, 0.0]))
    assert h.tolist() == pytest.approx([0.0, 0.0, 3.1, 18.0])


def test_simulate_fill_respects_deliverability_and_empty_floor():
    cap = pd.Series([100.0, 1000.0], index=[50.0, 100.0])    # GWh/d below / above 50% fill
    path = fm.simulate_fill(start_twh=6.0, wgv_twh=10.0, nw_gwh=np.full(200, 500.0), cap=cap)
    assert path[0] == pytest.approx(5.5)                      # 60% full: 500 GWh/d allowed
    assert np.all(np.diff(path) >= -0.5 - 1e-12)
    assert path.min() >= 0.0
    assert path[-1] == pytest.approx(0.0, abs=1e-9)


def test_deliverability_extrapolates_to_zero():
    full = np.r_[np.linspace(31, 100, 500)]
    df = pd.DataFrame({"full": full, "withdrawal": 50 * full})
    cap = fm.deliverability(df, bins=10, q=1.0)
    assert cap.loc[10.0] == pytest.approx(cap.loc[40.0] * 10 / 40)
    assert cap.is_monotonic_increasing
