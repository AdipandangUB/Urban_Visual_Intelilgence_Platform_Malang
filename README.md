# UVIP Malang — Urban Visual Intelligence Platform

Prototipe dashboard **Streamlit** untuk Model Sistem Simulasi Visual Digital
Ruang Terbuka Perkotaan Berbasis AI, Smart City Kota Malang — mengacu pada
proposal Penelitian Terapan (Santosa, Yudono, Tolle, Ernawati, Setia Budi —
EIIS Lab, PWK Universitas Brawijaya).

Konteks: **Urban Visual Index · Street View Imagery · Artificial
Intelligence · Ruang Terbuka Kota · Smart City**

## 1. Jalankan secara lokal

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 2. Deploy ke Streamlit Community Cloud

1. Push folder ini (`app.py`, `requirements.txt`) ke repository GitHub.
2. Buka [streamlit.io/cloud](https://streamlit.io/cloud) → **New app** →
   pilih repo & branch → *Main file path*: `app.py` → **Deploy**.
3. Tidak perlu secrets/API key — aplikasi membaca data langsung dari Google
   Sheets publik.

## 3. Menyiapkan sumber data (WAJIB)

Aplikasi mengambil data dari 5 tab (gid) pada satu Google Spreadsheet
"HASIL ANALISIS PKM":

| gid | Koridor (default) |
|---|---|
| 1062597004 | Tugu |
| 446487299 | Kayutangan–Tugu |
| 891123454 | Lafayette–PLN |
| 1869212551 | Kayutangan 1 |
| 385636298 | Alun-Alun Merdeka |

**Agar data ini bisa terbaca oleh `app.py`, spreadsheet harus dibagikan
sebagai "Anyone with the link — Viewer"** (Share → General access → Anyone
with the link). Jika belum publik, aplikasi otomatis menampilkan **data demo
sintetis** per koridor (dengan peringatan di sidebar) agar dashboard tetap
bisa dijalankan/diuji.

Jika urutan tab pada spreadsheet Anda berbeda dari tabel di atas, cukup ubah
nilai `"name"` pada dictionary `CORRIDOR_SHEETS` di awal `app.py` — struktur
kolom (paired-block per 8 baris/indikator) tetap sama untuk semua tab.

Untuk menambah koridor baru: tambahkan pasangan `gid: {"name": "..."}` baru
ke `CORRIDOR_SHEETS`.

## 4. Struktur data yang diasumsikan

Setiap tab berisi dua blok kolom berdampingan (sisi kiri = "Sisi A", sisi
kanan = "Sisi B" jalan/koridor), masing-masing node menempati 8 baris
berurutan (satu baris per indikator visual, indeks 0–7):

```
KODE | FOTO | LAT | LONG | HASIL ANALISIS (nama,nilai%) |
COMPOSITE VISUAL INDICATORS (nama,nilai%) | index | nama_dup | nilai_0-1 |
COMPOSITE VISUAL INDICES (nama "... Index", nilai_0-1)
```

Parser (`_parse_block` di `app.py`) membaca layout ini menjadi tabel *tidy*
per node dengan kolom `raw::<kelas>`, `pct::<indikator>`, `uvi::<indikator>`.

## 5. Fitur dashboard

- 🗺️ **Peta Hotspot UVI** — heatmap + node "fotogenik" bernomor skor (mirip
  luaran *Peta Hotspot PVI* pada proposal), basemap gelap, popup per node.
- 📊 **Indikator Visual** — radar profil 8 indikator per koridor, pie
  breakdown kelas visual per node, boxplot sebaran skor.
- 🧮 **Simulasi Skenario** — slider *what-if* untuk menguji dampak perubahan
  proporsi elemen visual (mis. menambah vegetasi) terhadap skor UVI.
- 📈 **Perbandingan Koridor** — ranking rata-rata UVI antar koridor & sisi
  jalan.
- 📄 **Tentang & Metodologi** — ringkasan metodologi dan keterbatasan
  prototipe.
- 📥 **Data & Unduh** — tabel lengkap + unduh CSV.

## 6. Pengembangan lanjutan (sesuai roadmap proposal)

- Integrasi model AI (CNN/ViT) untuk skoring citra baru secara otomatis
  (saat ini nilai indikator diambil dari hasil analisis yang sudah ada di
  spreadsheet, bukan inferensi model real-time).
- Kalibrasi bobot UVI dari survei preferensi publik (≥200 responden).
- Modul ekspor *feasibility study* & integrasi ke ekosistem smart city
  Pemkot Malang.
