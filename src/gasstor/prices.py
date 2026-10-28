"""Daily Dutch TTF front-month futures prices (EUR/MWh), cached on disk.

The open series is the continuous front-month contract TTF=F as published on Yahoo Finance, daily
settlement-like closes from October 2017. On a given trade date it is the price of gas delivered
in the next calendar month, so it is a proxy for the spot price with one month of delivery lag,
and it jumps at contract rolls. The raw JSON is written to data/raw/ and not committed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import requests

YAHOO_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/TTF=F"
             "?period1=0&period2=9999999999&interval=1d")
_HEADERS = {"User-Agent": "Mozilla/5.0 (eu-gas-storage-valuation; research)"}


def download(raw_dir: Path, refresh: bool = False) -> Path:
    path = raw_dir / "ttf_front_month.json"
    if refresh or not path.exists():
        r = requests.get(YAHOO_URL, headers=_HEADERS, timeout=60)
        r.raise_for_status()
        raw_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
    return path


def parse(path: Path) -> pd.Series:
    """Daily close by trade date (Europe/Amsterdam calendar day), missing closes dropped."""
    res = json.loads(path.read_text())["chart"]["result"][0]
    ts = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert("Europe/Amsterdam")
    s = pd.Series(res["indicators"]["quote"][0]["close"], index=ts.normalize().tz_localize(None),
                  name="ttf_eur_mwh", dtype=float)
    s = s[~s.index.duplicated(keep="last")].dropna()
    return s[s > 0].sort_index()


def load(raw_dir: Path, refresh: bool = False) -> pd.Series:
    return parse(download(raw_dir, refresh))
