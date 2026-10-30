# Web Cek Kelulusan Kaderisasi HMTT 2026

Portal resmi: https://kaderisasi-hmtt.fhanmazaya.my.id/

Backend menggunakan **Flask + SQLite** (`app.py`) dengan panel admin dan sinkronisasi data kelulusan otomatis.
Seluruh e-certificate peserta lulus disajikan secara tervalidasi dari direktori privat server (`sertifikat_pdf/<nim>.pdf`) yang tidak diekspos sebagai aset statis publik.

## Menjalankan Secara Lokal

```bash
pip install -r requirements.txt
python app.py
```

- Halaman peserta: http://localhost:5000
- Panel admin: http://localhost:5000/admin/login

## Konfigurasi (Environment Variable)

| Variabel | Fungsi |
|---|---|
| `ASTERISK_ADMIN_USER` / `ASTERISK_ADMIN_PASS` | Kredensial login admin |
| `ASTERISK_SECRET` | Secret key session |
| `ASTERISK_SHEET_URL` | Link spreadsheet sinkronisasi |
| `ASTERISK_CERT_DEFAULT` | Link e-certificate umum (opsional) |
| `ASTERISK_SYNC_INTERVAL` | Interval sinkron dalam detik (default 300) |
| `ASTERISK_DB` | Lokasi file database |
| `PORT` | Port server (default 5000) |
