import numpy as np
import pandas as pd
import pytest

from gasstor import curve, lsmc, spot
from gasstor.storage import Facility, intrinsic

FAC = Facility(wgv=150.0, inj_days=30, wd_days=15, inj_cost=0.0, wd_cost=0.0)


def _fwd(values):
    return pd.Series(values, index=pd.date_range("2027-04-01", periods=len(values), freq="D"))


def test_intrinsic_flat_curve_is_zero():
    res = intrinsic(FAC, _fwd(np.full(60, 30.0)))
    assert res.value == pytest.approx(0.0, abs=1e-6)


def test_intrinsic_two_price_spread():
    # 30 days at 20 then 30 days at 30: fill fully, then empty; value = spread * WGV
    res = intrinsic(FAC, _fwd([20.0] * 30 + [30.0] * 30))
    assert res.value == pytest.approx(10.0 * FAC.wgv, rel=1e-9)
    assert res.schedule["inventory"].max() == pytest.approx(FAC.wgv)
    assert res.schedule["inventory"].iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_intrinsic_costs_reduce_value():
    fac = Facility(wgv=150.0, inj_days=30, wd_days=15, inj_cost=1.0, wd_cost=0.5)
    res = intrinsic(fac, _fwd([20.0] * 30 + [30.0] * 30))
    assert res.value == pytest.approx(8.5 * fac.wgv, rel=1e-9)


def test_lsmc_without_volatility_equals_intrinsic():
    f = 25 + 5 * np.cos(np.linspace(0, 2 * np.pi, 90))
    p = spot.OUParams(kappa=5.0, sigma=0.0)
    paths = spot.simulate(f, p, 50)
    res = lsmc.value(FAC, f, paths, paths)
    assert res.value == pytest.approx(intrinsic(FAC, _fwd(f)).value, rel=1e-6)


def test_lsmc_extrinsic_is_positive_with_volatility():
    f = 25 + 5 * np.cos(np.linspace(0, 2 * np.pi, 90))
    p = spot.OUParams(kappa=20.0, sigma=0.8)
    train = spot.simulate(f, p, 4000, seed=1)
    test = spot.simulate(f, p, 4000, seed=2)
    res = lsmc.value(FAC, f, train, test)
    assert res.value > intrinsic(FAC, _fwd(f)).value + 3 * res.std_error


def test_simulated_mean_matches_forward():
    f = np.linspace(20, 40, 200)
    paths = spot.simulate(f, spot.OUParams(kappa=3.0, sigma=0.6), 40000, offset_days=100)
    assert np.allclose(paths.mean(axis=0) / f, 1.0, atol=0.01)


def test_calibration_recovers_ou_parameters():
    rng = np.random.default_rng(0)
    kappa, sigma, dt, n = 8.0, 0.5, 1 / 252, 20000
    phi = np.exp(-kappa * dt)
    sd = sigma * np.sqrt((1 - phi ** 2) / (2 * kappa))
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + sd * rng.standard_normal()
    idx = pd.bdate_range("2000-01-03", periods=n)
    px = pd.Series(30 * np.exp(x), index=idx)
    p = spot.calibrate(px, pd.Series(0.0, index=range(1, 13)))
    assert p.kappa == pytest.approx(kappa, rel=0.25)
    assert p.sigma == pytest.approx(sigma, rel=0.05)


def test_seasonal_factors_recover_known_shape():
    idx = pd.date_range("2015-01-01", "2020-12-31", freq="D")
    true = {m: 0.1 * np.cos(2 * np.pi * (m - 1) / 12) for m in range(1, 13)}
    dm = curve.delivery_month(idx).month
    px = pd.Series(30 * np.exp([true[m] for m in dm]), index=idx)
    s = curve.seasonal_factors(px, exclude_crisis=False)
    expected = pd.Series(true) - np.mean(list(true.values()))
    assert np.allclose(s.to_numpy(), expected.to_numpy(), atol=0.01)


def test_calendar_rule_cash_flow():
    from gasstor.backtest import calendar_rule
    fac = Facility(wgv=30.0, inj_days=30, wd_days=15, inj_cost=0.0, wd_cost=0.0)
    days = pd.date_range("2027-04-01", "2028-03-31", freq="D")
    p = pd.Series(np.where(days < pd.Timestamp("2027-12-01"), 20.0, 30.0), index=days)
    assert calendar_rule(fac, p) == pytest.approx(10.0 * fac.wgv)
