# Tahap 8 - Audit cakupan data makanan Indonesia (batch 2)

Tanggal audit: 17 September 2026.

## Sumber dan metode

Sumber utama adalah **Tabel Komposisi Pangan Indonesia (TKPI) 2020**, Kementerian
Kesehatan RI, ISBN 9786233010368, dari halaman resmi
`https://repository.kemkes.go.id/book/668`. PDF resmi merupakan scan. Setiap
baris di bawah diperiksa langsung dari render beresolusi tinggi; tidak ada nilai
yang diambil dari ingatan, prediksi model, atau kecocokan nama yang tidak persis.

Semua angka adalah per 100 gram BDD (bagian yang dapat dimakan). Nilai gula
tambahan tidak tersedia dalam tabel sumber dan karena itu tetap `null`. BDD
dicatat sebagai provenance, tetapi tidak diterapkan lagi pada gram makanan siap
makan agar porsi edible tidak dikoreksi dua kali.

## Record baru yang dipromosikan sebagai verified

| ID | Nama sumber | Kode | Halaman cetak/PDF | kcal | Protein | Karbo | Lemak | Serat | Sodium |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| kangkung_segar | Kangkung, segar | DR100 | 35/39 | 28 | 3,4 g | 3,9 g | 0,7 g | 2,0 g | - |
| sawi_segar | Sawi, segar | DR141 | 37/41 | 28 | 2,3 g | 4,0 g | 0,3 g | 1,7 g | - |
| wortel_segar | Wortel, segar | DR166 | 38/42 | 36 | 1,0 g | 7,9 g | 0,6 g | 1,0 g | 70 mg |
| gado_gado | Gado-gado | DP031 | 40/44 | 137 | 6,1 g | 21,0 g | 3,2 g | 1,1 g | - |
| apel_segar | Apel, segar | ER004 | 42/46 | 58 | 0,3 g | 14,9 g | 0,4 g | 2,6 g | 2 mg |
| jeruk_manis | Jeruk manis, segar | ER039 | 43/47 | 45 | 0,9 g | 11,2 g | 0,2 g | 1,4 g | 1 mg |
| pepaya_segar | Pepaya, segar | ER073 | 45/49 | 46 | 0,5 g | 12,2 g | 0,1 g | 1,6 g | 4 mg |
| semangka_segar | Semangka, segar | ER115 | 47/51 | 28 | 0,5 g | 6,9 g | 0,2 g | 0,4 g | 7 mg |

Tanda `-` berarti sel sumber tidak menyediakan nilai yang cukup jelas; database
menyimpannya sebagai kosong, bukan nol.

## Record lama yang diperkaya tanpa mengubah nilai lama

Kalori, protein, karbohidrat, dan lemak lama tidak diubah. Kolom resmi yang
ditambahkan adalah:

- Nasi putih AP001: serat 0,4 g; sodium 1 mg.
- Kol DR114: serat 1,5 g.
- Tempe goreng CP076: serat 4,2 g.
- Tahu goreng CP062: serat 0,1 g.
- Timun DR109: serat 0,3 g.

## Keputusan keselamatan pencocokan

- `kangkung`, `sawi`, dan `wortel` tanpa keterangan tidak diarahkan ke record
  mentah/segar karena makanan pada foto bisa sudah dimasak.
- `apel`, `jeruk`, `pepaya`, dan `semangka` boleh mengarah ke entri segar karena
  nama tersebut secara umum menunjuk buah yang dimakan segar, dan entri sumber
  tidak mengunci varietas khusus (kecuali jeruk yang eksplisit jeruk manis).
- Gado-gado memiliki entri hidangan persis di TKPI sehingga tidak membutuhkan
  resep buatan.
- Soto ayam, bakso, mie ayam, ketoprak, pecel lele, nasi uduk, dan nasi padang
  belum ditambahkan dalam batch ini. Nama yang sama dapat memiliki resep dan
  komposisi sangat berbeda; record hanya akan ditambahkan bila ditemukan
  padanan resmi yang identitasnya persis.
- Nilai gula tambahan tetap kosong. TKPI 2020 tidak memiliki kolom gula
  tambahan, sehingga gula total atau karbohidrat tidak boleh dipakai sebagai
  penggantinya.
