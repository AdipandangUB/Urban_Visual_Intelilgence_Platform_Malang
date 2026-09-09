"""
uvip_core.py — logika parsing data & perhitungan UVI, dipisah dari app.py
agar dapat diuji tanpa dependensi Streamlit.
"""
import io
import re
import html
import numpy as np
import pandas as pd
import requests

SHEET_ID = "1XrgdsW7IVULMQ3LIiEocPAbuFUGBkAnaPBYO0N2gxMA"

KORIDOR_SHEETS = {
    "1062597004": {"name": "Tugu"},
    "446487299":  {"name": "Kayutangan–Tugu"},
    "891123454":  {"name": "Lafayette–PLN"},
    "1869212551": {"name": "Kayutangan 1"},
    "385636298":  {"name": "Alun-Alun Merdeka"},
}

KELAS_MENTAH = [
    "Tanah", "Bangunan", "Rambu Lalu Lintas", "Vegetasi",
    "Langit", "Manusia", "Kendaraan 4R", "Kendaraan 2R",
]

INDIKATOR = [
    "Visibilitas Bangunan", "Cakupan Vegetasi", "Keterbukaan Langit",
    "Aksesibilitas Lahan", "Aktivitas Manusia", "Intensitas Kendaraan",
    "Infrastruktur Lalu Lintas", "Dominasi Warisan Budaya",
]

BOBOT_DEFAULT = {k: 1.0 for k in INDIKATOR}
SKALA_UVI = 10


def _ke_angka(val):
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


def _persen_ke_pecahan(val):
    return _ke_angka(val)


def _ekstrak_url_foto(val):
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


