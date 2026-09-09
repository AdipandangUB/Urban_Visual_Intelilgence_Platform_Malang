# -*- coding: utf-8 -*-
"""
UVIP Malang — Urban Visual Intelligence Platform
=================================================
Model Sistem Simulasi Visual Digital Ruang Terbuka Perkotaan Berbasis AI
untuk Smart City Kota Malang

Dikembangkan mengacu pada proposal Penelitian Terapan:
"Pengembangan Model Sistem Simulasi Visual Digital Ruang Terbuka Perkotaan
Berbasis AI untuk Smart City Kota Malang" dengan Peneliti Dr. Herry Santosa,Dr Adipandang Yudono,Dr Herman Tolle,Prof. Jenny Ernawati,
Dr. Agung Setia Budi - Universitas Brawijaya.

Konteks kunci yang diadaptasi ke dalam aplikasi ini:
- Urban Visual Index (UVI)      -> indeks komposit kualitas visual per titik/segmen
- Street View Imagery           -> data citra jalan (Google Street View / survei 360)
- Artificial Intelligence       -> hasil deteksi elemen visual (semantic segmentation)
- Ruang Terbuka Kota            -> unit analisis = koridor jalan / ruang terbuka
- Smart City                    -> dashboard WebGIS untuk pemantauan & simulasi kebijakan

Sumber data (Google Sheets hasil analisis AI computer-vision atas citra
street-level, disusun tim ):
  gid=1062597004, 446487299, 891123454, 1869212551, 385636298
(lihat CORRIDOR_SHEETS di bawah — silakan sesuaikan nama koridor jika urutan
tab pada spreadsheet Anda berbeda).

Menjalankan aplikasi:
    streamlit run app.py
"""

from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium

from uvip_core import (
    CORRIDOR_SHEETS, RAW_CLASSES, INDICATORS, DEFAULT_WEIGHTS, UVI_SCALE,
    sheet_csv_url, load_corridor, demo_corridor, compute_uvi,
)

# ----------------------------------------------------------------------------
# 0. KONFIGURASI DASAR
# ----------------------------------------------------------------------------

