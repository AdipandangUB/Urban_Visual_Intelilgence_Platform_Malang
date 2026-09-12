"""
UVIP Malang — Urban Visual Intelligence Platform
=================================================
Model Sistem Simulasi Visual Digital Ruang Terbuka Perkotaan Berbasis AI
untuk Smart City Kota Malang

Dikembangkan mengacu pada Penelitian Terapan:
"Pengembangan Model Sistem Simulasi Visual Digital Ruang Terbuka Perkotaan
Berbasis AI untuk Smart City Kota Malang" dengan Peneliti Dr. Herry Santosa,Dr Adipandang Yudono,Dr Herman Tolle,Prof. Jenny Ernawati,
Dr. Agung Setia Budi - Universitas Brawijaya.

Menjalankan aplikasi:
    streamlit run app.py
"""

from datetime import datetime
import os
import io
import base64
import html
import re
from urllib.parse import quote

import requests
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium
from PIL import Image

# ReportLab digunakan untuk menghasilkan policy brief PDF secara langsung dari
# hasil analisis UVI. Import dibuat defensif agar aplikasi tetap dapat berjalan
# dan menampilkan pesan yang jelas apabila paket belum tersedia di server.
try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, KeepTogether
    )
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# python-docx digunakan untuk menghasilkan versi editable Policy Brief dalam
# format Word (.docx). Import dibuat defensif dengan pola yang sama seperti
# ReportLab di atas.
try:
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

from uvip_core import (
    CORRIDOR_SHEETS, RAW_CLASSES, INDICATORS, DEFAULT_WEIGHTS, UVI_SCALE,
    sheet_csv_url, load_corridor, demo_corridor, compute_uvi,
)

# Fungsi-fungsi di bawah ini diimpor secara DEFENSIF.
try:
    from uvip_core import build_upload_template, parse_uploaded_corridor
    UPLOAD_FEATURE_AVAILABLE = True
except ImportError:
    UPLOAD_FEATURE_AVAILABLE = False

    def build_upload_template():
        import pandas as _pd
        return _pd.DataFrame()

    def parse_uploaded_corridor(file_obj, filename: str):
        import pandas as _pd
        return _pd.DataFrame(), [
            "Fitur upload koridor baru belum aktif: uvip_core.py di server "
            "belum berisi fungsi ini. Pastikan uvip_core.py versi terbaru "
            "sudah ikut di-push/redeploy bersama app.py."
        ]

# -----------------------------------------------------------------------------
# Popup node + sumber foto dari repository GitHub
# -----------------------------------------------------------------------------

GITHUB_OWNER = "AdipandangUB"
GITHUB_REPO = "Urban_Visual_Intelilgence_Platform_Malang"
GITHUB_BRANCH = "main"

# Ekstensi gambar yang dianggap valid saat membaca isi folder GitHub.
_GITHUB_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

PHOTO_SOURCES = {
    "385636298": {
        "name": "Alun-alun Merdeka",
        "folder": "images/Alun-Alun Merdeka",
    },
    "1869212551": {
        "name": "Kayutangan 1",
        "folder": "images/Kayutangan 1",
    },
    "891123454": {
        "name": "Lavalette-PLN",
        "folder": "images/Lavayette - PLN",
    },
    "446487299": {
        "name": "Kayutangan-Tugu",
        "folder": "images/Kayutangan - Tugu",
    },
    "1062597004": {
        "name": "Tugu",
        "folder": "images/Tugu",
    },
}


def _github_folder_browse_url(folder: str) -> str:
    """URL github.com (bukan raw) untuk membuka folder foto koridor di browser."""
    if not folder:
        return ""
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/tree/{GITHUB_BRANCH}/{quote(folder)}"


