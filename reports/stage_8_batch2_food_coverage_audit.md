# Tahap 8 Batch 2 - Audit cakupan data makanan

Tanggal audit: 17 September 2026.

## Sumber

Semua record baru pada batch ini dibaca langsung dari PDF resmi **Tabel
Komposisi Pangan Indonesia (TKPI) 2020**, Kementerian Kesehatan RI,
[repository.kemkes.go.id/book/668](https://repository.kemkes.go.id/book/668).
Nilai dicatat per 100 g BDD (bagian yang dapat dimakan). TKPI tidak menyediakan
kolom gula tambahan, sehingga `added_sugar_per_100g` tetap kosong. Kolom total
gula juga belum dimodelkan di NutruFood; tidak ada nilai total gula yang
ditambahkan sebagai pengganti.

## Record baru yang diverifikasi

| ID | Nama sumber | Kode | Halaman cetak | kcal | Protein | KH | Lemak | Serat | Sodium |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| bayam_segar | Bayam, segar | DR008 | 30 | 16 | 0,9 g | 2,9 g | 0,4 g | 0,7 g | - |
| buncis_segar | Buncis, segar | DR013 | 30 | 34 | 2,4 g | 6,6 g | 0,3 g | 3,4 g | - |
| tauge_segar | Taoge, segar | DR148 | 37 | 34 | 1,7 g | 6,4 g | 0,2 g | 1,1 g | - |
| tomat_merah_segar | Tomat merah, segar | DR161 | 38 | 24 | 1,1 g | 4,7 g | 0,5 g | 1,5 g | - |
| ubi_jalar_kuning_segar | Ubi jalar, kuning, segar | BR028 | 17 | 119 | 0,5 g | 25,1 g | 0,4 g | 4,2 g | - |
| mangga_segar | Mangga, segar | ER054 | 44 | 52 | 0,7 g | 12,3 g | 0,0 g | 1,8 g | 3 mg |
| melon_segar | Melon, segar | ER067 | 45 | 37 | 0,6 g | 7,4 g | 0,4 g | 1,0 g | - |

Tanda `-` berarti sel sodium pada baris sumber tidak cukup jelas untuk dicatat
dengan aman. Nilai yang kosong sengaja tidak digunakan untuk warning/saran.

## Data yang tidak ditambahkan

- Ayam bakar/rebus, telur ceplok/dadar, ikan goreng/bakar: record umum akan
  mencampur potongan, kulit, minyak, dan resep yang berbeda; tidak ada padanan
  TKPI yang cukup identik untuk deteksi polos.
- Kentang, singkong, roti tawar, bihun, mie: kandidat yang diperiksa tidak
  cocok secara persis dengan bentuk siap makan yang diminta atau belum diaudit
  langsung.
- Alpukat: belum ditemukan baris TKPI yang identitas dan preparasinya cukup
  jelas pada audit ini.
- Minuman, kecap, saus, dan makanan bermerek: komposisi sangat bergantung
  merek/resep; tidak dibuat satu angka umum.

## Cakupan setelah batch

Angka ini adalah cakupan record database, bukan persentase seluruh makanan yang
dimakan masyarakat Indonesia.

- Total record: **27** (sebelumnya 20; bertambah 35%).
- Record verified: **21** (sebelumnya 14; bertambah 50%).
- Di antara record verified: kalori, protein, karbohidrat, dan lemak tersedia
  pada **21/21 (100%)**; serat **20/21 (95%)** (selada lama belum memiliki
  nilai serat yang cukup jelas); sodium **7/21 (33%)**;
  total sugar **0/21 (0%)**; added sugar **0/21 (0%)**.
- Record provisional lama dipertahankan dan tidak dihitung sebagai data
  verified pada total nutrisi user-facing.

Tidak ada nilai pada record lama yang diubah dalam batch ini.