def url_csv_sheet(gid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"


def ambil_sheet_mentah(gid: str) -> pd.DataFrame:
    """Ambil data mentah 1 tab spreadsheet sebagai DataFrame tanpa header."""
    url = url_csv_sheet(gid)
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return pd.read_csv(io.StringIO(resp.text), header=None, dtype=str)


def parsing_blok(df: pd.DataFrame, kolom0: int, label_sisi: str, koridor: str) -> pd.DataFrame:
    """Parser generik untuk 1 sisi (kiri/kanan) dari layout paired-block khas
    lembar 'HASIL ANALISIS' hasil deteksi AI. Lihat README untuk skema kolom.
    """
    catatan = []
    n_baris, n_kolom = df.shape
    if kolom0 + 12 >= n_kolom:
        return pd.DataFrame()

    i = 0
    while i < n_baris:
        kode_mentah = df.iat[i, kolom0 + 0] if kolom0 + 0 < n_kolom else None
        kode = None if pd.isna(kode_mentah) else str(kode_mentah).strip()
        if kode and kode.upper() not in ("KODE", "NAN", ""):
            lat = _ke_angka(df.iat[i, kolom0 + 2])
            lon = _ke_angka(df.iat[i, kolom0 + 3])
            foto_mentah = df.iat[i, kolom0 + 1] if kolom0 + 1 < n_kolom else None
            foto_url = _ekstrak_url_foto(foto_mentah)
            kelas_mentah, komp_persen, komp_indeks = {}, {}, {}
            panjang_blok = 8
            for r in range(panjang_blok):
                baris = i + r
                if baris >= n_baris:
                    break
                nama_kelas = df.iat[baris, kolom0 + 4] if kolom0 + 4 < n_kolom else None
                nilai_kelas = df.iat[baris, kolom0 + 5] if kolom0 + 5 < n_kolom else None
                if pd.notna(nama_kelas) and str(nama_kelas).strip():
                    kelas_mentah[str(nama_kelas).strip()] = _persen_ke_pecahan(nilai_kelas)

                nama_ind = df.iat[baris, kolom0 + 6] if kolom0 + 6 < n_kolom else None
                nilai_ind = df.iat[baris, kolom0 + 7] if kolom0 + 7 < n_kolom else None
                if pd.notna(nama_ind) and str(nama_ind).strip():
                    komp_persen[str(nama_ind).strip()] = _persen_ke_pecahan(nilai_ind)

                nama_komp = df.iat[baris, kolom0 + 11] if kolom0 + 11 < n_kolom else None
                nilai_komp = df.iat[baris, kolom0 + 12] if kolom0 + 12 < n_kolom else None
                if pd.notna(nama_komp) and str(nama_komp).strip():
                    bersih = str(nama_komp).replace(" Index", "").strip()
                    komp_indeks[bersih] = _ke_angka(nilai_komp)

            rek = {
                "koridor": koridor,
                "sisi": label_sisi,
                "kode": kode,
                "lat": lat,
                "lon": lon,
                "foto": foto_url,
            }
            rek.update({f"mentah::{k}": v for k, v in kelas_mentah.items()})
            rek.update({f"persen::{k}": v for k, v in komp_persen.items()})
            rek.update({f"uvi::{k}": v for k, v in komp_indeks.items()})
            catatan.append(rek)
            i += panjang_blok
        else:
            i += 1
    return pd.DataFrame(catatan)


def muat_koridor(gid: str, nama_koridor: str) -> pd.DataFrame:
    try:
        mentah = ambil_sheet_mentah(gid)
    except Exception:
        return pd.DataFrame()
    kiri = parsing_blok(mentah, 0, "Sisi A", nama_koridor)
    kanan = parsing_blok(mentah, 13, "Sisi B", nama_koridor)
    return pd.concat([kiri, kanan], ignore_index=True)


def koridor_demo(nama_koridor: str, n: int = 8, seed: int = 0) -> pd.DataFrame:
    """Data sintetis fallback ketika Google Sheet belum publik / tak terjangkau."""
    rng = np.random.default_rng(abs(hash(nama_koridor)) % (2**32) + seed)
    lat_dasar, lon_dasar = -7.9772, 112.6350
    baris = []
    for sisi in ["Sisi A", "Sisi B"]:
        for i in range(1, n + 1):
            komp_indeks = {k: float(np.clip(rng.normal(0.35, 0.15), 0, 0.9)) for k in INDIKATOR}
            kelas_mentah = {k: float(np.clip(rng.normal(15, 10), 0, 60)) for k in KELAS_MENTAH}
            persen = {k: float(np.clip(rng.normal(15, 10), 0, 60)) for k in INDIKATOR}
            rek = {
                "koridor": nama_koridor,
                "sisi": sisi,
                "kode": f"{sisi[-1]}{i}",
                "lat": lat_dasar + rng.normal(0, 0.0006),
                "lon": lon_dasar + rng.normal(0, 0.0006) + (0.0008 if sisi == "Sisi B" else 0),
                "foto": None,
            }
            rek.update({f"mentah::{k}": v for k, v in kelas_mentah.items()})
            rek.update({f"persen::{k}": v for k, v in persen.items()})
            rek.update({f"uvi::{k}": v for k, v in komp_indeks.items()})
            baris.append(rek)
    return pd.DataFrame(baris)


def muat_semua_koridor():
    bingkai = []
    demo_digunakan = []
    for gid, meta in KORIDOR_SHEETS.items():
        nama = meta["name"]
        d = muat_koridor(gid, nama)
        if d.empty:
            d = koridor_demo(nama)
            demo_digunakan.append(nama)
        bingkai.append(d)
    semua_df = pd.concat(bingkai, ignore_index=True) if bingkai else pd.DataFrame()
    return semua_df, demo_digunakan


def hitung_uvi(df: pd.DataFrame, bobot: dict) -> pd.Series:
    kolom = [f"uvi::{k}" for k in INDIKATOR if f"uvi::{k}" in df.columns]
    if not kolom:
        return pd.Series(np.nan, index=df.index)
    w = np.array([bobot.get(c.split("::")[1], 1.0) for c in kolom], dtype=float)
    w = w / w.sum() if w.sum() > 0 else np.ones_like(w) / len(w)
    nilai = df[kolom].apply(pd.to_numeric, errors="coerce").fillna(0).values
    skor = (nilai * w).sum(axis=1) * SKALA_UVI
    return pd.Series(skor, index=df.index)


def bangun_html_popup_titik(baris, warna_header: str) -> str:
    """Bangun kartu popup lengkap untuk satu titik/node — menampilkan SEMUA
    keterangan dari sheet sumber (foto, koordinat, skor UVI, 8 indikator
    visual komposit, dan komposisi elemen visual hasil deteksi AI), mengacu
    pada gaya kartu pada referensi (foto + tabel keterangan)."""
    kode = html.escape(str(baris.get("kode", "")))
    koridor = html.escape(str(baris.get("koridor", "")))
    sisi = html.escape(str(baris.get("sisi", "")))
    lat, lon = baris.get("lat"), baris.get("lon")
    uvi = baris.get("UVI")

    foto = baris.get("foto")
    if isinstance(foto, str) and foto.strip().lower().startswith("http"):
        img_html = (
            f'<img src="{html.escape(foto.strip())}" '
            'style="width:100%;max-height:150px;object-fit:cover;'
            'border-radius:6px;margin-top:6px;display:block;" '
            'onerror="this.style.display=\'none\'">'
        )
    else:
        img_html = ""

    baris_ind = ""
    for ind in INDIKATOR:
        v = baris.get(f"uvi::{ind}")
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        lebar_persen = max(0.0, min(1.0, float(v))) * 100
        baris_ind += (
            "<tr>"
            f'<td style="padding:2px 4px;color:#444;white-space:nowrap;">{html.escape(ind)}</td>'
            '<td style="padding:2px 4px;width:70px;">'
            f'<div style="background:#eee;border-radius:3px;height:6px;overflow:hidden;">'
            f'<div style="background:{warna_header};width:{lebar_persen:.0f}%;height:100%;"></div></div>'
            "</td>"
            f'<td style="padding:2px 4px;text-align:right;font-weight:600;">{float(v):.3f}</td>'
            "</tr>"
        )

    baris_mentah = ""
    for cls in KELAS_MENTAH:
        v = baris.get(f"mentah::{cls}")
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        baris_mentah += (
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
      <div style="background:{warna_header};color:#fff;padding:8px 10px;
                  border-radius:6px 6px 0 0;font-weight:700;font-size:13px;">
        📷 {kode} — {koridor} ({sisi})
      </div>
      {img_html}
      <table style="width:100%;font-size:11.5px;border-collapse:collapse;margin-top:6px;">
        <tr><td style="padding:2px 4px;color:#666;">Koordinat</td>
            <td style="padding:2px 4px;text-align:right;">{lat_txt}, {lon_txt}</td></tr>
        <tr><td style="padding:2px 4px;color:#666;">Skor UVI</td>
            <td style="padding:2px 4px;text-align:right;font-weight:700;color:{warna_header};">{uvi_txt}</td></tr>
      </table>
      <div style="font-weight:700;font-size:11.5px;margin-top:6px;
                  border-top:1px solid #ddd;padding-top:4px;">
        Indikator Visual Komposit
      </div>
      <table style="width:100%;font-size:11px;border-collapse:collapse;">
        {baris_ind}
      </table>
      <div style="font-weight:700;font-size:11.5px;margin-top:6px;
                  border-top:1px solid #ddd;padding-top:4px;">
        Komposisi Elemen Visual (deteksi AI)
      </div>
      <table style="width:100%;font-size:11px;border-collapse:collapse;">
        {baris_mentah}
      </table>
    </div>
    """

# Untuk kompatibilitas mundur dengan app.py yang masih menggunakan nama variabel Inggris
# (agar tidak perlu mengubah app.py secara drastis)
CORRIDOR_SHEETS = KORIDOR_SHEETS
RAW_CLASSES = KELAS_MENTAH
INDICATORS = INDIKATOR
DEFAULT_WEIGHTS = BOBOT_DEFAULT
UVI_SCALE = SKALA_UVI
sheet_csv_url = url_csv_sheet
fetch_raw_sheet = ambil_sheet_mentah
parse_block = parsing_blok
load_corridor = muat_koridor
demo_corridor = koridor_demo
load_all_corridors = muat_semua_koridor
compute_uvi = hitung_uvi
build_node_popup_html = bangun_html_popup_titik