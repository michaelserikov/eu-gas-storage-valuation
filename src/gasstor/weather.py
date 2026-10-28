"""Daily mean temperature for Germany from ERA5 and heating degree days, cached on disk.

Population-weighted mean of the daily 2 m temperature at the ten largest German cities, from the
Open-Meteo historical weather API (ERA5 reanalysis, 1991 onwards). Heating degree days follow the
Eurostat convention: 18 - T on days with a mean temperature below 15 C, else zero.

Data: Open-Meteo.com, CC BY 4.0; ERA5: Copernicus Climate Change Service.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

URL = "https://archive-api.open-meteo.com/v1/archive"
# city: (lat, lon, population in millions, 2023, rounded)
CITIES = {
    "Berlin": (52.52, 13.40, 3.76), "Hamburg": (53.55, 9.99, 1.89), "Munich": (48.14, 11.58, 1.51),
    "Cologne": (50.94, 6.96, 1.08), "Frankfurt": (50.11, 8.68, 0.77), "Stuttgart": (48.78, 9.18, 0.63),
    "Duesseldorf": (51.23, 6.78, 0.63), "Leipzig": (51.34, 12.37, 0.62),
    "Dortmund": (51.51, 7.47, 0.59), "Essen": (51.46, 7.01, 0.58),
}
_HEADERS = {"User-Agent": "eu-gas-storage-valuation (research; github.com/michaelserikov)"}


def download(raw_dir: Path, start: str, end: str, refresh: bool = False) -> Path:
    path = raw_dir / f"temperature_de_{start}_{end}.json"
    if path.exists() and not refresh:
        return path
    out = {}
    for city, (lat, lon, _) in CITIES.items():
        for attempt in range(10):
            r = requests.get(URL, headers=_HEADERS, timeout=180, params={
                "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
                "daily": "temperature_2m_mean", "timezone": "Europe/Berlin"})
            if r.status_code == 429:
                time.sleep(60)
                continue
            r.raise_for_status()
            out[city] = r.json()["daily"]
            break
        time.sleep(2)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return path


def parse(path: Path) -> pd.Series:
    raw = json.loads(path.read_text())
    w = pd.Series({c: v[2] for c, v in CITIES.items()})
    df = pd.DataFrame({c: pd.Series(d["temperature_2m_mean"], index=pd.to_datetime(d["time"]))
                       for c, d in raw.items()}, dtype=float)
    t = (df * w).sum(axis=1, min_count=len(w)) / w.sum()
    return t.dropna().rename("t2m")


def load(raw_dir: Path, start: str = "1991-01-01", end: str | None = None,
         refresh: bool = False) -> pd.Series:
    end = end or (pd.Timestamp.today() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    return parse(download(raw_dir, start, end, refresh))


def hdd(t: pd.Series) -> pd.Series:
    return (18.0 - t).where(t < 15.0, 0.0).rename("hdd")
