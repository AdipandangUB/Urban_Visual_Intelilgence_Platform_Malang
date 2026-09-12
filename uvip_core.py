# -*- coding: utf-8 -*-
"""
uvip_core.py — logika parsing data & perhitungan UVI, dipisah dari app.py
agar dapat diuji tanpa dependensi Streamlit.
"""
import io
import re
import html
import base64
from bisect import bisect_left

import numpy as np
import pandas as pd
import requests

# openpyxl & Pillow dipakai khusus untuk mengekstrak FOTO yang ditempel
# langsung sebagai gambar di dalam sel Excel (bukan berupa teks URL). Import
# dibuat defensif: kalau salah satu paket tidak tersedia di server, fitur
# upload tetap berjalan tapi tanpa ekstraksi foto tertanam.
try:
    import openpyxl
    from PIL import Image as _PILImage
    _EMBEDDED_IMAGE_SUPPORT = True
except ImportError:
    _EMBEDDED_IMAGE_SUPPORT = False

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


def _extract_photo_url(val):
    """Kolom FOTO pada sheet biasanya berisi gambar lewat formula IMAGE()/
    smart-chip. Saat diekspor sebagai CSV, isinya bisa berupa:
      - URL langsung (http...)
      - formula mentah  =IMAGE("https://...")
      - markdown gambar ![](https://...)  (kadang muncul dari sumber lain)
    Fungsi ini mencoba mengekstrak URL gambar yang bisa dipakai di tag <img>,
    atau None kalau tidak ditemukan (popup akan menyembunyikan foto)."""
    if pd.isna(val):
        return None
    s = str(val).strip()
    if not s:
        return None
    m = re.search(r'https?://\S+', s)
    if not m:
        return None
    url = m.group(0).rstrip(')"\']')
    return url


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
            foto_raw = df.iat[i, col0 + 1] if col0 + 1 < n_cols else None
            foto_url = _extract_photo_url(foto_raw)
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
                "foto": foto_url,
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
                "foto": None,
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


# ----------------------------------------------------------------------------
# UPLOAD KORIDOR BARU — skema sederhana satu-baris-per-titik/node
# ----------------------------------------------------------------------------
# Kolom wajib : kode, corridor, lat, lon
# Kolom opsional : side, foto
# Kolom indikator visual komposit (nilai 0–1, dipakai utk skor UVI & radar) —
#   nama kolom harus SAMA PERSIS dengan salah satu nilai di INDICATORS.
# Kolom komposisi elemen visual mentah (nilai persen 0–100, opsional, dipakai
#   utk pie chart breakdown) — nama kolom harus SAMA PERSIS dengan RAW_CLASSES.

UPLOAD_REQUIRED_COLUMNS = ["kode", "corridor", "lat", "lon"]
UPLOAD_OPTIONAL_COLUMNS = ["side", "foto"] + INDICATORS + RAW_CLASSES


def build_upload_template() -> pd.DataFrame:
    """Buat satu baris contoh berisi semua kolom yang dikenali, untuk diunduh
    pengguna sebagai template pengisian file koridor baru."""
    cols = ["kode", "corridor", "side", "lat", "lon", "foto"] + INDICATORS + RAW_CLASSES
    example = {
        "kode": "KB 1",
        "corridor": "Nama Koridor Baru",
        "side": "Sisi A",
        "lat": -7.97710000,
        "lon": 112.63470000,
        "foto": "https://contoh.com/foto-titik-1.jpg",
    }
    for ind in INDICATORS:
        example[ind] = 0.30
    for cls in RAW_CLASSES:
        example[cls] = 15.0
    return pd.DataFrame([example], columns=cols)