st.set_page_config(
    page_title="UVIP Malang — Urban Visual Intelligence Platform",
    page_icon="🏙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Bungkus fungsi murni dari uvip_core.py dengan cache Streamlit di sini, agar
# uvip_core.py tetap dapat diimpor & diuji tanpa dependensi Streamlit.
_load_corridor_cached = st.cache_data(ttl=3600, show_spinner=False)(load_corridor)


@st.cache_data(ttl=3600, show_spinner=False)
def load_all_corridors():
    frames = []
    used_demo = []
    for gid, meta in CORRIDOR_SHEETS.items():
        name = meta["name"]
        d = _load_corridor_cached(gid, name)
        if d.empty:
            d = demo_corridor(name)
            used_demo.append(name)
        frames.append(d)
    all_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return all_df, used_demo


def _sheet_csv_url(gid: str) -> str:
    return sheet_csv_url(gid)


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

corridor_names = sorted(data_all["corridor"].unique()) if not data_all.empty else []

# selected_corridors tetap berupa list agar logika filtering di bawah tetap kompatibel.
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
with st.sidebar.expander("Atur bobot 8 indikator visual", expanded=False):
    for ind in INDICATORS:
        weights[ind] = st.slider(ind, 0.0, 2.0, 1.0, 0.1, key=f"w_{ind}")

if st.sidebar.button("↺ Reset bobot ke default"):
    for ind in INDICATORS:
        st.session_state[f"w_{ind}"] = 1.0
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption(
    "Sumber data: hasil deteksi AI (computer vision) atas citra *street-level* "
    "koridor Kota Malang, diproses tim Peneliti UVIP (Urban Visual Intelligence Platform) Malang - Universitas Brawijaya. "
)

df = data_all[data_all["corridor"].isin(selected_corridors)].copy() if not data_all.empty else data_all
if not df.empty:
    df["UVI"] = compute_uvi(df, weights)

# ----------------------------------------------------------------------------
# 3. HEADER
# ----------------------------------------------------------------------------

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

tab_map, tab_indicators, tab_sim, tab_compare, tab_about, tab_data = st.tabs(
    ["🗺️ Peta Hotspot UVI", "📊 Indikator Visual", "🧮 Simulasi Skenario",
     "📈 Perbandingan Koridor", "📄 Tentang & Metodologi", "📥 Data & Unduh"]
)

# ----------------------------------------------------------------------------
# TAB 1 — PETA HOTSPOT (heatmap + node fotogenik, mengacu pada visual acuan)
# ----------------------------------------------------------------------------
with tab_map:
    left, right = st.columns([3, 1])
    with right:
        st.markdown("#### Legenda")
        st.caption("🔴 UVI tinggi (visual berkualitas) → 🔵 UVI rendah")
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
                    popup_html = (
                        f"<b>{row['kode']}</b> — {row['corridor']} ({row['side']})<br>"
                        f"UVI: <b>{row['UVI']:.2f}</b> / 10"
                    )
                    folium.CircleMarker(
                        location=[row["lat"], row["lon"]],
                        radius=9,
                        color=color,
                        fill=True,
                        fill_color=color,
                        fill_opacity=0.9,
                        popup=folium.Popup(popup_html, max_width=220),
                        tooltip=f"📷 {row['kode']} · {row['UVI']:.2f}",
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
        "Peta ini mensimulasikan luaran 'HASIL & LUARAN: PETA HOTSPOT PVI' pada "
        "proposal — heatmap kualitas visual dengan node fotogenik yang menampilkan "
        "skor indeks pada setiap titik survei."
    )

# ----------------------------------------------------------------------------
# TAB 2 — INDIKATOR VISUAL (radar + breakdown kelas piksel)
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
# TAB 3 — SIMULASI SKENARIO (what-if penataan ruang terbuka)
# ----------------------------------------------------------------------------
with tab_sim:
    st.markdown(
        "#### 🧮 Simulasi Skenario Penataan\n"
        "Ubah proporsi elemen visual pada sebuah node (mis. menambah vegetasi, "
        "mengurangi signage/kendaraan) untuk melihat dampaknya terhadap skor UVI — "
        "mensimulasikan modul *'perubahan parameter desain'* pada proposal."
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
        "in-painting / GAN) lalu dievaluasi ulang oleh model AI computer-vision, "
        "sesuai rancangan modul simulasi pada proposal. Panel ini menyediakan "
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
# TAB 5 — TENTANG & METODOLOGI
# ----------------------------------------------------------------------------
with tab_about:
    st.markdown(
        """
### Tentang Platform

**UVIP Malang** (*Urban Visual Intelligence Platform*) adalah piranti WebGIS Analytics
*smart city* yang dikembangkan pada Penelitian Terapan
*"Pengembangan Model Sistem Simulasi Visual Digital Ruang Terbuka Perkotaan
Berbasis AI untuk Smart City Kota Malang"* (
Tim: Dr. Herry Santosa, Dr. Adipandang Yudono, Dr. Herman Tolle, Prof. Jenny Ernawati, Dr. Agung
Setia Budi —  Universitas Brawijaya).

**Kata kunci konteks:** Urban Visual Index · Street View Imagery ·
Artificial Intelligence · Ruang Terbuka Kota · Smart City

#### Alur Metodologi yang Diadaptasi
1. **Pengumpulan citra street-level** pada segmen koridor ruang terbuka
   (mis. Kayutangan, Tugu, Lafayette–PLN, Alun-Alun Merdeka).
2. **Deteksi elemen visual berbasis AI** (semantic segmentation) menghasilkan
   proporsi kelas: *Ground, Building, Traffic sign, Vegetation, Sky, Human,
   Vehicle 4w/2w*.
3. **Perhitungan 8 indikator visual komposit**: Building Visibility,
   Vegetation Coverage, Sky Openness, Ground Accessibility, Human Activity,
   Vehicle Intensity, Traffic Infrastructure, Heritage Dominance.
4. **Pembobotan & agregasi** menjadi **Urban Visual Index (UVI)** — pada
   platform ini bobot dapat diubah interaktif untuk mensimulasikan skenario
   preferensi publik/kebijakan (adaptasi ringan dari pendekatan AHP pada
   proposal, yang di lapangan dikalibrasi melalui survei preferensi publik).
5. **Visualisasi & simulasi** pada dashboard: peta hotspot, radar indikator,
   simulasi what-if perubahan parameter visual, dan perbandingan antar
   koridor — sebagai *bukti konsep* modul dashboard yang diusulkan.

#### Keterbatasan Prototipe
- Bobot UVI pada prototipe ini bersifat *user-adjustable* untuk kebutuhan
  demonstrasi; kalibrasi final memerlukan survei persepsi publik (≥200
  responden, sesuai target proposal) dan analisis statistik lanjutan.
- Data ditarik langsung dari Google Sheets hasil kerja tim (bukan pipeline
  model AI *end-to-end* yang berjalan otomatis pada citra baru); integrasi
  model CNN/ViT untuk skoring citra baru merupakan pengembangan lanjutan
  sesuai roadmap penelitian 2025–2027 (Gambar Road Map proposal).
- Simulasi skenario pada Tab 3 bekerja pada level indeks, bukan pada level
  regenerasi citra.

# ----------------------------------------------------------------------------
# TAB 6 — DATA & UNDUH
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
    st.markdown("#### Sumber Data Google Sheets")
    for gid, meta in CORRIDOR_SHEETS.items():
        st.markdown(f"- **{meta['name']}** — [buka spreadsheet]({_sheet_csv_url(gid).split('/export')[0]}/edit?gid={gid})")

st.markdown("---")
st.caption(
    "WebGIS Analytics dikembangkan oleh Tim UVIP MAlang - Universitas Brawijaya "
)