@st.cache_data(ttl=3600, show_spinner=False)
def _github_folder_files(folder: str):
    """Ambil daftar file gambar dalam sebuah folder repo GitHub.

    Mengembalikan dict {kode_dinormalisasi: raw_image_url}, di mana kode
    diambil dari nama file tanpa ekstensi (mis. "TBS 1.jpg" -> "TBS1").
    Memakai GitHub Contents API sehingga tidak perlu tahu ekstensi file
    di muka (.jpg/.jpeg/.png/.webp semua dicoba).
    """
    if not folder:
        return {}

    api_url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/contents/{quote(folder)}?ref={GITHUB_BRANCH}"
    )
    lookup = {}
    try:
        resp = requests.get(
            api_url, timeout=10,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if resp.status_code == 200:
            for item in resp.json():
                if item.get("type") != "file":
                    continue
                name = str(item.get("name", ""))
                stem, ext = os.path.splitext(name)
                if ext.lower() not in _GITHUB_IMAGE_EXTS:
                    continue
                download_url = item.get("download_url")
                if not download_url:
                    continue
                key = _normalize_key(stem)
                if key:
                    lookup[key] = download_url
    except Exception:
        # Jaringan bermasalah / rate limit GitHub API -> popup akan tampil
        # tanpa foto, tidak menghentikan aplikasi.
        return {}
    return lookup


def _find_key_column(columns):
    """Cari kolom kunci yang bisa dipakai untuk mencocokkan baris foto ke baris data.

    Prioritas: kolom yang mengandung 'kode', 'node', 'id', 'nomor', 'no'.
    """
    candidates = {
        'kode', 'code', 'id', 'node', 'nodeid', 'nodid', 'nomor',
        'no', 'nourut', 'nomorurut', 'urutan', 'pointid', 'point',
        'kodeunik', 'kodetitik', 'nodecode', 'node_id', 'titik', 'titikid'
    }
    for col in columns:
        norm = re.sub(r'[^a-z0-9]', '', str(col).lower())
        if norm in candidates or norm.endswith('kode') or norm.endswith('code'):
            return col
    return None


def _normalize_key(v):
    """Normalisasi nilai kunci agar pencocokan lebih toleran."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return re.sub(r'\s+', '', str(v).strip().upper())


def _attach_photo_urls(data, gid):
    """Tambahkan kolom foto dengan mencocokkan kode node ke file di GitHub.

    Setiap koridor (gid) dipetakan ke satu folder foto di repo GitHub lewat
    PHOTO_SOURCES[gid]["folder"]. Isi folder itu dibaca sekali (dan
    di-cache), lalu setiap baris dicocokkan berdasarkan kolom kode/node
    (mis. "kode") terhadap nama file (tanpa ekstensi), sama-sama
    dinormalisasi dengan _normalize_key agar toleran terhadap spasi/huruf
    besar-kecil.
    """
    if data is None or data.empty:
        return data

    out = data.copy()
    out.columns = [str(c).strip() for c in out.columns]

    meta = PHOTO_SOURCES.get(str(gid), {})
    folder = meta.get("folder", "")

    if 'foto' not in out.columns:
        out['foto'] = ''
    if 'foto_source' not in out.columns:
        out['foto_source'] = _github_folder_browse_url(folder)

    if not folder:
        return out

    lookup = _github_folder_files(folder)
    if not lookup:
        return out

    key_col = _find_key_column(out.columns)
    if key_col is None:
        return out

    def match_by_kode(r):
        current = str(r.get('foto', '')).strip()
        if current and current.lower() not in {'nan', 'none', 'null'}:
            return current
        val = r.get(key_col, '')
        return lookup.get(_normalize_key(val), '')

    out['foto'] = out.apply(match_by_kode, axis=1)
    return out


def _source_url_for_row(row):
    source = row.get('foto_source', '')
    if isinstance(source, str) and source.startswith('http'):
        return source
    return ''


def build_node_popup_html(row, header_color: str) -> str:
    """Bangun HTML popup untuk node peta, termasuk foto jika tersedia."""
    kode = html.escape(str(row.get('kode', '')))
    corridor = html.escape(str(row.get('corridor', '')))
    side = html.escape(str(row.get('side', '')))
    lat, lon = row.get('lat'), row.get('lon')
    uvi = row.get('UVI')

    foto = str(row.get('foto', '')).strip()
    source_url = _source_url_for_row(row)

    if foto:
        img_html = (
            f'<a href="{html.escape(foto)}" target="_blank" rel="noopener noreferrer">'
            f'<img src="{html.escape(foto)}" alt="Foto {kode}" '
            'style="width:100%;max-height:200px;object-fit:cover;'
            'border-radius:8px;margin:8px 0 4px 0;display:block;" '
            'onerror="this.onerror=null;this.src=\'https://via.placeholder.com/280x160/e0e0e0/666666?text=Foto+tidak+dapat+dimuat\';" />'
            '</a>'
        )
    elif source_url:
        img_html = (
            '<div style="background:#f3f6f3;border:1px dashed #8aa58d;'
            'border-radius:7px;padding:10px;margin:8px 0;color:#555;font-size:11px;text-align:center;">'
            f'📷 Foto untuk node {kode} belum ditemukan di repository'
            '</div>'
        )
    else:
        img_html = ''

    ind_rows = ''
    for ind in INDICATORS:
        v = row.get(f'uvi::{ind}')
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        pct_width = max(0.0, min(1.0, float(v))) * 100
        ind_rows += (
            '<tr>'
            f'<td style="padding:2px 4px;color:#444;white-space:nowrap;">{html.escape(ind)}</td>'
            '<td style="padding:2px 4px;width:70px;">'
            '<div style="background:#eee;border-radius:3px;height:6px;overflow:hidden;">'
            f'<div style="background:{header_color};width:{pct_width:.0f}%;height:100%;"></div></div>'
            '</td>'
            f'<td style="padding:2px 4px;text-align:right;font-weight:600;">{float(v):.3f}</td>'
            '</tr>'
        )

    raw_rows = ''
    for cls in RAW_CLASSES:
        v = row.get(f'raw::{cls}')
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        raw_rows += (
            '<tr>'
            f'<td style="padding:2px 4px;color:#444;">{html.escape(cls)}</td>'
            f'<td style="padding:2px 4px;text-align:right;">{float(v):.2f}%</td>'
            '</tr>'
        )

    uvi_txt = f'{uvi:.2f} / 10' if uvi is not None and not pd.isna(uvi) else '—'
    lat_txt = f'{lat:.6f}' if lat is not None and not pd.isna(lat) else '—'
    lon_txt = f'{lon:.6f}' if lon is not None and not pd.isna(lon) else '—'

    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif;width:285px;max-height:470px;overflow-y:auto;">
      <div style="background:{header_color};color:#fff;padding:9px 11px;
                  border-radius:7px 7px 0 0;font-weight:700;font-size:13px;">
        📷 {kode} — {corridor} ({side})
      </div>
      {img_html}
      <table style="width:100%;font-size:11.5px;border-collapse:collapse;margin-top:5px;">
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


# ----------------------------------------------------------------------------
# 0. KONFIGURASI DASAR
# ----------------------------------------------------------------------------

st.set_page_config(
    page_title="UVIP Malang — Urban Visual Intelligence Platform",
    page_icon="🏙️",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ----------------------------------------------------------------------------
# 0.1 FUNGSI UNTUK BACKGROUND IMAGE
# ----------------------------------------------------------------------------

def get_base64_of_bin_file(bin_file):
    """Mengkonversi file gambar ke base64 untuk CSS background"""
    try:
        with open(bin_file, 'rb') as f:
            data = f.read()
        return base64.b64encode(data).decode()
    except Exception:
        return None


def set_sidebar_background(image_path):
    """Set background untuk sidebar dengan gambar"""
    if os.path.exists(image_path):
        try:
            img_base64 = get_base64_of_bin_file(image_path)
            if img_base64:
                ext = os.path.splitext(image_path)[1].lower()
                if ext in ['.png']:
                    mime_type = 'image/png'
                elif ext in ['.jpg', '.jpeg']:
                    mime_type = 'image/jpeg'
                else:
                    mime_type = 'image/jpeg'

                st.markdown(
                    f"""
                    <style>
                    [data-testid="stSidebar"] {{
                        background-image: url("data:{mime_type};base64,{img_base64}") !important;
                        background-size: cover !important;
                        background-position: center !important;
                        background-repeat: no-repeat !important;
                    }}
                    [data-testid="stSidebar"]::before {{
                        content: "";
                        position: absolute;
                        top: 0;
                        left: 0;
                        width: 100%;
                        height: 100%;
                        background-color: rgba(0, 0, 0, 0.65);
                        z-index: 0;
                    }}
                    [data-testid="stSidebar"] > * {{
                        position: relative;
                        z-index: 1;
                    }}
                    [data-testid="stSidebar"] .sidebar-content {{
                        background-color: transparent !important;
                    }}
                    </style>
                    """,
                    unsafe_allow_html=True
                )
                return True
        except Exception:
            return False
    return False


def set_header_background(image_path):
    """Set background untuk header dengan gambar"""
    if os.path.exists(image_path):
        try:
            img_base64 = get_base64_of_bin_file(image_path)
            if img_base64:
                ext = os.path.splitext(image_path)[1].lower()
                if ext in ['.png']:
                    mime_type = 'image/png'
                elif ext in ['.jpg', '.jpeg']:
                    mime_type = 'image/jpeg'
                else:
                    mime_type = 'image/jpeg'

                st.markdown(
                    f"""
                    <style>
                    .header-with-bg {{
                        background-image: linear-gradient(rgba(0, 0, 0, 0.5), rgba(0, 0, 0, 0.5)), 
                                          url("data:{mime_type};base64,{img_base64}") !important;
                        background-size: cover !important;
                        background-position: center !important;
                        padding: 2rem 2rem;
                        border-radius: 14px;
                        color: white;
                        margin-bottom: 1rem;
                        min-height: 150px;
                        display: flex;
                        flex-direction: column;
                        justify-content: center;
                    }}
                    .header-with-bg h2 {{
                        margin: 0;
                        font-size: 2.2rem;
                        text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
                    }}
                    .header-with-bg p {{
                        margin: 0.5rem 0 0 0;
                        opacity: 0.95;
                        font-size: 1.1rem;
                        text-shadow: 1px 1px 3px rgba(0,0,0,0.5);
                    }}
                    </style>
                    """,
                    unsafe_allow_html=True
                )
                return True
        except Exception:
            return False
    return False


# ----------------------------------------------------------------------------
# 0.2 SET BACKGROUND
# ----------------------------------------------------------------------------

sidebar_bg_paths = [
    "images/Digital twin.png",
    "images/digital twin.png",
    "images/Digital_twin.png",
    "images/digital_twin.png",
    "Digital twin.png",
    "Digital_twin.png"
]

for path in sidebar_bg_paths:
    if os.path.exists(path):
        set_sidebar_background(path)
        break

header_bg_paths = [
    "images/Digital_twin_Kota.jpg",
    "images/Digital_twin_Kota.jpeg",
    "images/Digital twin Kota.jpg",
    "images/digital_twin_kota.jpg"
]

header_bg_set = False
for path in header_bg_paths:
    if os.path.exists(path):
        header_bg_set = set_header_background(path)
        if header_bg_set:
            break

_load_corridor_cached = st.cache_data(ttl=3600, show_spinner=False)(load_corridor)


def _sheet_csv_url(gid: str) -> str:
    return sheet_csv_url(gid)


@st.cache_data(ttl=3600, show_spinner=False)
def load_all_corridors():
    frames = []
    used_demo = []
    for gid, meta in CORRIDOR_SHEETS.items():
        gid = str(gid)
        name = meta["name"]
        d = _load_corridor_cached(gid, name)
        if d.empty:
            d = demo_corridor(name)
            used_demo.append(name)
        else:
            # Cocokkan foto dari folder GitHub koridor ini.
            d = _attach_photo_urls(d, gid)

        # Pastikan setiap baris membawa sumber foto koridornya.
        folder = PHOTO_SOURCES.get(gid, {}).get('folder', '')
        browse_url = _github_folder_browse_url(folder)
        if 'foto_source' not in d.columns:
            d['foto_source'] = browse_url
        else:
            d['foto_source'] = d['foto_source'].fillna(browse_url)
        frames.append(d)

    all_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return all_df, used_demo


# Basemap
BASEMAPS = {
    "OpenStreetMap Standar": {
        "tiles": "OpenStreetMap",
        "attr": None,
    },
    "Citra Satelit (Esri World Imagery)": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/"
                 "World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": "Tiles &copy; Esri — Source: Esri, Maxar, Earthstar "
                "Geographics, and the GIS User Community",
    },
}
DEFAULT_BASEMAP = "OpenStreetMap Standar"


def make_base_map(center, zoom_start, basemap_name):
    bm = BASEMAPS.get(basemap_name, BASEMAPS[DEFAULT_BASEMAP])
    if bm["attr"]:
        return folium.Map(location=center, zoom_start=zoom_start,
                           tiles=bm["tiles"], attr=bm["attr"])
    return folium.Map(location=center, zoom_start=zoom_start, tiles=bm["tiles"])


# ----------------------------------------------------------------------------
# 2. SIDEBAR
# ----------------------------------------------------------------------------

st.markdown(
    """
    <style>
    [data-testid="stSidebar"] .stMarkdown {
        color: white !important;
        text-shadow: 1px 1px 3px rgba(0,0,0,0.8);
    }
    [data-testid="stSidebar"] .stMarkdown h1, 
    [data-testid="stSidebar"] .stMarkdown h2, 
    [data-testid="stSidebar"] .stMarkdown h3 {
        color: white !important;
        text-shadow: 2px 2px 4px rgba(0,0,0,0.8);
    }
    [data-testid="stSidebar"] .stCaption {
        color: #f0f0f0 !important;
        text-shadow: 1px 1px 3px rgba(0,0,0,0.8);
    }
    [data-testid="stSidebar"] .stSelectbox label,
    [data-testid="stSidebar"] .stSlider label,
    [data-testid="stSidebar"] .stCheckbox label {
        color: white !important;
        text-shadow: 1px 1px 3px rgba(0,0,0,0.8);
    }
    [data-testid="stSidebar"] .stWarning {
        background-color: rgba(255, 255, 0, 0.15) !important;
        color: white !important;
        border: 1px solid rgba(255, 255, 0, 0.3);
    }
    [data-testid="stSidebar"] .stSlider > div > div > div {
        background-color: rgba(255,255,255,0.3) !important;
    }
    [data-testid="stSidebar"] .stSlider > div > div > div > div {
        background-color: #ffffff !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"],
    [data-testid="stSidebar"] .streamlit-expander {
        background-color: #1a1a1a !important;
        border: 3px solid #ffffff !important;
        border-radius: 10px !important;
        margin-bottom: 8px;
        overflow: hidden;
        box-shadow: 0 2px 10px rgba(0,0,0,0.3) !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] summary,
    [data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderHeader"],
    [data-testid="stSidebar"] .streamlit-expanderHeader {
        color: #ffffff !important;
        background-color: #1a1a1a !important;
        padding: 14px 18px !important;
        font-weight: 700 !important;
        font-size: 14px !important;
        transition: all 0.3s ease;
        cursor: pointer;
        letter-spacing: 0.5px;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] summary p,
    [data-testid="stSidebar"] [data-testid="stExpander"] summary span,
    [data-testid="stSidebar"] [data-testid="stExpander"] summary div,
    [data-testid="stSidebar"] [data-testid="stExpander"] summary label {
        color: #ffffff !important;
        font-weight: 700 !important;
        text-shadow: none !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] summary:hover,
    [data-testid="stSidebar"] .streamlit-expanderHeader:hover {
        background-color: #000000 !important;
        box-shadow: 0 4px 15px rgba(0,0,0,0.5) !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] summary svg,
    [data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderToggleIcon"],
    [data-testid="stSidebar"] .streamlit-expanderHeader svg {
        color: #ffffff !important;
        fill: #ffffff !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderDetails"],
    [data-testid="stSidebar"] .streamlit-expanderContent {
        background-color: #1a1a1a !important;
        padding: 16px 16px 20px 16px !important;
        border-top: 1px solid rgba(255,255,255,0.35) !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderDetails"] .stMarkdown,
    [data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderDetails"] .stSlider label,
    [data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderDetails"] .stSlider p,
    [data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderDetails"] .stMarkdown p,
    [data-testid="stSidebar"] .streamlit-expanderContent .stMarkdown,
    [data-testid="stSidebar"] .streamlit-expanderContent .stSlider label,
    [data-testid="stSidebar"] .streamlit-expanderContent .stSlider p,
    [data-testid="stSidebar"] .streamlit-expanderContent .stMarkdown p {
        color: #ffffff !important;
        text-shadow: none !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderDetails"] .stSlider > div > div > div,
    [data-testid="stSidebar"] .streamlit-expanderContent .stSlider > div > div > div {
        background-color: rgba(255,255,255,0.2) !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderDetails"] .stSlider > div > div > div > div,
    [data-testid="stSidebar"] .streamlit-expanderContent .stSlider > div > div > div > div {
        background-color: #ffffff !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderDetails"] .stSlider label,
    [data-testid="stSidebar"] .streamlit-expanderContent .stSlider label {
        color: #ffffff !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderDetails"] .stSlider .stMarkdown,
    [data-testid="stSidebar"] .streamlit-expanderContent .stSlider .stMarkdown {
        color: #ffffff !important;
    }
    [data-testid="stSidebar"] [data-testid="stSliderThumbValue"],
    [data-testid="stSidebar"] [data-testid="stSliderThumbValue"] * {
        color: #ff1a1a !important;
        -webkit-text-fill-color: #ff1a1a !important;
        font-weight: 800 !important;
    }
    [data-testid="stSidebar"] .stButton button {
        background-color: rgba(255, 255, 255, 0.15) !important;
        color: white !important;
        border: 1px solid rgba(255, 255, 255, 0.3);
    }
    [data-testid="stSidebar"] .stButton button:hover {
        background-color: rgba(255, 255, 255, 0.25) !important;
    }
    [data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] {
        background-color: rgba(0, 0, 0, 0.2) !important;
        border-radius: 6px;
    }
    [data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] > div {
        color: white !important;
    }
    [data-testid="stSidebar"] .stCheckbox label {
        color: white !important;
    }
    [data-testid="stSidebar"] ::-webkit-scrollbar {
        width: 6px;
    }
    [data-testid="stSidebar"] ::-webkit-scrollbar-track {
        background: rgba(255, 255, 255, 0.1);
    }
    [data-testid="stSidebar"] ::-webkit-scrollbar-thumb {
        background: rgba(255, 255, 255, 0.3);
        border-radius: 3px;
    }
    [data-testid="stSidebar"] ::-webkit-scrollbar-thumb:hover {
        background: rgba(255, 255, 255, 0.5);
    }
    .main .block-container {
        background-color: transparent !important;
        backdrop-filter: none !important;
        padding: 1rem 2rem;
    }
    div[data-testid="metric-container"] {
        background-color: rgba(255, 255, 255, 0.92) !important;
        border-radius: 10px;
        padding: 10px;
        border: 1px solid rgba(200, 200, 200, 0.3);
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    .stTabs [data-baseweb="tab-list"] {
        background-color: rgba(255, 255, 255, 0.85) !important;
        border-radius: 8px;
        padding: 4px;
        border: 1px solid rgba(200, 200, 200, 0.2);
    }
    .stTabs [data-baseweb="tab"] {
        background-color: rgba(255, 255, 255, 0.5) !important;
        border-radius: 6px;
    }
    .stTabs [data-baseweb="tab"][aria-selected="true"] {
        background-color: #ffffff !important;
        border: 1px solid #0f2027;
    }
    .stTabs [role="tabpanel"] {
        background-color: rgba(255, 255, 255, 0.0) !important;
        padding-top: 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.sidebar.markdown("## 🏙️ UVIP Malang")
st.sidebar.caption(
    "**U**rban **V**isual **I**ntelligence **P**latform — piranti WebGIS Analytics "
    "*smart city* untuk menilai & mensimulasikan kualitas visual ruang terbuka "
    "Kota Malang berbasis AI."
)

with st.spinner("Memuat data hasil analisis AI dari Google Sheets…"):
    data_all, demo_used = load_all_corridors()

if demo_used:
    st.sidebar.warning(
        "Data demo digunakan untuk koridor: " + ", ".join(demo_used) +
        ". Pastikan Google Sheet sudah dibagikan sebagai **'Anyone with the "
        "link — Viewer'** agar data riil dapat dimuat.",
        icon="⚠️",
    )

# ----------------------------------------------------------------------------
# 1.1 TAMBAH KORIDOR BARU (UPLOAD FILE)
# ----------------------------------------------------------------------------
with st.sidebar.expander("➕ Tambah koridor baru (upload file)", expanded=False):
    if not UPLOAD_FEATURE_AVAILABLE:
        st.warning(
            "Fitur ini butuh versi terbaru **uvip_core.py** di server. "
            "Pastikan file itu sudah ikut di-push/redeploy.",
            icon="⚠️",
        )
    st.caption(
        "Unggah data titik/node koridor baru dalam format Excel (.xlsx) atau "
        "CSV — satu baris per titik. Kolom wajib: **kode, corridor, lat, lon**. "
        "Kolom opsional: side, foto, dan 8 indikator visual komposit "
        "(Building Visibility, Vegetation Coverage, dst — nilai 0–1). "
        "Unduh template (.xlsx) di bawah agar nama kolom sesuai."
    )
    template_df = build_upload_template()
    template_xlsx_buf = io.BytesIO()
    try:
        with pd.ExcelWriter(template_xlsx_buf, engine="openpyxl") as writer:
            template_df.to_excel(writer, index=False, sheet_name="Template Koridor")
        template_xlsx_bytes = template_xlsx_buf.getvalue()
    except ImportError:
        template_xlsx_bytes = None

    if template_xlsx_bytes:
        st.download_button(
            "⬇️ Unduh template (.xlsx)",
            data=template_xlsx_bytes,
            file_name="template_koridor_uvip.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_template_koridor",
        )
    else:
        st.warning(
            "Paket `openpyxl` belum terpasang di server, template diunduh "
            "sebagai CSV. Tambahkan `openpyxl` ke requirements.txt agar "
            "template dapat berupa .xlsx.",
            icon="⚠️",
        )
        st.download_button(
            "⬇️ Unduh template (CSV)",
            data=template_df.to_csv(index=False).encode("utf-8"),
            file_name="template_koridor_uvip.csv",
            mime="text/csv",
            key="dl_template_koridor",
        )
    uploaded_corridor_file = st.file_uploader(
        "Pilih file koridor baru (.csv / .xlsx / .xls)",
        type=["csv", "xlsx", "xls"],
        key="corridor_upload_file",
    )

uploaded_df = pd.DataFrame()
if uploaded_corridor_file is not None:
    uploaded_df, upload_msgs = parse_uploaded_corridor(
        uploaded_corridor_file, uploaded_corridor_file.name
    )
    if uploaded_df.empty:
        for msg in upload_msgs:
            st.sidebar.error(msg, icon="🚫")
    else:
        for msg in upload_msgs:
            st.sidebar.warning(msg, icon="⚠️")
        st.sidebar.success(
            f"✅ {uploaded_df['corridor'].nunique()} koridor baru / "
            f"{len(uploaded_df)} titik berhasil dimuat dari file.",
            icon="✅",
        )

if not uploaded_df.empty:
    data_all = (
        pd.concat([data_all, uploaded_df], ignore_index=True)
        if not data_all.empty else uploaded_df
    )

corridor_names = sorted(data_all["corridor"].unique()) if not data_all.empty else []

selected_corridor = st.sidebar.selectbox(
    "Pilih koridor / ruang terbuka",
    options=corridor_names,
    index=0 if corridor_names else None,
    key="selected_corridor",
)
selected_corridors = [selected_corridor] if selected_corridor else []

st.sidebar.markdown("---")
st.sidebar.markdown("### ⚙️ Bobot Indikator UVI")
st.sidebar.caption("Geser untuk mensimulasikan skenario preferensi publik / kebijakan penataan.")

weights = {}

if st.session_state.get("_reset_weights_pending", False):
    for ind in INDICATORS:
        st.session_state[f"w_{ind}"] = 1.0
    st.session_state["_reset_weights_pending"] = False

with st.sidebar.expander("📊 Atur bobot 8 indikator visual", expanded=False):
    for ind in INDICATORS:
        weights[ind] = st.slider(ind, 0.0, 2.0, 1.0, 0.1, key=f"w_{ind}")

if st.sidebar.button("↺ Reset bobot ke default"):
    st.session_state["_reset_weights_pending"] = True
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption(
    "Sumber data: hasil deteksi AI (computer vision) atas citra *street-level* "
    "koridor Kota Malang, diproses tim Peneliti UVIP (*Urban Visual Intelligence Platform*) Malang - Universitas Brawijaya. "
)

df = data_all[data_all["corridor"].isin(selected_corridors)].copy() if not data_all.empty else data_all
if not df.empty:
    df["UVI"] = compute_uvi(df, weights)

# ----------------------------------------------------------------------------
# 3. HEADER
# ----------------------------------------------------------------------------

if header_bg_set:
    st.markdown(
        """
        <div class="header-with-bg">
            <h2>🏙️ UVIP Malang - <i>Urban Visual Intelligence Platform<i> Malang City</h2>
            <p>
                Decision Support System Inteligensi Visual Digital Ruang Terbuka Perkotaan
                Berbasis AI untuk <b>Smart City Kota Malang</b> — adaptasi
                <i>Urban Visual Index (UVI)</i> dari citra <i>street-level</i>,
                divalidasi persepsi publik, mendukung SDG 11.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        """
        <div style="padding:1.1rem 1.4rem;border-radius:14px;
                    background:linear-gradient(120deg,#0f2027,#203a43,#2c5364);
                    color:white;margin-bottom:1rem;">
          <h2 style="margin:0;">🏙️ Model Sistem Simulasi Visual Digital Ruang Terbuka Perkotaan</h2>
          <p style="margin:0.3rem 0 0 0;opacity:0.9;">
            Berbasis AI untuk <b>Smart City Kota Malang</b> — adaptasi
            <i>Urban Visual Index (UVI)</i> dari citra <i>street-level</i>,
            divalidasi persepsi publik, mendukung SDG 11.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

if df.empty:
    st.error("Tidak ada data untuk ditampilkan. Periksa koneksi atau pilihan koridor di sidebar.")
    st.stop()

k1, k2, k3, k4 = st.columns(4)
k1.metric("Jumlah Koridor", f"{df['corridor'].nunique()}")
k2.metric("Jumlah Titik/Node", f"{len(df)}")
k3.metric("Rata-rata UVI", f"{df['UVI'].mean():.2f} / 10")
best = df.loc[df["UVI"].idxmax()]
k4.metric("Node Terbaik", f"{best['kode']} ({best['UVI']:.2f})", help=str(best["corridor"]))

st.markdown("")

tab_map, tab_indicators, tab_sim, tab_compare, tab_policy, tab_about, tab_data = st.tabs(
    ["🗺️ Peta Hotspot UVI", "📊 Indikator Visual", "🧮 Simulasi Skenario",
     "📈 Perbandingan Koridor", "🏙️ Policy Brief Perancangan", "📄 Tentang & Metodologi", "📥 Data & Unduh"]
)


# ----------------------------------------------------------------------------
# FUNGSI ANALISIS POLICY BRIEF BERBASIS UVI
# ----------------------------------------------------------------------------
UVI_POLICY_THRESHOLDS = {
    "Rendah": (0.0, 4.99),
    "Sedang": (5.0, 6.99),
    "Tinggi": (7.0, 8.49),
    "Sangat Tinggi": (8.5, 10.0),
}

INDICATOR_POLICY = {
    "Building Visibility": {
        "issue": "Kualitas keterbacaan dan keteraturan tampilan bangunan/fasad perlu diperkuat.",
        "actions": "Pedoman fasad koridor, pengendalian reklame/signage, penataan frontage, serta penguatan kontinuitas fasad pada ruang publik.",
        "design": "Facade guideline, active frontage, visual corridor, pengendalian signage dan utilitas yang mengganggu pandangan."
    },
    "Vegetation Coverage": {
        "issue": "Tutupan vegetasi/naungan visual relatif rendah.",
        "actions": "Prioritaskan penanaman pohon peneduh, pocket green, planter, dan green buffer pada segmen dengan skor rendah.",
        "design": "Tree canopy, planting strip, bioswale/green buffer, dan ruang hijau mikro yang terintegrasi dengan trotoar."
    },
    "Sky Openness": {
        "issue": "Keterbukaan langit/pandangan vertikal relatif rendah.",
        "actions": "Evaluasi massa bangunan, setback, elemen vertikal, dan koridor pandang pada lokasi dengan skor rendah.",
        "design": "View corridor, pengendalian ketinggian/massa pada titik sensitif, dan perlindungan vista perkotaan."
    },
    "Ground Accessibility": {
        "issue": "Aksesibilitas dan keterhubungan ruang pejalan kaki perlu ditingkatkan.",
        "actions": "Perbaiki kontinuitas trotoar, ramp, penyeberangan, konektivitas antar ruang publik, serta hambatan fisik.",
        "design": "Complete pedestrian network, universal design, tactile paving, curb ramp, crossing dan street furniture yang tidak menghalangi jalur."
    },
    "Human Activity": {
        "issue": "Aktivitas manusia dan vitalitas ruang publik relatif rendah.",
        "actions": "Aktifkan frontage, tempat duduk, ruang interaksi, program kegiatan, dan fungsi lantai dasar yang mendukung aktivitas publik.",
        "design": "Active frontage, seating, plaza pocket, ruang komunal, pencahayaan, dan programming ruang publik."
    },
    "Vehicle Intensity": {
        "issue": "Dominasi kendaraan berpotensi menurunkan kualitas visual dan kenyamanan ruang publik.",
        "actions": "Terapkan traffic calming, manajemen parkir, pembatasan kendaraan pada titik tertentu, dan prioritas pejalan kaki.",
        "design": "Narrowing, raised crossing, curb extension, parklet, pembatas parkir dan redistribusi ruang jalan."
    },
    "Traffic Infrastructure": {
        "issue": "Kualitas dan keteraturan infrastruktur lalu lintas perlu diperbaiki.",
        "actions": "Rapikan marka, rambu, street furniture, penerangan, utilitas dan elemen tepi jalan agar konsisten secara visual.",
        "design": "Street furniture guideline, coordinated signage, lighting design, utility concealment dan streetscape palette."
    },
    "Heritage Dominance": {
        "issue": "Karakter/identitas heritage belum cukup terbaca pada ruang visual.",
        "actions": "Perkuat elemen identitas lokal, konservasi fasad, interpretasi heritage, dan pengendalian visual pada koridor bersejarah.",
        "design": "Heritage streetscape, material/facade guideline, interpretive signage dan perlindungan visual landmark."
    },
}


def _uvi_category(v):
    try:
        v = float(v)
    except Exception:
        return "Tidak tersedia"
    if v < 5.0:
        return "Rendah"
    if v < 7.0:
        return "Sedang"
    if v < 8.5:
        return "Tinggi"
    return "Sangat Tinggi"


def _indicator_mean(df_in, indicator):
    col = f"uvi::{indicator}"
    if col not in df_in.columns:
        return np.nan
    return pd.to_numeric(df_in[col], errors="coerce").mean()


def build_policy_brief(df_in, weights):
    """Menyusun diagnosis dan rekomendasi perancangan kota dari hasil UVI."""
    if df_in.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    corr_rows = []
    for corridor, sub in df_in.groupby("corridor"):
        uvi_mean = pd.to_numeric(sub["UVI"], errors="coerce").mean()
        ind_values = {ind: _indicator_mean(sub, ind) for ind in INDICATORS}
        valid_inds = {k: v for k, v in ind_values.items() if pd.notna(v)}
        weakest = sorted(valid_inds.items(), key=lambda x: x[1])[:3]
        corr_rows.append({
            "Koridor": corridor,
            "Rata-rata UVI": uvi_mean,
            "Kategori": _uvi_category(uvi_mean),
            "Indikator Prioritas 1": weakest[0][0] if len(weakest) > 0 else "—",
            "Nilai 1": weakest[0][1] if len(weakest) > 0 else np.nan,
            "Indikator Prioritas 2": weakest[1][0] if len(weakest) > 1 else "—",
            "Nilai 2": weakest[1][1] if len(weakest) > 1 else np.nan,
            "Indikator Prioritas 3": weakest[2][0] if len(weakest) > 2 else "—",
            "Nilai 3": weakest[2][1] if len(weakest) > 2 else np.nan,
            "Jumlah Node": len(sub),
        })
    corridor_df = pd.DataFrame(corr_rows).sort_values("Rata-rata UVI")

    ind_rows = []
    for ind in INDICATORS:
        val = _indicator_mean(df_in, ind)
        if pd.notna(val):
            priority = "Prioritas Tinggi" if val < 0.50 else ("Prioritas Menengah" if val < 0.70 else "Pemeliharaan")
            meta = INDICATOR_POLICY.get(ind, {})
            ind_rows.append({
                "Indikator": ind,
                "Rata-rata": val,
                "Prioritas": priority,
                "Diagnosis": meta.get("issue", "Perlu evaluasi lebih lanjut."),
                "Intervensi": meta.get("actions", "Evaluasi desain pada lokasi dengan skor rendah."),
                "Arahan Desain": meta.get("design", "Integrasikan dengan pedoman desain koridor."),
            })
    indicator_df = pd.DataFrame(ind_rows).sort_values("Rata-rata")

    node_df = df_in.copy()
    node_df["Prioritas UVI"] = node_df["UVI"].apply(_uvi_category)
    node_df = node_df[node_df["UVI"] < 7.0].copy()
    node_df["Prioritas"] = np.where(node_df["UVI"] < 5.0, "P1 — Intervensi segera", "P2 — Peningkatan")
    node_df = node_df.sort_values("UVI")
    return corridor_df, indicator_df, node_df


def policy_brief_markdown(corridor_df, indicator_df, selected_scope):
    if corridor_df.empty or indicator_df.empty:
        return "# Policy Brief UVIP Malang\n\nData UVI belum tersedia."
    top = corridor_df.iloc[0]
    strongest = corridor_df.iloc[-1]
    weakest = indicator_df.iloc[0]
    high = int((corridor_df["Rata-rata UVI"] < 5.0).sum())
    medium = int(((corridor_df["Rata-rata UVI"] >= 5.0) & (corridor_df["Rata-rata UVI"] < 7.0)).sum())
    return f"""# POLICY BRIEF — Perancangan Kota Berbasis Urban Visual Index (UVI)

**Wilayah analisis:** {selected_scope}

## 1. Pesan kebijakan
Hasil UVI digunakan sebagai instrumen diagnosis kualitas visual ruang terbuka pada tingkat koridor dan node. Koridor dengan UVI rendah perlu diprioritaskan untuk intervensi desain, sedangkan koridor dengan UVI tinggi diarahkan untuk pemeliharaan kualitas dan perlindungan karakter ruang.

## 2. Temuan utama
- Koridor dengan prioritas intervensi tertinggi: **{top['Koridor']}** (UVI {top['Rata-rata UVI']:.2f}).
- Koridor dengan kualitas visual tertinggi dalam cakupan analisis: **{strongest['Koridor']}** (UVI {strongest['Rata-rata UVI']:.2f}).
- Indikator terendah: **{weakest['Indikator']}** ({weakest['Rata-rata']:.3f}).
- Koridor UVI rendah (<5): **{high}**; koridor UVI sedang (5–<7): **{medium}**.

## 3. Arahan perancangan kota
1. **Prioritaskan node UVI rendah** sebagai lokasi pilot improvement, bukan melakukan intervensi seragam pada seluruh koridor.
2. **Gunakan indikator terendah sebagai diagnosis desain**: vegetasi → canopy/green buffer; aksesibilitas → trotoar dan crossing; aktivitas → active frontage dan seating; kendaraan → traffic calming/parkir; heritage → konservasi karakter visual.
3. **Terapkan pendekatan complete street dan universal design** pada ruang jalan yang menjadi penghubung ruang publik.
4. **Pertahankan koridor UVI tinggi** melalui design guideline, pemeliharaan vegetasi, street furniture, fasad, vista, dan pengendalian elemen visual yang berpotensi menurunkan kualitas.
5. **Gunakan simulasi UVI sebagai alat ex-ante** untuk membandingkan alternatif desain sebelum implementasi fisik.

## 4. Tata kelola implementasi
UVI dapat digunakan sebagai dashboard monitoring lintas perangkat daerah untuk menetapkan lokasi prioritas, menyusun desain teknis, mengalokasikan anggaran, dan mengevaluasi perubahan kualitas ruang setelah intervensi.

## 5. Catatan metodologis
UVI merupakan indeks komposit berbasis 8 indikator visual. Rekomendasi ini adalah **diagnosis berbasis indikator**, sehingga keputusan desain akhir tetap memerlukan verifikasi lapangan, aspek keselamatan, aksesibilitas, utilitas, regulasi, dan partisipasi pengguna ruang.
"""


def policy_brief_docx_bytes(corridor_df, indicator_df, node_df, selected_scope):
    """Membangun versi editable Policy Brief UVI dalam format Word (.docx)."""
    if not DOCX_AVAILABLE:
        return None
    if corridor_df.empty or indicator_df.empty:
        return None

    top = corridor_df.iloc[0]
    strongest = corridor_df.iloc[-1]
    weakest = indicator_df.iloc[0]
    low_count = int((corridor_df["Rata-rata UVI"] < 5.0).sum())
    med_count = int(((corridor_df["Rata-rata UVI"] >= 5.0) & (corridor_df["Rata-rata UVI"] < 7.0)).sum())

    doc = Document()

    for section in doc.sections:
        section.top_margin = Cm(1.7)
        section.bottom_margin = Cm(1.6)
        section.left_margin = Cm(1.6)
        section.right_margin = Cm(1.6)

    def set_col_widths(table, widths_cm):
        for row in table.rows:
            for cell, w in zip(row.cells, widths_cm):
                cell.width = Cm(w)
        table.autofit = False
        for col, w in zip(table.columns, widths_cm):
            col.width = Cm(w)

    def shade_cell(cell, hex_color):
        shd = OxmlElement("w:shd")
        shd.set(qn("w:fill"), hex_color)
        cell._tc.get_or_add_tcPr().append(shd)

    def set_cell_text(cell, text, bold=False, color_hex=None, size=9, align_center=False):
        cell.text = ""
        p = cell.paragraphs[0]
        if align_center:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(str(text))
        run.bold = bold
        run.font.size = Pt(size)
        if color_hex:
            run.font.color.rgb = RGBColor.from_string(color_hex)

    title = doc.add_heading("POLICY BRIEF", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = doc.add_heading("Perancangan Kota Berbasis Urban Visual Index (UVI)", level=1)
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    scope_p = doc.add_paragraph()
    scope_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    scope_run = scope_p.add_run(f"UVIP Malang - Urban Visual Intelligence Platform | Cakupan: {selected_scope}")
    scope_run.font.size = Pt(9.5)
    scope_run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

    metric_table = doc.add_table(rows=2, cols=4)
    metric_table.style = "Table Grid"
    headers = ["Koridor prioritas", "UVI terendah", "Indikator terlemah", "Node prioritas"]
    values = [str(top["Koridor"]), f"{float(top['Rata-rata UVI']):.2f}", str(weakest["Indikator"]), str(len(node_df))]
    for i, h in enumerate(headers):
        cell = metric_table.rows[0].cells[i]
        set_cell_text(cell, h, size=8.5)
        shade_cell(cell, "EAF1F5")
    for i, v in enumerate(values):
        set_cell_text(metric_table.rows[1].cells[i], v, bold=True, size=10)
    set_col_widths(metric_table, [4.3, 3.2, 4.8, 3.2])
    doc.add_paragraph()

    def add_body(text):
        p = doc.add_paragraph(text)
        p.paragraph_format.space_after = Pt(8)
        return p

    def add_dash_bullet(html_parts):
        p = doc.add_paragraph()
        p.add_run("- ")
        for text, bold in html_parts:
            r = p.add_run(text)
            r.bold = bold
        return p

    doc.add_heading("1. Pesan Kebijakan", level=1)
    add_body(
        "Hasil UVI digunakan sebagai instrumen diagnosis kualitas visual ruang terbuka pada "
        "tingkat koridor dan node. Koridor dengan UVI rendah diprioritaskan untuk intervensi "
        "perancangan, sedangkan koridor dengan UVI tinggi diarahkan untuk pemeliharaan kualitas "
        "dan perlindungan karakter ruang."
    )

    doc.add_heading("2. Temuan Utama", level=1)
    add_dash_bullet([
        ("Koridor dengan prioritas intervensi tertinggi: ", False),
        (str(top["Koridor"]), True),
        (f" (UVI {float(top['Rata-rata UVI']):.2f}).", False),
    ])
    add_dash_bullet([
        ("Koridor dengan kualitas visual tertinggi: ", False),
        (str(strongest["Koridor"]), True),
        (f" (UVI {float(strongest['Rata-rata UVI']):.2f}).", False),
    ])
    add_dash_bullet([
        ("Indikator dengan nilai rata-rata terendah: ", False),
        (str(weakest["Indikator"]), True),
        (f" ({float(weakest['Rata-rata']):.3f}).", False),
    ])
    add_dash_bullet([
        ("Koridor UVI rendah (<5): ", False),
        (str(low_count), True),
        ("; koridor UVI sedang (5-<7): ", False),
        (str(med_count), True),
        (".", False),
    ])

    doc.add_heading("3. Matriks Prioritas Koridor", level=1)
    mcols = ["Koridor", "UVI", "Kategori", "Prioritas 1", "Nilai", "Prioritas 2", "Nilai"]
    mtable = doc.add_table(rows=1, cols=len(mcols))
    mtable.style = "Table Grid"
    for i, h in enumerate(mcols):
        cell = mtable.rows[0].cells[i]
        set_cell_text(cell, h, bold=True, color_hex="FFFFFF", size=8.5, align_center=(i != 0))
        shade_cell(cell, "263238")
    for _, r in corridor_df.iterrows():
        row_cells = mtable.add_row().cells
        set_cell_text(row_cells[0], r["Koridor"], size=8.5)
        set_cell_text(row_cells[1], f"{float(r['Rata-rata UVI']):.2f}", size=8.5, align_center=True)
        set_cell_text(row_cells[2], r["Kategori"], size=8.5)
        set_cell_text(row_cells[3], r["Indikator Prioritas 1"], size=8.5)
        set_cell_text(row_cells[4], f"{float(r['Nilai 1']):.3f}" if pd.notna(r["Nilai 1"]) else "-", size=8.5, align_center=True)
        set_cell_text(row_cells[5], r["Indikator Prioritas 2"], size=8.5)
        set_cell_text(row_cells[6], f"{float(r['Nilai 2']):.3f}" if pd.notna(r["Nilai 2"]) else "-", size=8.5, align_center=True)
    set_col_widths(mtable, [3.6, 1.6, 2.4, 3.6, 1.7, 3.6, 1.7])
    doc.add_paragraph()

    doc.add_heading("4. Diagnosis Indikator dan Arahan Perancangan", level=1)
    for _, r in indicator_df.iterrows():
        p = doc.add_paragraph()
        p.add_run(f"{r['Indikator']}").bold = True
        p.add_run(f" - skor rata-rata {float(r['Rata-rata']):.3f} ({r['Prioritas']})")
        p.paragraph_format.space_after = Pt(2)
        for label, key in [("Diagnosis", "Diagnosis"), ("Intervensi", "Intervensi"), ("Arahan desain", "Arahan Desain")]:
            sp = doc.add_paragraph()
            sp.paragraph_format.space_after = Pt(2)
            sp.paragraph_format.left_indent = Cm(0.4)
            run = sp.add_run(f"{label}: {r[key]}")
            run.font.size = Pt(9)
        doc.add_paragraph().paragraph_format.space_after = Pt(2)

    doc.add_heading("5. Rekomendasi Kebijakan Perancangan Kota", level=1)
    recommendations = [
        ("Prioritas berbasis node.", "Fokuskan investasi awal pada node UVI < 5 sebagai lokasi pilot improvement dan gunakan indikator terendah sebagai dasar diagnosis desain."),
        ("Complete street dan universal design.", "Jika Ground Accessibility rendah, prioritaskan kontinuitas trotoar, akses universal, crossing, dan penghilangan hambatan pada jalur pedestrian."),
        ("Green streets dan kenyamanan.", "Jika Vegetation Coverage rendah, tingkatkan canopy pohon, planting strip, pocket green, dan green buffer pada ruang jalan dan ruang terbuka."),
        ("Aktivasi ruang publik.", "Jika Human Activity rendah, arahkan desain pada active frontage, seating, ruang interaksi, pencahayaan, dan programming kegiatan."),
        ("Manajemen kendaraan.", "Jika Vehicle Intensity menjadi indikator lemah, pertimbangkan traffic calming, manajemen parkir, dan redistribusi ruang jalan dengan prioritas pejalan kaki."),
        ("Identitas dan heritage.", "Jika Heritage Dominance rendah pada koridor bersejarah, perkuat fasad, material, signage, pencahayaan, interpretasi heritage, dan perlindungan visual landmark."),
        ("Pemeliharaan koridor unggul.", "Koridor dengan UVI tinggi perlu dipertahankan melalui design guideline, pemeliharaan vegetasi, street furniture, fasad, vista, dan pengendalian elemen visual."),
        ("Evaluasi ex-ante.", "Gunakan simulasi UVI untuk membandingkan alternatif desain sebelum intervensi fisik sehingga pilihan desain dapat dievaluasi berbasis bukti."),
    ]
    for title_rec, desc in recommendations:
        p = doc.add_paragraph()
        p.add_run(title_rec + " ").bold = True
        p.add_run(desc)
        p.paragraph_format.space_after = Pt(6)

    if not node_df.empty:
        doc.add_heading("6. Daftar Node Prioritas", level=1)
        ncols = ["Koridor", "Node", "UVI", "Prioritas"]
        ntable = doc.add_table(rows=1, cols=len(ncols))
        ntable.style = "Table Grid"
        for i, h in enumerate(ncols):
            cell = ntable.rows[0].cells[i]
            set_cell_text(cell, h, bold=True, color_hex="FFFFFF", size=8.5, align_center=(i != 0))
            shade_cell(cell, "455A64")
        for _, r in node_df.head(30).iterrows():
            row_cells = ntable.add_row().cells
            set_cell_text(row_cells[0], r.get("corridor", "-"), size=8.5)
            set_cell_text(row_cells[1], r.get("kode", "-"), size=8.5)
            set_cell_text(row_cells[2], f"{float(r['UVI']):.2f}", size=8.5, align_center=True)
            set_cell_text(row_cells[3], r.get("Prioritas", "-"), size=8.5)
        set_col_widths(ntable, [6.0, 3.5, 2.2, 6.0])
        if len(node_df) > 30:
            note = doc.add_paragraph()
            note_run = note.add_run(f"Catatan: dokumen ini menampilkan 30 node prioritas teratas dari total {len(node_df)} node.")
            note_run.italic = True
            note_run.font.size = Pt(8.5)
        doc.add_paragraph()

    doc.add_heading("7. Tata Kelola Implementasi", level=1)
    add_body(
        "UVI dapat digunakan sebagai dashboard monitoring lintas perangkat daerah untuk "
        "menetapkan lokasi prioritas, menyusun desain teknis, mengalokasikan anggaran, serta "
        "mengevaluasi perubahan kualitas ruang setelah intervensi. Hasil UVI harus dipadukan "
        "dengan survei lapangan, keselamatan, aksesibilitas, utilitas, regulasi, dan partisipasi "
        "pengguna ruang."
    )

    doc.add_heading("8. Catatan Metodologis", level=1)
    add_body(
        "UVI merupakan indeks komposit berbasis 8 indikator visual. Rekomendasi dalam policy "
        "brief ini merupakan diagnosis berbasis indikator dan tidak dimaksudkan menggantikan "
        "desain teknis, analisis lalu lintas, audit keselamatan, ketentuan tata ruang, maupun "
        "proses konsultasi pemangku kepentingan."
    )

    footer = doc.add_paragraph()
    footer_run = footer.add_run(
        "Sumber: UVIP Malang - Universitas Brawijaya. Policy brief digenerasikan otomatis dari "
        "data UVI yang aktif pada dashboard."
    )
    footer_run.italic = True
    footer_run.font.size = Pt(8.5)
    footer_run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


def _pdf_safe(text):
    """Normalisasi karakter agar kompatibel dengan font standar ReportLab."""
    if text is None:
        return ""
    return (str(text)
            .replace("—", "-")
            .replace("–", "-")
            .replace("→", "->")
            .replace("≤", "<=")
            .replace("≥", ">=")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def policy_brief_pdf_bytes(corridor_df, indicator_df, node_df, selected_scope):
    """Membangun Policy Brief UVI dalam format PDF A4."""
    if not REPORTLAB_AVAILABLE:
        return None
    if corridor_df.empty or indicator_df.empty:
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=16*mm, leftMargin=16*mm,
        topMargin=17*mm, bottomMargin=16*mm,
        title="Policy Brief Perancangan Kota Berbasis UVI - UVIP Malang",
        author="Tim UVIP Malang - Universitas Brawijaya",
    )

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "PBTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=18, leading=22, alignment=TA_CENTER, spaceAfter=6,
    )
    subtitle = ParagraphStyle(
        "PBSubtitle", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9.5, leading=13, alignment=TA_CENTER, spaceAfter=14,
    )
    h1 = ParagraphStyle(
        "PBH1", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=12.5, leading=15, spaceBefore=8, spaceAfter=6,
    )
    body = ParagraphStyle(
        "PBBody", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=9.2, leading=13, spaceAfter=5, alignment=TA_LEFT,
    )
    small = ParagraphStyle(
        "PBSmall", parent=body, fontSize=7.8, leading=10.5,
    )
    callout = ParagraphStyle(
        "PBCallout", parent=body, fontName="Helvetica-Bold",
        fontSize=10, leading=14, spaceAfter=4,
    )

    story = []
    top = corridor_df.iloc[0]
    strongest = corridor_df.iloc[-1]
    weakest = indicator_df.iloc[0]
    low_count = int((corridor_df["Rata-rata UVI"] < 5.0).sum())
    med_count = int(((corridor_df["Rata-rata UVI"] >= 5.0) &
                     (corridor_df["Rata-rata UVI"] < 7.0)).sum())

    story.append(Paragraph("POLICY BRIEF", title))
    story.append(Paragraph(
        "Perancangan Kota Berbasis Urban Visual Index (UVI)", title
    ))
    story.append(Paragraph(
        f"UVIP Malang - Urban Visual Intelligence Platform | Cakupan: {_pdf_safe(selected_scope)}",
        subtitle
    ))

    metric_data = [
        [Paragraph("Koridor prioritas", small), Paragraph("UVI terendah", small),
         Paragraph("Indikator terlemah", small), Paragraph("Node prioritas", small)],
        [Paragraph(f"<b>{_pdf_safe(top['Koridor'])}</b>", body),
         Paragraph(f"<b>{float(top['Rata-rata UVI']):.2f}</b>", body),
         Paragraph(f"<b>{_pdf_safe(weakest['Indikator'])}</b>", body),
         Paragraph(f"<b>{len(node_df)}</b>", body)],
    ]
    metric_table = Table(metric_data, colWidths=[43*mm, 32*mm, 48*mm, 32*mm])
    metric_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#EAF1F5")),
        ("BOX", (0,0), (-1,-1), 0.6, colors.HexColor("#9AA7B2")),
        ("INNERGRID", (0,0), (-1,-1), 0.4, colors.HexColor("#C7D0D7")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 6), ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ]))
    story.append(metric_table)
    story.append(Spacer(1, 7))

    story.append(Paragraph("1. Pesan Kebijakan", h1))
    story.append(Paragraph(
        "Hasil UVI digunakan sebagai instrumen diagnosis kualitas visual ruang terbuka pada tingkat koridor dan node. "
        "Koridor dengan UVI rendah diprioritaskan untuk intervensi perancangan, sedangkan koridor dengan UVI tinggi "
        "diarahkan untuk pemeliharaan kualitas dan perlindungan karakter ruang.", body))

    story.append(Paragraph("2. Temuan Utama", h1))
    findings = [
        f"Koridor dengan prioritas intervensi tertinggi: <b>{_pdf_safe(top['Koridor'])}</b> (UVI {float(top['Rata-rata UVI']):.2f}).",
        f"Koridor dengan kualitas visual tertinggi: <b>{_pdf_safe(strongest['Koridor'])}</b> (UVI {float(strongest['Rata-rata UVI']):.2f}).",
        f"Indikator dengan nilai rata-rata terendah: <b>{_pdf_safe(weakest['Indikator'])}</b> ({float(weakest['Rata-rata']):.3f}).",
        f"Koridor UVI rendah (&lt;5): <b>{low_count}</b>; koridor UVI sedang (5-&lt;7): <b>{med_count}</b>.",
    ]
    for item in findings:
        story.append(Paragraph("- " + item, body))

    story.append(Paragraph("3. Matriks Prioritas Koridor", h1))
    cdata = [[Paragraph(x, small) for x in [
        "Koridor", "UVI", "Kategori", "Prioritas 1", "Nilai", "Prioritas 2", "Nilai"
    ]]]
    for _, r in corridor_df.iterrows():
        cdata.append([
            Paragraph(_pdf_safe(r["Koridor"]), small),
            Paragraph(f"{float(r['Rata-rata UVI']):.2f}", small),
            Paragraph(_pdf_safe(r["Kategori"]), small),
            Paragraph(_pdf_safe(r["Indikator Prioritas 1"]), small),
            Paragraph(f"{float(r['Nilai 1']):.3f}" if pd.notna(r["Nilai 1"]) else "-", small),
            Paragraph(_pdf_safe(r["Indikator Prioritas 2"]), small),
            Paragraph(f"{float(r['Nilai 2']):.3f}" if pd.notna(r["Nilai 2"]) else "-", small),
        ])
    ctable = Table(cdata, colWidths=[33*mm, 15*mm, 23*mm, 35*mm, 17*mm, 35*mm, 17*mm], repeatRows=1)
    ctable.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#263238")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#AAB4BA")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (1,1), (1,-1), "CENTER"), ("ALIGN", (4,1), (4,-1), "CENTER"), ("ALIGN", (6,1), (6,-1), "CENTER"),
        ("LEFTPADDING", (0,0), (-1,-1), 4), ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    story.append(ctable)

    story.append(Paragraph("4. Diagnosis Indikator dan Arahan Perancangan", h1))
    for _, r in indicator_df.iterrows():
        story.append(KeepTogether([
            Paragraph(f"<b>{_pdf_safe(r['Indikator'])}</b> - skor rata-rata {float(r['Rata-rata']):.3f} ({_pdf_safe(r['Prioritas'])})", body),
            Paragraph(f"Diagnosis: {_pdf_safe(r['Diagnosis'])}", small),
            Paragraph(f"Intervensi: {_pdf_safe(r['Intervensi'])}", small),
            Paragraph(f"Arahan desain: {_pdf_safe(r['Arahan Desain'])}", small),
            Spacer(1, 3),
        ]))

    story.append(Paragraph("5. Rekomendasi Kebijakan Perancangan Kota", h1))
    recommendations = [
        ("Prioritas berbasis node", "Fokuskan investasi awal pada node UVI < 5 sebagai lokasi pilot improvement dan gunakan indikator terendah sebagai dasar diagnosis desain."),
        ("Complete street dan universal design", "Jika Ground Accessibility rendah, prioritaskan kontinuitas trotoar, akses universal, crossing, dan penghilangan hambatan pada jalur pedestrian."),
        ("Green streets dan kenyamanan", "Jika Vegetation Coverage rendah, tingkatkan canopy pohon, planting strip, pocket green, dan green buffer pada ruang jalan dan ruang terbuka."),
        ("Aktivasi ruang publik", "Jika Human Activity rendah, arahkan desain pada active frontage, seating, ruang interaksi, pencahayaan, dan programming kegiatan."),
        ("Manajemen kendaraan", "Jika Vehicle Intensity menjadi indikator lemah, pertimbangkan traffic calming, manajemen parkir, dan redistribusi ruang jalan dengan prioritas pejalan kaki."),
        ("Identitas dan heritage", "Jika Heritage Dominance rendah pada koridor bersejarah, perkuat fasad, material, signage, pencahayaan, interpretasi heritage, dan perlindungan visual landmark."),
        ("Pemeliharaan koridor unggul", "Koridor dengan UVI tinggi perlu dipertahankan melalui design guideline, pemeliharaan vegetasi, street furniture, fasad, vista, dan pengendalian elemen visual."),
        ("Evaluasi ex-ante", "Gunakan simulasi UVI untuk membandingkan alternatif desain sebelum intervensi fisik sehingga pilihan desain dapat dievaluasi berbasis bukti."),
    ]
    for title_rec, desc in recommendations:
        story.append(Paragraph(f"<b>{_pdf_safe(title_rec)}.</b> {_pdf_safe(desc)}", body))

    if not node_df.empty:
        story.append(Paragraph("6. Daftar Node Prioritas", h1))
        ndata = [[Paragraph(x, small) for x in ["Koridor", "Node", "UVI", "Prioritas"]]]
        for _, r in node_df.head(30).iterrows():
            ndata.append([
                Paragraph(_pdf_safe(r.get("corridor", "-")), small),
                Paragraph(_pdf_safe(r.get("kode", "-")), small),
                Paragraph(f"{float(r['UVI']):.2f}", small),
                Paragraph(_pdf_safe(r.get("Prioritas", "-")), small),
            ])
        ntable = Table(ndata, colWidths=[55*mm, 32*mm, 20*mm, 55*mm], repeatRows=1)
        ntable.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#455A64")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#B0BEC5")),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("ALIGN", (2,1), (2,-1), "CENTER"),
            ("LEFTPADDING", (0,0), (-1,-1), 4), ("RIGHTPADDING", (0,0), (-1,-1), 4),
            ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ]))
        story.append(ntable)
        if len(node_df) > 30:
            story.append(Paragraph(f"Catatan: PDF menampilkan 30 node prioritas teratas dari total {len(node_df)} node.", small))

    story.append(Paragraph("7. Tata Kelola Implementasi", h1))
    story.append(Paragraph(
        "UVI dapat digunakan sebagai dashboard monitoring lintas perangkat daerah untuk menetapkan lokasi prioritas, "
        "menyusun desain teknis, mengalokasikan anggaran, serta mengevaluasi perubahan kualitas ruang setelah intervensi. "
        "Hasil UVI harus dipadukan dengan survei lapangan, keselamatan, aksesibilitas, utilitas, regulasi, dan partisipasi pengguna ruang.", body))

    story.append(Paragraph("8. Catatan Metodologis", h1))
    story.append(Paragraph(
        "UVI merupakan indeks komposit berbasis 8 indikator visual. Rekomendasi dalam policy brief ini merupakan diagnosis "
        "berbasis indikator dan tidak dimaksudkan menggantikan desain teknis, analisis lalu lintas, audit keselamatan, "
        "ketentuan tata ruang, maupun proses konsultasi pemangku kepentingan.", body))

    story.append(Spacer(1, 7))
    story.append(Paragraph(
        "Sumber: UVIP Malang - Universitas Brawijaya. Policy brief digenerasikan otomatis dari data UVI yang aktif pada dashboard.",
        small))

    def add_page_number(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(16*mm, 9*mm, "UVIP Malang - Policy Brief Perancangan Kota Berbasis UVI")
        canvas.drawRightString(A4[0]-16*mm, 9*mm, f"Halaman {doc_obj.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    return buffer.getvalue()


# ----------------------------------------------------------------------------
# TAB 1 — PETA HOTSPOT
# ----------------------------------------------------------------------------
with tab_map:
    left, right = st.columns([3, 1])
    with right:
        st.markdown("#### Legenda")
        st.caption("🔴 UVI tinggi (visual berkualitas) → 🔵 UVI rendah")
        with st.expander("ℹ️ Apa arti UVI tinggi/rendah?"):
            st.markdown(
                """
                **🔴 UVI tinggi** — ruang terbuka dengan kualitas visual yang lebih baik:
                bangunan tertata & mudah dilihat *(Building Visibility)*, vegetasi cukup rimbun
                *(Vegetation Coverage)*, langit cukup terbuka *(Sky Openness)*, area pejalan
                mudah diakses *(Ground Accessibility)*, aktivitas manusia hidup namun tidak
                sesak *(Human Activity)*, kendaraan tidak mendominasi *(Vehicle Intensity)*,
                infrastruktur lalu lintas tertata rapi *(Traffic Infrastructure)*, dan unsur
                bangunan/heritage cukup menonjol *(Heritage Dominance)*.

                **🔵 UVI rendah** — sebaliknya: pandangan visual cenderung terganggu, misalnya
                minim vegetasi/naungan, langit tertutup bangunan padat, area pejalan sulit
                diakses, kepadatan kendaraan tinggi, atau elemen visual kurang tertata.

                Skor UVI merupakan agregasi dari **8 indikator visual komposit** di atas
                (bobot dapat diubah pada panel *Bobot Indikator UVI* untuk mensimulasikan
                skenario preferensi publik/kebijakan penataan).
                """
            )
        basemap_name = st.selectbox("Basemap", list(BASEMAPS.keys()),
                                     index=list(BASEMAPS.keys()).index(DEFAULT_BASEMAP))
        show_nodes = st.checkbox("Tampilkan node fotogenik + skor UVI", value=True)
        show_heat = st.checkbox("Tampilkan heatmap kepadatan UVI", value=True)
        radius = st.slider("Radius heatmap", 10, 40, 22)

    with left:
        valid = df.dropna(subset=["lat", "lon"])
        if valid.empty:
            st.info("Tidak ada koordinat valid pada data terpilih.")
        else:
            center = [valid["lat"].mean(), valid["lon"].mean()]
            fmap = make_base_map(center, 17, basemap_name)

            if show_heat:
                heat_data = valid[["lat", "lon", "UVI"]].dropna().values.tolist()
                HeatMap(heat_data, radius=radius, blur=18, max_zoom=19).add_to(fmap)

            if show_nodes:
                vmin, vmax = valid["UVI"].min(), valid["UVI"].max()
                for _, row in valid.iterrows():
                    frac = 0.5 if vmax == vmin else (row["UVI"] - vmin) / (vmax - vmin)
                    color = f"#{int(255*frac):02x}{int(80*(1-frac)):02x}{int(255*(1-frac)):02x}"
                    popup_html = build_node_popup_html(row, color)
                    folium.CircleMarker(
                        location=[row["lat"], row["lon"]],
                        radius=9,
                        color=color,
                        fill=True,
                        fill_color=color,
                        fill_opacity=0.9,
                        popup=folium.Popup(popup_html, max_width=300),
                        tooltip=f"📷 {row['kode']} · {row['UVI']:.2f} (klik untuk detail lengkap)",
                    ).add_to(fmap)
                    folium.map.Marker(
                        [row["lat"], row["lon"]],
                        icon=folium.DivIcon(html=f"""
                            <div style="font-size:11px;font-weight:700;color:white;
                                        text-shadow:1px 1px 2px #000;
                                        transform:translate(10px,-10px);">
                                {row['UVI']:.2f}
                            </div>""")
                    ).add_to(fmap)

            st_folium(fmap, height=560, width=None)
    st.caption(
        "Peta ini mensimulasikan luaran 'HASIL & LUARAN: PETA HOTSPOT PVI'"
        "— heatmap kualitas visual dengan node fotogenik yang menampilkan "
        "skor indeks pada setiap titik survei."
    )

# ----------------------------------------------------------------------------
# TAB 2 — INDIKATOR VISUAL
# ----------------------------------------------------------------------------
with tab_indicators:
    c1, c2 = st.columns([1, 1])

    with c1:
        st.markdown("#### Profil Indikator Visual (rata-rata per koridor)")
        rows = []
        for corridor in sorted(df["corridor"].unique()):
            sub = df[df["corridor"] == corridor]
            for ind in INDICATORS:
                col = f"uvi::{ind}"
                if col in sub.columns:
                    rows.append({"corridor": corridor, "indikator": ind,
                                 "nilai": pd.to_numeric(sub[col], errors="coerce").mean()})
        radar_df = pd.DataFrame(rows)
        if not radar_df.empty:
            fig = go.Figure()
            for corridor in radar_df["corridor"].unique():
                sub = radar_df[radar_df["corridor"] == corridor]
                fig.add_trace(go.Scatterpolar(
                    r=sub["nilai"], theta=sub["indikator"], fill="toself", name=corridor,
                ))
            fig.update_layout(
                polar=dict(radialaxis=dict(visible=True, range=[0, max(0.05, radar_df['nilai'].max()*1.1)])),
                showlegend=True, height=460, margin=dict(t=20, b=20),
            )
            st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.markdown("#### Komposisi Elemen Visual (deteksi AI)")
        node_options = df["kode"] + " — " + df["corridor"]
        pick = st.selectbox("Pilih node untuk melihat breakdown kelas visual", node_options)
        sel_kode = pick.split(" — ")[0]
        node_row = df[df["kode"] == sel_kode].iloc[0]
        raw_cols = [c for c in df.columns if c.startswith("raw::")]
        pie_data = {c.replace("raw::", ""): node_row[c] for c in raw_cols if pd.notna(node_row[c])}
        if pie_data:
            pie_fig = px.pie(
                names=list(pie_data.keys()), values=list(pie_data.values()),
                hole=0.45, color_discrete_sequence=px.colors.sequential.Teal,
            )
            pie_fig.update_layout(height=460, margin=dict(t=10, b=10))
            st.plotly_chart(pie_fig, use_container_width=True)
        else:
            st.info("Tidak ada data breakdown kelas visual untuk node ini.")

    st.markdown("#### Distribusi Skor UVI per Indikator (seluruh node terpilih)")
    box_rows = []
    for ind in INDICATORS:
        col = f"uvi::{ind}"
        if col in df.columns:
            for v in pd.to_numeric(df[col], errors="coerce").dropna():
                box_rows.append({"indikator": ind, "nilai": v})
    box_df = pd.DataFrame(box_rows)
    if not box_df.empty:
        box_fig = px.box(box_df, x="indikator", y="nilai", color="indikator", points="all")
        box_fig.update_layout(showlegend=False, height=420)
        st.plotly_chart(box_fig, use_container_width=True)

# ----------------------------------------------------------------------------
# TAB 3 — SIMULASI SKENARIO
# ----------------------------------------------------------------------------
with tab_sim:
    st.markdown(
        "#### 🧮 Simulasi Skenario Penataan\n"
        "Ubah proporsi elemen visual pada sebuah node (mis. menambah vegetasi, "
        "mengurangi signage/kendaraan) untuk melihat dampaknya terhadap skor UVI — "
        "mensimulasikan modul *'perubahan parameter desain'*."
    )

    node_options2 = df["kode"] + " — " + df["corridor"]
    pick2 = st.selectbox("Pilih node yang akan disimulasikan", node_options2, key="sim_node")
    sel_kode2 = pick2.split(" — ")[0]
    base_row = df[df["kode"] == sel_kode2].iloc[0]

    base_vals = {ind: float(pd.to_numeric(base_row.get(f"uvi::{ind}", np.nan), errors="coerce") or 0)
                 for ind in INDICATORS}
    base_score = sum(base_vals[k] * weights.get(k, 1.0) for k in INDICATORS)
    base_score = base_score / sum(weights.values()) * UVI_SCALE if sum(weights.values()) else 0

    st.markdown("##### Kondisi Eksisting vs. Skenario")
    sim_cols = st.columns(4)
    sim_vals = {}
    for idx, ind in enumerate(INDICATORS):
        with sim_cols[idx % 4]:
            sim_vals[ind] = st.slider(
                ind, 0.0, 1.0, float(np.clip(base_vals[ind], 0, 1)), 0.01,
                key=f"sim_{ind}",
            )

    sim_score = sum(sim_vals[k] * weights.get(k, 1.0) for k in INDICATORS)
    sim_score = sim_score / sum(weights.values()) * UVI_SCALE if sum(weights.values()) else 0

    r1, r2, r3 = st.columns(3)
    r1.metric("UVI Eksisting", f"{base_score:.2f} / 10")
    r2.metric("UVI Skenario", f"{sim_score:.2f} / 10", delta=f"{sim_score - base_score:+.2f}")
    r3.metric("Perubahan", f"{((sim_score-base_score)/base_score*100 if base_score else 0):+.1f}%")

    comp_fig = go.Figure()
    comp_fig.add_trace(go.Scatterpolar(r=list(base_vals.values()), theta=INDICATORS,
                                        fill="toself", name="Eksisting"))
    comp_fig.add_trace(go.Scatterpolar(r=list(sim_vals.values()), theta=INDICATORS,
                                        fill="toself", name="Skenario"))
    comp_fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
                            height=460, margin=dict(t=20, b=20))
    st.plotly_chart(comp_fig, use_container_width=True)

    st.info(
        "Catatan metodologis: pada implementasi penuh, perubahan parameter desain "
        "ini idealnya ditarik balik dari citra tersimulasikan (mis. via image "
        "in-painting / GAN) lalu dievaluasi ulang oleh model AI computer-vision. "
        "Panel ini menyediakan "
        "simulasi tingkat-indeks (index-level) sebagai bukti-konsep antarmuka.",
        icon="ℹ️",
    )

# ----------------------------------------------------------------------------
# TAB 4 — PERBANDINGAN KORIDOR
# ----------------------------------------------------------------------------
with tab_compare:
    st.markdown("#### Peringkat Rata-rata UVI per Koridor")
    corr_avg = df.groupby("corridor")["UVI"].agg(["mean", "std", "count"]).reset_index()
    corr_avg = corr_avg.sort_values("mean", ascending=False)
    bar_fig = px.bar(
        corr_avg, x="corridor", y="mean", error_y="std",
        color="mean", color_continuous_scale="RdYlGn",
        labels={"mean": "Rata-rata UVI", "corridor": "Koridor"},
        text=corr_avg["mean"].round(2),
    )
    bar_fig.update_layout(height=440, coloraxis_showscale=False)
    st.plotly_chart(bar_fig, use_container_width=True)

    st.markdown("#### Sisi Jalan (Barat/Timur vs. Utara/Selatan) — Rata-rata UVI")
    side_avg = df.groupby(["corridor", "side"])["UVI"].mean().reset_index()
    side_fig = px.bar(side_avg, x="corridor", y="UVI", color="side", barmode="group")
    side_fig.update_layout(height=400)
    st.plotly_chart(side_fig, use_container_width=True)

    st.dataframe(
        corr_avg.rename(columns={"mean": "Rata-rata UVI", "std": "Std Dev", "count": "Jumlah Node"})
        .style.format({"Rata-rata UVI": "{:.2f}", "Std Dev": "{:.2f}"}),
        use_container_width=True, hide_index=True,
    )

# ----------------------------------------------------------------------------
# TAB 5 — POLICY BRIEF PERANCANGAN KOTA BERBASIS UVI
# ----------------------------------------------------------------------------
with tab_policy:
    st.markdown("### 🏙️ Policy Brief Perancangan Kota Berbasis UVI")
    st.caption(
        "Modul ini menerjemahkan hasil Urban Visual Index menjadi diagnosis, "
        "prioritas lokasi, dan arahan intervensi desain. UVI dipakai sebagai "
        "evidence base, bukan sebagai pengganti verifikasi lapangan dan desain teknis."
    )

    pb_scope = st.radio(
        "Cakupan policy brief",
        ["Koridor terpilih", "Seluruh koridor"],
        horizontal=True,
        key="pb_scope",
    )
    pb_df = df.copy() if pb_scope == "Koridor terpilih" else data_all.copy()
    if not pb_df.empty:
        pb_df["UVI"] = compute_uvi(pb_df, weights)

    corridor_pb, indicator_pb, node_pb = build_policy_brief(pb_df, weights)

    if corridor_pb.empty:
        st.info("Belum tersedia data UVI yang dapat diterjemahkan menjadi policy brief.")
    else:
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Koridor prioritas", str(corridor_pb.iloc[0]["Koridor"]))
        p2.metric("UVI terendah", f"{corridor_pb.iloc[0]['Rata-rata UVI']:.2f}")
        p3.metric("Indikator terendah", str(indicator_pb.iloc[0]["Indikator"]))
        p4.metric("Node prioritas", f"{len(node_pb)}")

        st.markdown("#### 1. Matriks prioritas koridor")
        st.dataframe(
            corridor_pb[["Koridor", "Rata-rata UVI", "Kategori", "Indikator Prioritas 1", "Nilai 1", "Indikator Prioritas 2", "Nilai 2", "Jumlah Node"]]
            .style.format({"Rata-rata UVI": "{:.2f}", "Nilai 1": "{:.3f}", "Nilai 2": "{:.3f}"}),
            use_container_width=True, hide_index=True,
        )

        st.markdown("#### 2. Diagnosis indikator → rekomendasi desain")
        st.dataframe(
            indicator_pb[["Indikator", "Rata-rata", "Prioritas", "Diagnosis", "Intervensi", "Arahan Desain"]]
            .style.format({"Rata-rata": "{:.3f}"}),
            use_container_width=True, hide_index=True,
        )

        st.markdown("#### 3. Rekomendasi kebijakan perancangan kota")
        st.markdown("""
**A. Koridor UVI rendah — intervensi prioritas**  
Fokuskan anggaran pada node dengan UVI < 5.00. Intervensi sebaiknya berbasis masalah dominan yang ditunjukkan indikator terendah, bukan sekadar beautifikasi.

**B. Ruang pejalan kaki dan complete street**  
Jika *Ground Accessibility* rendah, prioritaskan kontinuitas trotoar, universal access, penyeberangan, dan penghilangan hambatan.

**C. Green streets dan kenyamanan visual**  
Jika *Vegetation Coverage* rendah, prioritaskan canopy pohon, planting strip, pocket green, dan green buffer.

**D. Aktivasi ruang publik**  
Jika *Human Activity* rendah, arahkan desain pada active frontage, seating, ruang interaksi, pencahayaan, dan programming kegiatan.

**E. Manajemen kendaraan dan streetscape**  
Jika *Vehicle Intensity* menjadi indikator lemah, pertimbangkan traffic calming, manajemen parkir, redistribusi ruang jalan, dan prioritas pejalan kaki.

**F. Identitas kota dan heritage**  
Jika *Heritage Dominance* rendah pada koridor bersejarah, gunakan pedoman fasad, material, signage, pencahayaan, dan interpretasi heritage.
""")

        st.markdown("#### 4. Prioritas node untuk tindakan cepat")
        if not node_pb.empty:
            node_display = [c for c in ["corridor", "kode", "side", "lat", "lon", "UVI", "Prioritas UVI", "Prioritas"] if c in node_pb.columns]
            st.dataframe(
                node_pb[node_display].rename(columns={"corridor":"Koridor", "kode":"Node", "side":"Sisi", "lat":"Lat", "lon":"Lon", "UVI":"UVI"})
                .style.format({"UVI":"{:.2f}", "Lat":"{:.6f}", "Lon":"{:.6f}"}),
                use_container_width=True, hide_index=True,
            )
        else:
            st.success("Tidak terdapat node dengan UVI < 7 pada cakupan analisis ini.")

        st.markdown("#### 5. Policy brief siap unduh")
        pb_pdf = policy_brief_pdf_bytes(corridor_pb, indicator_pb, node_pb, pb_scope)
        if pb_pdf is not None:
            st.download_button(
                "⬇️ Unduh Policy Brief (PDF)",
                data=pb_pdf,
                file_name=f"policy_brief_UVI_Malang_{datetime.now():%Y%m%d}.pdf",
                mime="application/pdf",
                key="download_policy_brief_pdf",
            )
        else:
            st.error(
                "Generator PDF belum tersedia karena paket `reportlab` belum terpasang. "
                "Tambahkan `reportlab` ke requirements.txt lalu redeploy aplikasi.",
                icon="📄",
            )

        with st.expander("Versi editable (Word / .docx)", expanded=False):
            pb_docx = policy_brief_docx_bytes(corridor_pb, indicator_pb, node_pb, pb_scope)
            if pb_docx is not None:
                st.download_button(
                    "⬇️ Unduh Policy Brief (.docx)",
                    data=pb_docx,
                    file_name=f"policy_brief_UVI_Malang_{datetime.now():%Y%m%d}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key="download_policy_brief_docx",
                )
            else:
                st.error(
                    "Generator Word (.docx) belum tersedia karena paket `python-docx` belum "
                    "terpasang. Tambahkan `python-docx` ke requirements.txt lalu redeploy aplikasi.",
                    icon="📝",
                )

        st.info(
            "PDF digenerasikan langsung dari hasil UVI aktif pada dashboard. "
            "Untuk dokumen kebijakan final, hasil ini perlu dipadukan dengan survei lapangan, "
            "ketentuan tata ruang, standar teknis jalan/pedestrian, serta konsultasi pemangku kepentingan.",
            icon="ℹ️",
        )

# ----------------------------------------------------------------------------
# TAB 6 — TENTANG & METODOLOGI
# ----------------------------------------------------------------------------
with tab_about:
    st.markdown("### 📋 Tentang Platform")
    st.markdown(
        """
        **UVIP Malang** (*Urban Visual Intelligence Platform*) adalah piranti WebGIS Analytics
        *smart city* yang dikembangkan pada Penelitian Terapan
        *"Pengembangan Model Sistem Simulasi Visual Digital Ruang Terbuka Perkotaan
        Berbasis AI untuk Smart City Kota Malang"* 
        """
    )

    st.markdown(
        """
        **Kata kunci konteks:** Urban Visual Index · Street View Imagery ·
        Artificial Intelligence · Ruang Terbuka Kota · Smart City
        """
    )

    st.markdown("#### 🎯 Alur Metodologi yang Diadaptasi")
    st.markdown(
        """
        1. **Pengumpulan citra street-level** pada segmen koridor ruang terbuka
           (mis. Kayutangan, Tugu, Lafayette–PLN, Alun-Alun Merdeka).
        2. **Deteksi elemen visual berbasis AI** (semantic segmentation) menghasilkan
           proporsi kelas: *Ground, Building, Traffic sign, Vegetation, Sky, Human,
           Vehicle 4w/2w*.
        3. **Perhitungan 8 indikator visual komposit**: Building Visibility,
           Vegetation Coverage, Sky Openness, Ground Accessibility, Human Activity,
           Vehicle Intensity, Traffic Infrastructure, Heritage Dominance.
        4. **Pembobotan & agregasi** menjadi **Urban Visual Index (UVI)**.
        5. **Visualisasi & simulasi** pada dashboard: peta hotspot, radar indikator,
           simulasi what-if perubahan parameter visual, dan perbandingan antar
           koridor.
        """
    )

    st.markdown("---")
    st.markdown("#### 🧮 Formula Pembobotan & Agregasi menjadi Urban Visual Index (UVI)")
    st.markdown(
        """
        UVI adalah **indeks komposit** (*composite indicator*) yang dibentuk dari 8 indikator
        visual, mengikuti alur umum konstruksi indeks komposit: normalisasi → pembobotan →
        agregasi linear (OECD/EU-JRC, 2008).
        """
    )

    st.markdown("**Langkah 1 — Normalisasi indikator (dari deteksi AI ke skala 0–1)**")
    st.latex(r"""
        x_i \;=\; \frac{\displaystyle\sum_{c \,\in\, K_i} p_c}{100}\,, \qquad x_i \in [0,1]
    """)

    st.markdown("**Langkah 2 — Pembobotan (weighting)**")
    st.markdown(
        """
        Setiap indikator $i$ diberi bobot $w_i$ yang dapat diubah interaktif melalui panel
        *⚙️ Bobot Indikator UVI* pada sidebar (rentang $0 \\le w_i \\le 2$, default
        $w_i = 1$).
        """
    )

    st.markdown("**Langkah 3 — Agregasi linear terbobot**")
    st.latex(r"""
        \mathrm{UVI} \;=\; \left(\frac{\displaystyle\sum_{i=1}^{8} w_i \, x_i}
        {\displaystyle\sum_{i=1}^{8} w_i}\right) \times S\,, \qquad S = 10
    """)

    st.markdown("---")
    st.markdown("#### 📚 Sumber Referensi")
    st.markdown(
        """
        1. OECD/European Union/JRC-European Commission (2008). *Handbook on Constructing
           Composite Indicators: Methodology and User Guide*. OECD Publishing, Paris.
        2. Nardo, M., Saisana, M., Saltelli, A., & Tarantola, S. (2005). *Tools for
           Composite Indicators Building*. OECD Statistics Working Papers No. 2005/03.
        3. Saaty, T. L. (1980). *The Analytic Hierarchy Process*. McGraw-Hill, New York.
        4. Yang, J., Zhao, L., McBride, J., & Gong, P. (2009). Can you see green?
           *Landscape and Urban Planning*, 91(2), 97–104.
        5. Li, X., Zhang, C., Li, W., Ricard, R., Meng, Q., & Zhang, W. (2015). Assessing
           street-level urban greenery using Google Street View and a modified Green
           View Index. *Urban Forestry & Urban Greening*, 14(3), 675–685.
        6. Long, Y., & Liu, L. (2017). How green are the streets? *PLoS ONE*, 12(2), e0171110.
        7. Zhang, F., Zhou, B., Liu, L., Liu, Y., Fung, H. H., Lin, H., & Ratti, C.
           (2018). Measuring human perceptions of a large-scale urban region using
           machine learning. *Landscape and Urban Planning*, 180, 148–160.
        8. Ye, Y., Zeng, W., Shen, Q., Zhang, X., & Lu, Y. (2019). The visual quality of
           streets. *Environment and Planning B*, 46(8), 1439–1457.
        9. Gong, F.-Y., Zeng, Z.-C., Zhang, F., Li, X., Ng, E., & Norford, L. K. (2018).
           Mapping sky, tree, and building view factors of street canyons. *Building and
           Environment*, 134, 155–167.
        10. Biljecki, F., & Ito, K. (2023). Street view imagery in urban analytics and
            GIS: A review. *Landscape and Urban Planning*, 215, 104217.
        """
    )
    st.markdown("#### 👨‍🔬 Tim Peneliti UVIP Malang - UNIVERSITAS BRAWIJAYA")

    researchers = [
        {
            "name": "Dr. Herry Santosa",
            "image": "images/herry santosa.jpeg",
            "expertise": "Ahli *3D Building Digital* | *Digital Management Asset*",
            "role": "Ketua Tim Peneliti"
        },
        {
            "name": "Dr. Adipandang Yudono",
            "image": "images/Adipandang Yudono.jpg",
            "expertise": "Ahli *Spatial Data Science* | *GIS Programmer & Developer* | *Smart Cities* | *GeoAI* ",
            "role": "Anggota Tim Peneliti"
        },
        {
            "name": "Dr. Herman Tolle",
            "image": "images/herman tolle.png",
            "expertise": "Ahli Teknologi Informasi | Sistem Cerdas | *Human-Computer Interaction*",
            "role": "Anggota Tim Peneliti"
        },
        {
            "name": "Prof. Jenny Ernawati",
            "image": "images/jenny ernawati.jpeg",
            "expertise": "Ahli Arsitektur | Desain Perkotaan | Lingkungan Binaan",
            "role": "Anggota Tim Peneliti"
        },
        {
            "name": "Dr. Agung Setia Budi",
            "image": "images/Agung Setia Budi.jpg",
            "expertise": "Ahli Kecerdasan Buatan | *Computer Vision* | *Data Science*",
            "role": "Anggota Tim Peneliti"
        }
    ]

    cols = st.columns(len(researchers))
    for idx, (col, researcher) in enumerate(zip(cols, researchers)):
        with col:
            try:
                if os.path.exists(researcher["image"]):
                    img = Image.open(researcher["image"])
                    st.image(img, use_container_width=True)
                else:
                    st.markdown(
                        f"""
                        <div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);
                                    border-radius:10px;padding:20px;text-align:center;color:white;">
                            <div style="font-size:40px;">👤</div>
                            <div style="font-weight:bold;margin-top:10px;">
                                {researcher['name'].replace('Dr. ', '').replace('Prof. ', '')}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
            except Exception:
                st.markdown(
                    f"""
                    <div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);
                                border-radius:10px;padding:20px;text-align:center;color:white;">
                        <div style="font-size:40px;">👤</div>
                        <div style="font-weight:bold;margin-top:10px;">
                            {researcher['name'].replace('Dr. ', '').replace('Prof. ', '')}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

            st.markdown(f"**{researcher['name']}**")
            st.caption(f"*{researcher['role']}*")
            st.caption(f"📌 {researcher['expertise']}")

# ----------------------------------------------------------------------------
# TAB 7 — DATA & UNDUH
# ----------------------------------------------------------------------------
with tab_data:
    st.markdown("#### Tabel Data Node (hasil parsing dari Google Sheets)")
    display_cols = ["corridor", "side", "kode", "lat", "lon", "UVI"] + \
                    [c for c in df.columns if c.startswith("uvi::")]
    st.dataframe(df[display_cols].sort_values("UVI", ascending=False),
                 use_container_width=True, hide_index=True)

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Unduh data (CSV)", data=csv_bytes,
        file_name=f"uvip_malang_{datetime.now():%Y%m%d}.csv", mime="text/csv",
    )

st.markdown("---")
st.caption(
    "WebGIS Analytics dikembangkan oleh Tim UVIP Malang - Universitas Brawijaya "
    "(Dr. Herry Santosa, Dr. Adipandang Yudono, Dr. Herman Tolle, "
    "Prof. Jenny Ernawati, Dr. Agung Setia Budi)"
)
