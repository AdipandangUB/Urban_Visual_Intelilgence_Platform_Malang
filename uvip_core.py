# -*- coding: utf-8 -*-
"""
uvip_core.py — logika parsing data & perhitungan UVI, dipisah dari app.py
agar dapat diuji tanpa dependensi Streamlit.
"""
import io
import numpy as np
import pandas as pd
import requests

SHEET_ID = "1XrgdsW7IVULMQ3LIiEocPAbuFUGBkAnaPBYO0N2gxMA"

CORRIDOR_SHEETS = {
    "1062597004": {"name": "Tugu"},
    "446487299":  {"name": "Kayutangan–Tugu"},
    "891123454":  {"name": "Lafayette–PLN"},
    "1869212551": {"name": "Kayutangan 1"},
    "385636298":  {"name": "Alun-Alun Merdeka"},
}

RAW_CLASSES = [
    "Ground", "Building", "Traffic sign", "Vegetation",
    "Sky", "Human", "Vehicle 4w", "Vehicle 2w",
]

INDICATORS = [
    "Building Visibility", "Vegetation Coverage", "Sky Openness",
    "Ground Accessibility", "Human Activity", "Vehicle Intensity",
    "Traffic Infrastructure", "Heritage Dominance",
]

DEFAULT_WEIGHTS = {k: 1.0 for k in INDICATORS}
UVI_SCALE = 10


def _to_float(val):
    if val is None:
        return np.nan
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return np.nan
    s = s.replace("%", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return np.nan


def _pct_to_frac(val):
    return _to_float(val)


def sheet_csv_url(gid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"


def fetch_raw_sheet(gid: str) -> pd.DataFrame:
    """Ambil data mentah 1 tab spreadsheet sebagai DataFrame tanpa header."""
    url = sheet_csv_url(gid)
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return pd.read_csv(io.StringIO(resp.text), header=None, dtype=str)


def parse_block(df: pd.DataFrame, col0: int, side_label: str, corridor: str) -> pd.DataFrame:
    """Parser generik untuk 1 sisi (kiri/kanan) dari layout paired-block khas
    lembar 'HASIL ANALISIS' hasil deteksi AI. Lihat README untuk skema kolom.
    """
    records = []
    n_rows, n_cols = df.shape
    if col0 + 12 >= n_cols:
        return pd.DataFrame()

    i = 0
    while i < n_rows:
        kode_raw = df.iat[i, col0 + 0] if col0 + 0 < n_cols else None
        kode = None if pd.isna(kode_raw) else str(kode_raw).strip()
        if kode and kode.upper() not in ("KODE", "NAN", ""):
            lat = _to_float(df.iat[i, col0 + 2])
            lon = _to_float(df.iat[i, col0 + 3])
            raw_cls, comp_pct, comp_idx = {}, {}, {}
            block_len = 8
            for r in range(block_len):
                row = i + r
                if row >= n_rows:
                    break
                cls_name = df.iat[row, col0 + 4] if col0 + 4 < n_cols else None
                cls_val = df.iat[row, col0 + 5] if col0 + 5 < n_cols else None
                if pd.notna(cls_name) and str(cls_name).strip():
                    raw_cls[str(cls_name).strip()] = _pct_to_frac(cls_val)

                ind_name = df.iat[row, col0 + 6] if col0 + 6 < n_cols else None
                ind_val = df.iat[row, col0 + 7] if col0 + 7 < n_cols else None
                if pd.notna(ind_name) and str(ind_name).strip():
                    comp_pct[str(ind_name).strip()] = _pct_to_frac(ind_val)

                comp_name = df.iat[row, col0 + 11] if col0 + 11 < n_cols else None
                comp_val = df.iat[row, col0 + 12] if col0 + 12 < n_cols else None
                if pd.notna(comp_name) and str(comp_name).strip():
                    clean = str(comp_name).replace(" Index", "").strip()
                    comp_idx[clean] = _to_float(comp_val)

            rec = {
                "corridor": corridor,
                "side": side_label,
                "kode": kode,
                "lat": lat,
                "lon": lon,
            }
            rec.update({f"raw::{k}": v for k, v in raw_cls.items()})
            rec.update({f"pct::{k}": v for k, v in comp_pct.items()})
            rec.update({f"uvi::{k}": v for k, v in comp_idx.items()})
            records.append(rec)
            i += block_len
        else:
            i += 1
    return pd.DataFrame(records)


def load_corridor(gid: str, corridor_name: str) -> pd.DataFrame:
    try:
        raw = fetch_raw_sheet(gid)
    except Exception:
        return pd.DataFrame()
    left = parse_block(raw, 0, "Sisi A", corridor_name)
    right = parse_block(raw, 13, "Sisi B", corridor_name)
    return pd.concat([left, right], ignore_index=True)


def demo_corridor(corridor_name: str, n: int = 8, seed: int = 0) -> pd.DataFrame:
    """Data sintetis fallback ketika Google Sheet belum publik / tak terjangkau."""
    rng = np.random.default_rng(abs(hash(corridor_name)) % (2**32) + seed)
    base_lat, base_lon = -7.9772, 112.6350
    rows = []
    for side in ["Sisi A", "Sisi B"]:
        for i in range(1, n + 1):
            comp_idx = {k: float(np.clip(rng.normal(0.35, 0.15), 0, 0.9)) for k in INDICATORS}
            raw_cls = {k: float(np.clip(rng.normal(15, 10), 0, 60)) for k in RAW_CLASSES}
            pct = {k: float(np.clip(rng.normal(15, 10), 0, 60)) for k in INDICATORS}
            rec = {
                "corridor": corridor_name,
                "side": side,
                "kode": f"{side[-1]}{i}",
                "lat": base_lat + rng.normal(0, 0.0006),
                "lon": base_lon + rng.normal(0, 0.0006) + (0.0008 if side == "Sisi B" else 0),
            }
            rec.update({f"raw::{k}": v for k, v in raw_cls.items()})
            rec.update({f"pct::{k}": v for k, v in pct.items()})
            rec.update({f"uvi::{k}": v for k, v in comp_idx.items()})
            rows.append(rec)
    return pd.DataFrame(rows)


def load_all_corridors():
    frames = []
    used_demo = []
    for gid, meta in CORRIDOR_SHEETS.items():
        name = meta["name"]
        d = load_corridor(gid, name)
        if d.empty:
            d = demo_corridor(name)
            used_demo.append(name)
        frames.append(d)
    all_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return all_df, used_demo


def compute_uvi(df: pd.DataFrame, weights: dict) -> pd.Series:
    cols = [f"uvi::{k}" for k in INDICATORS if f"uvi::{k}" in df.columns]
    if not cols:
        return pd.Series(np.nan, index=df.index)
    w = np.array([weights.get(c.split("::")[1], 1.0) for c in cols], dtype=float)
    w = w / w.sum() if w.sum() > 0 else np.ones_like(w) / len(w)
    vals = df[cols].apply(pd.to_numeric, errors="coerce").fillna(0).values
    score = (vals * w).sum(axis=1) * UVI_SCALE
    return pd.Series(score, index=df.index)
