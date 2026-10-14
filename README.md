# Web Cek Kelulusan Kaderisasi HMTT 2026

Data NIM kelulusan diambil langsung dari Google Spreadsheet. Ada dua versi:

- **`docs/` — versi GitHub Pages (statis, yang dipakai online).** Browser peserta membaca Google Sheet langsung.
  Pengaturan (link e-certificate) ada di [`docs/config.js`](docs/config.js).
- **Root (`app.py`) — versi Flask + SQLite** dengan panel admin, untuk dijalankan di server sendiri.

## GitHub Pages

Web online: https://opallep.github.io/KADERISASI-HMTT/

- Tambah / hapus NIM: cukup edit Google Sheet (perubahan muncul dalam beberapa menit).
- **E-certificate otomatis:** peserta lulus yang namanya ada di `docs/peserta.js` mendapat tombol
  *Download E-Certificate*; PDF (nama + NIM) dibuat langsung di browser dari `docs/sertifikat/template.pdf`
  (font Lora Bold & Poppins Medium, library pdf-lib di `docs/vendor/`). Atur di `docs/sertifikat.js`.
- Link e-certificate manual (opsional, menggantikan sertifikat otomatis): edit `docs/config.js` → commit.
- Nama peserta (ditampilkan saat lulus): `docs/peserta.js`. Salinan CSV `peserta_lulus.csv` bisa di-import ke versi Flask.
- Branch `gh-pages` adalah salinan folder `docs/`; perbarui dengan
  `git subtree split --prefix docs -b gh-pages && git push -f origin gh-pages`,
  atau ubah sumber Pages ke `main` / `docs` di **Settings → Pages** agar cukup push ke `main`.

## Versi Flask

### Menjalankan

```bash
pip install -r requirements.txt
python app.py
```

- Halaman peserta: http://localhost:5000
- Panel admin: http://localhost:5000/admin/login (default `admin` / `asterisk2026`)

## Sumber data

- **Google Spreadsheet** (default: sheet KELULUSAN SCHOLARSHIP 2026). Semua sel berisi angka NIM (8–15 digit)
  dibaca otomatis, tata letaknya bebas. Sinkron saat server start, tiap 5 menit, atau lewat tombol
  *Sinkron Sekarang* di admin. Spreadsheet harus di-share **Anyone with the link → Viewer**.
- NIM yang dihapus dari spreadsheet akan ikut terhapus dari web saat sinkron berikutnya.
- Peserta juga bisa ditambah manual / via import CSV (`nim,nama,sertifikat`, lihat `contoh_data.csv`).

## E-Certificate

- **Link umum** (diatur di admin) dipakai untuk semua peserta yang tidak punya link sendiri.
  Tulisan `{nim}` di link akan diganti NIM peserta.
- **Link per peserta** bisa diisi lewat Edit atau import CSV.

## Konfigurasi (environment variable)

| Variabel | Fungsi |
|---|---|
| `ASTERISK_ADMIN_USER` / `ASTERISK_ADMIN_PASS` | Login admin — **wajib diganti saat online** |
| `ASTERISK_SECRET` | Secret key session — **wajib diganti saat online** |
| `ASTERISK_SHEET_URL` | Link spreadsheet awal |
| `ASTERISK_CERT_DEFAULT` | Link e-certificate umum awal |
| `ASTERISK_SYNC_INTERVAL` | Interval sinkron dalam detik (default 300) |
| `ASTERISK_DB` | Lokasi file database |
| `PORT` | Port server (default 5000) |

## Deploy ke Vercel (gratis, tanpa kartu kredit) — direkomendasikan

1. Login https://vercel.com dengan akun GitHub → **Add New → Project** → import repo ini.
2. Di **Environment Variables** isi:
   - `ASTERISK_ADMIN_PASS` — password admin
   - `ASTERISK_SECRET` — teks acak panjang (secret session)
   - `ASTERISK_CERT_DEFAULT` — link e-certificate umum (opsional)
3. Klik **Deploy**. Setiap push ke `main` otomatis di-deploy ulang.

Catatan: database di Vercel bersifat sementara (`/tmp`), jadi kelola NIM lewat Google Sheet dan link
sertifikat lewat `ASTERISK_CERT_DEFAULT`; perubahan dari panel admin tidak permanen.

## Deploy ke Render (alternatif)

1. Push repo ini ke GitHub.
2. Buka https://dashboard.render.com → **New → Blueprint** → pilih repo ini (`render.yaml` terbaca otomatis).
   Blueprint meminta kartu kredit; tanpa kartu pakai **New → Web Service**, instance *Free*, start command
   `gunicorn app:app --bind 0.0.0.0:$PORT`, lalu isi environment variable secara manual.
3. Isi `ASTERISK_ADMIN_PASS` (password admin) dan `ASTERISK_CERT_DEFAULT` (link e-certificate umum, boleh kosong) → **Apply**.
4. Web online di `https://kaderisasi-hmtt.onrender.com` (nama bisa berbeda).

Catatan paket gratis Render:
- Server tidur setelah 15 menit tanpa pengunjung; akses pertama setelahnya butuh ±30–60 detik.
- Disk tidak permanen: data NIM aman karena selalu diambil ulang dari Google Sheet, tetapi
  perubahan lewat panel admin (tambah manual, import CSV, pengaturan) hilang saat server restart/deploy ulang.
  Simpan link e-certificate umum di environment variable `ASTERISK_CERT_DEFAULT`.
