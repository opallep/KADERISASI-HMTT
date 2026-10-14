# Web Cek Kelulusan Kaderisasi HMTT 2026

Stack: **Flask (Python)** + **SQLite** + HTML/CSS/JS. Data NIM kelulusan disinkronkan otomatis dari Google Spreadsheet.

## Menjalankan

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

## Deploy ke Render (gratis)

1. Push repo ini ke GitHub.
2. Buka https://dashboard.render.com → **New → Blueprint** → pilih repo ini (`render.yaml` terbaca otomatis).
3. Isi `ASTERISK_ADMIN_PASS` (password admin) dan `ASTERISK_CERT_DEFAULT` (link e-certificate umum, boleh kosong) → **Apply**.
4. Web online di `https://kaderisasi-hmtt.onrender.com` (nama bisa berbeda).

Catatan paket gratis Render:
- Server tidur setelah 15 menit tanpa pengunjung; akses pertama setelahnya butuh ±30–60 detik.
- Disk tidak permanen: data NIM aman karena selalu diambil ulang dari Google Sheet, tetapi
  perubahan lewat panel admin (tambah manual, import CSV, pengaturan) hilang saat server restart/deploy ulang.
  Simpan link e-certificate umum di environment variable `ASTERISK_CERT_DEFAULT`.
