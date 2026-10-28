"""Daily gas storage data from GIE AGSI+ (aggregated gas storage inventory), cached on disk.

A free API key is required (registration at agsi.gie.eu); it is read from the environment variable
AGSI_KEY and never stored. Units as published: gas in storage and working gas volume in TWh,
injection, withdrawal and their capacities in GWh per day, fill level in percent.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pandas as pd
import requests

URL = "https://agsi.gie.eu/api"
FIELDS = ["gasInStorage", "injection", "withdrawal", "netWithdrawal", "workingGasVolume",
          "injectionCapacity", "withdrawalCapacity", "full", "consumption"]


def _key() -> str:
    key = os.environ.get("AGSI_KEY")
    if not key:
        raise RuntimeError("set the AGSI_KEY environment variable (free key from agsi.gie.eu)")
    return key


def download(raw_dir: Path, country: str, start: str, end: str, refresh: bool = False) -> Path:
    path = raw_dir / f"agsi_{country}_{start}_{end}.json"
    if path.exists() and not refresh:
        return path
    rows, page, last = [], 1, 1
    while page <= last:
        r = requests.get(URL, headers={"x-key": _key()}, timeout=120,
                         params={"country": country, "from": start, "to": end, "size": 300,
                                 "page": page})
        r.raise_for_status()
        d = r.json()
        rows += d["data"]
        last = d["last_page"]
        page += 1
        time.sleep(1)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    return path


def parse(path: Path) -> pd.DataFrame:
    df = pd.DataFrame(json.loads(path.read_text()))
    df.index = pd.to_datetime(df["gasDayStart"])
    df = df[FIELDS].apply(pd.to_numeric, errors="coerce").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df.index.name = "gas_day"
    return df


def load(raw_dir: Path, country: str = "de", start: str = "2011-01-01", end: str | None = None,
         refresh: bool = False) -> pd.DataFrame:
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    return parse(download(raw_dir, country, start, end, refresh))