def _encode_embedded_image(raw_bytes: bytes, max_width: int = 900, quality: int = 78):
    """Kompres & encode bytes gambar menjadi data URI base64 siap-pakai di
    tag <img>. Gambar di-resize agar popup peta tetap ringan (foto asli hasil
    jepretan HP biasanya beberapa MB, terlalu besar untuk ditanam langsung
    di HTML popup)."""
    try:
        im = _PILImage.open(io.BytesIO(raw_bytes))
        im = im.convert("RGB")
        if im.width > max_width:
            new_h = int(im.height * (max_width / im.width))
            im = im.resize((max_width, new_h), _PILImage.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return None


def _extract_embedded_photos_by_pandas_index(file_bytes: bytes, foto_col_name: str):
    """Cari gambar yang ditempel (di-*insert*) langsung di atas kolom FOTO
    pada file .xlsx yang diunggah, lalu petakan ke indeks baris pandas yang
    sesuai (indeks yang sama dipakai oleh `raw.iterrows()` di
    `parse_uploaded_corridor`).

    Ini diperlukan karena banyak pengguna mengisi kolom "foto" dengan cara
    men-*drag-drop* / Insert Picture gambar langsung ke sel Excel, bukan
    menempelkan URL. `pandas.read_excel` / openpyxl HANYA membaca *nilai*
    sel (teks/angka) dan sama sekali tidak melihat gambar yang melayang di
    atas sel — itulah sebabnya foto tidak pernah muncul di popup peta
    sebelumnya, walau gambarnya ada dan terlihat di Excel.

    Return: dict {pandas_row_index: data_uri}. Dict kosong kalau tidak ada
    gambar tertanam, openpyxl/Pillow tidak tersedia, atau terjadi error apa
    pun (fungsi ini tidak boleh menggagalkan proses upload).
    """
    if not _EMBEDDED_IMAGE_SUPPORT:
        return {}
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        ws = wb.active
        if not getattr(ws, "_images", None):
            return {}

        # Kolom header ada di baris pertama (row=1, 1-indexed di openpyxl).
        header_cells = next(ws.iter_rows(min_row=1, max_row=1))
        col_lookup = {
            str(c.value).strip().lower(): idx
            for idx, c in enumerate(header_cells) if c.value is not None
        }
        foto_col_idx = col_lookup.get(foto_col_name.strip().lower())
        kode_col_idx = col_lookup.get("kode")
        if foto_col_idx is None or kode_col_idx is None:
            return {}

        # Baris-baris (0-indexed, relatif ke sheet) yang benar-benar berisi
        # data (kolom "kode" terisi). Sel yang jadi bagian tengah/bawah dari
        # cell yang di-merge otomatis kosong di openpyxl, jadi ini otomatis
        # hanya menangkap baris "kepala" tiap titik/node.
        data_rows = [
            r for r in range(1, ws.max_row)
            if ws.cell(row=r + 1, column=kode_col_idx + 1).value not in (None, "")
        ]
        if not data_rows:
            return {}

        result = {}
        used_rows = set()
        for img in ws._images:
            anchor = getattr(img, "anchor", None)
            frm = getattr(anchor, "_from", None) if anchor is not None else None
            if frm is None or frm.col != foto_col_idx:
                continue  # gambar di kolom lain (mis. logo), abaikan

            # Gambar biasanya sedikit "naik" relatif ke baris datanya sendiri
            # (offset sub-baris saat insert), jadi dicocokkan ke baris data
            # TERDEKAT DI BAWAH ATAU SAMA DENGAN posisi gambar (ceiling-match)
            # — bukan sekadar baris persis tempat gambar berada.
            pos = bisect_left(data_rows, frm.row)
            if pos >= len(data_rows):
                pos = len(data_rows) - 1
            target_row = data_rows[pos]
            if target_row in used_rows:
                continue  # sudah ada gambar lain untuk baris ini, lewati
            used_rows.add(target_row)

            try:
                raw_bytes = img._data()
            except Exception:
                continue
            data_uri = _encode_embedded_image(raw_bytes)
            if not data_uri:
                continue

            pandas_index = target_row - 1  # baris 0 = header, jadi -1
            result[pandas_index] = data_uri
        return result
    except Exception:
        # File korup / format tak terduga -> upload tetap lanjut tanpa foto
        # tertanam, kolom foto teks (kalau ada) tetap dipakai seperti biasa.
        return {}


def parse_uploaded_corridor(file_obj, filename: str):
    """Parse file upload (.csv/.xlsx/.xls) skema sederhana satu-baris-per-titik
    menjadi DataFrame dengan skema yang sama seperti keluaran load_corridor()
    (corridor, side, kode, lat, lon, foto, uvi::<indikator>, raw::<kelas>).

    Return: (df, messages) — jika df kosong, `messages` berisi error fatal;
    jika df tidak kosong, `messages` berisi peringatan non-fatal (bisa []).
    """
    name_lower = (filename or "").lower()
    is_excel = name_lower.endswith((".xlsx", ".xls"))

    # Simpan bytes mentah dulu (sekali baca) supaya file yang sama bisa
    # dibaca dua kali: sekali oleh pandas (nilai sel/teks), sekali lagi oleh
    # openpyxl khusus untuk menemukan gambar yang ditempel di kolom "foto".
    file_bytes = None
    if is_excel:
        try:
            file_bytes = file_obj.read()
            file_obj_for_pandas = io.BytesIO(file_bytes)
        except Exception:
            file_obj_for_pandas = file_obj
    else:
        file_obj_for_pandas = file_obj

    try:
        if is_excel:
            raw = pd.read_excel(file_obj_for_pandas, dtype=str)
        else:
            raw = pd.read_csv(file_obj_for_pandas, dtype=str)
    except Exception as e:
        return pd.DataFrame(), [f"Gagal membaca file: {e}"]

    if raw.empty:
        return pd.DataFrame(), ["File kosong / tidak berisi baris data."]

    raw.columns = [str(c).strip() for c in raw.columns]
    col_map = {c.lower(): c for c in raw.columns}

    def get_col(name):
        return col_map.get(name.lower())

    missing = [c for c in UPLOAD_REQUIRED_COLUMNS if get_col(c) is None]
    if missing:
        return pd.DataFrame(), [
            "Kolom wajib tidak ditemukan pada file: " + ", ".join(missing) +
            ". Unduh template untuk melihat nama kolom yang benar."
        ]

    # Foto yang ditempel sebagai gambar (bukan teks URL) langsung di kolom
    # "foto" pada file .xlsx — dipetakan ke indeks baris pandas di atas.
    embedded_photos = {}
    warnings = []
    foto_col_name = get_col("foto")
    if is_excel and file_bytes and foto_col_name is not None:
        if _EMBEDDED_IMAGE_SUPPORT:
            embedded_photos = _extract_embedded_photos_by_pandas_index(
                file_bytes, foto_col_name
            )
        else:
            warnings.append(
                "Server belum punya paket `openpyxl`/`Pillow` lengkap, jadi "
                "foto yang ditempel langsung di sel Excel (bukan URL) tidak "
                "bisa diekstrak."
            )

    records = []
    for i, row in raw.iterrows():
        kode_val, corridor_val = row[get_col("kode")], row[get_col("corridor")]
        kode = str(kode_val).strip() if pd.notna(kode_val) else ""
        corridor = str(corridor_val).strip() if pd.notna(corridor_val) else ""
        if not kode or not corridor:
            continue  # baris kosong/pemisah, lewati diam-diam

        lat = _to_float(row[get_col("lat")])
        lon = _to_float(row[get_col("lon")])
        if pd.isna(lat) or pd.isna(lon):
            warnings.append(f"Baris {i + 2}: lat/lon tidak valid, baris dilewati.")
            continue

        side_col = get_col("side")
        side = str(row[side_col]).strip() if side_col and pd.notna(row[side_col]) else "Sisi A"

        foto_col = get_col("foto")
        foto = _extract_photo_url(row[foto_col]) if foto_col is not None else None
        if not foto and i in embedded_photos:
            # Tidak ada URL teks di kolom foto, tapi ada gambar yang
            # ditempel langsung di sel -> pakai itu.
            foto = embedded_photos[i]

        rec = {
            "corridor": corridor, "side": side, "kode": kode,
            "lat": lat, "lon": lon,
            # "" (bukan None) supaya pandas tidak mengubahnya jadi NaN saat
            # DataFrame dibentuk dari records — row["foto"] jadi string
            # literal "nan" kalau None, dan itu dianggap "ada foto" oleh
            # build_node_popup_html (src="nan" -> ikon gambar rusak).
            "foto": foto or "",
        }
        for ind in INDICATORS:
            c = get_col(ind)
            if c is not None:
                v = _to_float(row[c])
                if not pd.isna(v):
                    rec[f"uvi::{ind}"] = v
        for cls in RAW_CLASSES:
            c = get_col(cls)
            if c is not None:
                v = _to_float(row[c])
                if not pd.isna(v):
                    rec[f"raw::{cls}"] = v
        records.append(rec)

    if not records:
        return pd.DataFrame(), ["Tidak ada baris data valid ditemukan pada file."] + warnings

    if embedded_photos:
        n_used = sum(1 for r in records if r.get("foto") in embedded_photos.values())
        warnings.append(
            f"📷 {n_used} foto yang ditempel langsung di sel Excel berhasil "
            "diekstrak dan dicocokkan ke baris datanya."
        )

    df = pd.DataFrame(records)
    if not any(col.startswith("uvi::") for col in df.columns):
        warnings.append(
            "Tidak ada kolom indikator visual (mis. 'Building Visibility') yang "
            "cocok pada file — skor UVI tidak dapat dihitung untuk data ini."
        )
    return df, warnings


def build_node_popup_html(row, header_color: str) -> str:
    """Bangun kartu popup lengkap untuk satu titik/node — menampilkan SEMUA
    keterangan dari sheet sumber (foto, koordinat, skor UVI, 8 indikator
    visual komposit, dan komposisi elemen visual hasil deteksi AI), mengacu
    pada gaya kartu pada referensi (foto + tabel keterangan)."""
    kode = html.escape(str(row.get("kode", "")))
    corridor = html.escape(str(row.get("corridor", "")))
    side = html.escape(str(row.get("side", "")))
    lat, lon = row.get("lat"), row.get("lon")
    uvi = row.get("UVI")

    foto = row.get("foto")
    if isinstance(foto, str) and foto.strip().lower().startswith("http"):
        img_html = (
            f'<img src="{html.escape(foto.strip())}" '
            'style="width:100%;max-height:150px;object-fit:cover;'
            'border-radius:6px;margin-top:6px;display:block;" '
            'onerror="this.style.display=\'none\'">'
        )
    else:
        img_html = ""

    ind_rows = ""
    for ind in INDICATORS:
        v = row.get(f"uvi::{ind}")
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        pct_width = max(0.0, min(1.0, float(v))) * 100
        ind_rows += (
            "<tr>"
            f'<td style="padding:2px 4px;color:#444;white-space:nowrap;">{html.escape(ind)}</td>'
            '<td style="padding:2px 4px;width:70px;">'
            f'<div style="background:#eee;border-radius:3px;height:6px;overflow:hidden;">'
            f'<div style="background:{header_color};width:{pct_width:.0f}%;height:100%;"></div></div>'
            "</td>"
            f'<td style="padding:2px 4px;text-align:right;font-weight:600;">{float(v):.3f}</td>'
            "</tr>"
        )

    raw_rows = ""
    for cls in RAW_CLASSES:
        v = row.get(f"raw::{cls}")
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        raw_rows += (
            "<tr>"
            f'<td style="padding:2px 4px;color:#444;">{html.escape(cls)}</td>'
            f'<td style="padding:2px 4px;text-align:right;">{float(v):.2f}%</td>'
            "</tr>"
        )

    uvi_txt = f"{uvi:.2f} / 10" if uvi is not None and not pd.isna(uvi) else "—"
    lat_txt = f"{lat:.6f}" if lat is not None and not pd.isna(lat) else "—"
    lon_txt = f"{lon:.6f}" if lon is not None and not pd.isna(lon) else "—"

    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif;width:270px;
                max-height:380px;overflow-y:auto;">
      <div style="background:{header_color};color:#fff;padding:8px 10px;
                  border-radius:6px 6px 0 0;font-weight:700;font-size:13px;">
        📷 {kode} — {corridor} ({side})
      </div>
      {img_html}
      <table style="width:100%;font-size:11.5px;border-collapse:collapse;margin-top:6px;">
        <tr><td style="padding:2px 4px;color:#666;">Koordinat</td>
            <td style="padding:2px 4px;text-align:right;">{lat_txt}, {lon_txt}</td></tr>
        <tr><td style="padding:2px 4px;color:#666;">Skor UVI</td>
            <td style="padding:2px 4px;text-align:right;font-weight:700;color:{header_color};">{uvi_txt}</td></tr>
      </table>
      <div style="font-weight:700;font-size:11.5px;margin-top:6px;
                  border-top:1px solid #ddd;padding-top:4px;">
        Indikator Visual Komposit
      </div>
      <table style="width:100%;font-size:11px;border-collapse:collapse;">
        {ind_rows}
      </table>
      <div style="font-weight:700;font-size:11.5px;margin-top:6px;
                  border-top:1px solid #ddd;padding-top:4px;">
        Komposisi Elemen Visual (deteksi AI)
      </div>
      <table style="width:100%;font-size:11px;border-collapse:collapse;">
        {raw_rows}
      </table>
    </div>
    """
