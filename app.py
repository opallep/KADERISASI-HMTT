"""
ASTERISK - Web Pengecekan Kelulusan Kaderisasi
Backend: Flask + SQLite, data kelulusan disinkronkan dari Google Sheets
"""
import csv
import io
import os
import re
import sqlite3
import threading
import time
import urllib.request
from functools import wraps

from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Di Vercel hanya /tmp yang bisa ditulis
DB_PATH = os.environ.get(
    "ASTERISK_DB",
    "/tmp/asterisk.db" if os.environ.get("VERCEL") else os.path.join(BASE_DIR, "asterisk.db"),
)

app = Flask(__name__)
app.secret_key = os.environ.get("ASTERISK_SECRET", "ganti-secret-key-ini")
app.config["TEMPLATES_AUTO_RELOAD"] = True  # perubahan HTML langsung terlihat tanpa restart
ADMIN_USERNAME = os.environ.get("ASTERISK_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ASTERISK_ADMIN_PASS", "asterisk2026")

DEFAULT_SHEET_URL = os.environ.get(
    "ASTERISK_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/1sOJ2-r1G0W9Rhq_6RZRy1Equln1u6VcRG1klr5SP6A4/edit?gid=0#gid=0",
)
SYNC_INTERVAL = int(os.environ.get("ASTERISK_SYNC_INTERVAL", 300))  # detik
RETRY_AFTER = 20  # detik, jeda coba ulang jika sinkron gagal

NIM_PATTERN = re.compile(r"^[A-Za-z0-9.\-]{3,30}$")
SHEET_NIM_PATTERN = re.compile(r"^\d{8,15}$")  # sel spreadsheet yang dianggap NIM

_sync_lock = threading.Lock()


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS peserta (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nim         TEXT NOT NULL UNIQUE,
                nama        TEXT NOT NULL DEFAULT '',
                sertifikat  TEXT NOT NULL DEFAULT '',
                sumber      TEXT NOT NULL DEFAULT 'manual',  -- 'sheet' | 'manual'
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        db.execute("INSERT OR IGNORE INTO settings VALUES ('sheet_url', ?)", (DEFAULT_SHEET_URL,))
        db.execute("INSERT OR IGNORE INTO settings VALUES ('sertifikat_default', ?)",
                   (os.environ.get("ASTERISK_CERT_DEFAULT", ""),))


def get_setting(db, key, default=""):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(db, key, value):
    db.execute(
        "INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def normalize_nim(nim):
    return (nim or "").strip().upper()


def valid_url(url):
    return url.startswith("http://") or url.startswith("https://")


# ---------------------------------------------------------------- google sheets sync
def sheet_csv_url(url):
    """Ubah link Google Sheets biasa (/edit?gid=..) menjadi link export CSV."""
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url)
    if not m:
        return url  # anggap sudah berupa link CSV langsung
    gid = re.search(r"[?#&]gid=(\d+)", url)
    return (f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
            f"&gid={gid.group(1) if gid else 0}")


def fetch_sheet_nims(url, attempts=3):
    """Ambil semua sel berisi angka NIM dari spreadsheet (tata letak bebas)."""
    for i in range(attempts):
        try:
            with urllib.request.urlopen(sheet_csv_url(url), timeout=10) as resp:
                if "text/csv" not in resp.headers.get("Content-Type", ""):
                    raise RuntimeError("Spreadsheet tidak publik. Set share ke 'Anyone with the link'.")
                text = resp.read().decode("utf-8-sig", errors="replace")
            break
        except OSError:  # timeout / koneksi putus: coba lagi
            if i == attempts - 1:
                raise
            time.sleep(1)
    nims = set()
    for row in csv.reader(io.StringIO(text)):
        for cell in row:
            cell = cell.strip()
            if SHEET_NIM_PATTERN.match(cell):
                nims.add(cell)
    return nims


def sync_from_sheet(force=False):
    """Sinkronkan NIM dari Google Sheet ke database. Return (ok, pesan)."""
    # Tunggu sinkron yang sedang berjalan agar pengecekan tidak membaca database yang masih kosong
    if not _sync_lock.acquire(timeout=30):
        return False, "Sinkronisasi sedang berjalan."
    try:
        with sqlite3.connect(DB_PATH) as db:
            last = float(get_setting(db, "last_sync", "0") or 0)
            if not force and time.time() - last < SYNC_INTERVAL:
                return True, "Data masih baru."
            url = get_setting(db, "sheet_url")
            if not url:
                return False, "Link spreadsheet belum diatur."
            # Jika gagal: pakai data lama dan coba lagi setelah RETRY_AFTER detik
            retry_mark = str(time.time() - SYNC_INTERVAL + RETRY_AFTER)
            try:
                nims = fetch_sheet_nims(url)
            except Exception as exc:
                set_setting(db, "last_sync", retry_mark)
                set_setting(db, "last_sync_status", f"Gagal: {exc}")
                return False, f"Gagal sinkron: {exc}"
            if not nims:
                set_setting(db, "last_sync", retry_mark)
                set_setting(db, "last_sync_status", "Gagal: tidak ada NIM ditemukan di spreadsheet")
                return False, "Tidak ada NIM ditemukan di spreadsheet, data lama dipertahankan."

            existing = {r[0] for r in db.execute("SELECT nim FROM peserta WHERE sumber = 'sheet'")}
            removed = existing - nims
            db.executemany("DELETE FROM peserta WHERE nim = ? AND sumber = 'sheet'",
                           [(n,) for n in removed])
            db.executemany("INSERT OR IGNORE INTO peserta (nim, sumber) VALUES (?, 'sheet')",
                           [(n,) for n in nims])
            msg = (f"{len(nims)} NIM dari spreadsheet "
                   f"(+{len(nims - existing)} baru, -{len(removed)} dihapus)")
            set_setting(db, "last_sync", str(time.time()))
            set_setting(db, "last_sync_status", f"Berhasil {time.strftime('%d-%m-%Y %H:%M')}: {msg}")
            return True, f"Sinkron berhasil: {msg}."
    finally:
        _sync_lock.release()


# ---------------------------------------------------------------- auth
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------- public
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/cek", methods=["POST"])
def api_cek():
    data = request.get_json(silent=True) or {}
    nim = normalize_nim(data.get("nim"))

    if not nim:
        return jsonify(ok=False, message="NIM tidak boleh kosong."), 400
    if not NIM_PATTERN.match(nim):
        return jsonify(ok=False, message="Format NIM tidak valid."), 400

    sync_from_sheet()  # hanya benar-benar mengambil data jika sudah lewat SYNC_INTERVAL

    db = get_db()
    # Database kosong (sinkron pertama gagal): jangan sampai peserta dinyatakan tidak lulus
    if db.execute("SELECT COUNT(*) FROM peserta").fetchone()[0] == 0:
        return jsonify(ok=False, message="Data kelulusan sedang dimuat. Coba lagi dalam beberapa detik."), 503

    row = db.execute("SELECT nim, nama, sertifikat FROM peserta WHERE nim = ?", (nim,)).fetchone()

    if row:
        sertifikat = row["sertifikat"] or get_setting(db, "sertifikat_default").replace("{nim}", row["nim"])
        return jsonify(
            ok=True,
            lulus=True,
            message="SELAMAT ANDA LULUS KADERISASI",
            nim=row["nim"],
            nama=row["nama"],
            sertifikat=sertifikat,
        )
    return jsonify(
        ok=True,
        lulus=False,
        message="MAAF ANDA BELUM LULUS KADERISASI",
        nim=nim,
    )


# ---------------------------------------------------------------- admin
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if (request.form.get("username") == ADMIN_USERNAME
                and request.form.get("password") == ADMIN_PASSWORD):
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("Username atau password salah.", "error")
    return render_template("login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin")
@login_required
def admin():
    q = request.args.get("q", "").strip()
    db = get_db()
    if q:
        rows = db.execute(
            "SELECT * FROM peserta WHERE nim LIKE ? OR nama LIKE ? ORDER BY nim",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM peserta ORDER BY nim").fetchall()
    total = db.execute("SELECT COUNT(*) FROM peserta").fetchone()[0]
    return render_template(
        "admin.html", rows=rows, total=total, q=q,
        sheet_url=get_setting(db, "sheet_url"),
        sertifikat_default=get_setting(db, "sertifikat_default"),
        sync_status=get_setting(db, "last_sync_status", "Belum pernah sinkron"),
    )


@app.route("/admin/sync", methods=["POST"])
@login_required
def admin_sync():
    ok, msg = sync_from_sheet(force=True)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("admin"))


@app.route("/admin/pengaturan", methods=["POST"])
@login_required
def admin_pengaturan():
    sheet_url = request.form.get("sheet_url", "").strip()
    default = request.form.get("sertifikat_default", "").strip()
    if (sheet_url and not valid_url(sheet_url)) or (default and not valid_url(default)):
        flash("Link harus diawali http:// atau https://", "error")
        return redirect(url_for("admin"))
    db = get_db()
    set_setting(db, "sheet_url", sheet_url)
    set_setting(db, "sertifikat_default", default)
    db.commit()
    flash("Pengaturan disimpan.", "success")
    return redirect(url_for("admin"))


def _read_form():
    nim = normalize_nim(request.form.get("nim"))
    nama = request.form.get("nama", "").strip()
    sertifikat = request.form.get("sertifikat", "").strip()
    if not nim or not NIM_PATTERN.match(nim):
        return None, "Format NIM tidak valid."
    if sertifikat and not valid_url(sertifikat):
        return None, "Link sertifikat harus diawali http:// atau https://"
    return (nim, nama, sertifikat), None


@app.route("/admin/tambah", methods=["POST"])
@login_required
def admin_tambah():
    data, err = _read_form()
    if err:
        flash(err, "error")
        return redirect(url_for("admin"))
    try:
        db = get_db()
        db.execute("INSERT INTO peserta (nim, nama, sertifikat) VALUES (?, ?, ?)", data)
        db.commit()
        flash(f"Peserta {data[0]} berhasil ditambahkan.", "success")
    except sqlite3.IntegrityError:
        flash(f"NIM {data[0]} sudah terdaftar.", "error")
    return redirect(url_for("admin"))


@app.route("/admin/edit/<int:pid>", methods=["POST"])
@login_required
def admin_edit(pid):
    data, err = _read_form()
    if err:
        flash(err, "error")
        return redirect(url_for("admin"))
    nim, nama, sertifikat = data
    db = get_db()
    old = db.execute("SELECT nim FROM peserta WHERE id = ?", (pid,)).fetchone()
    try:
        # NIM diubah -> tidak lagi terikat ke spreadsheet
        sumber_sql = ", sumber = 'manual'" if old and old["nim"] != nim else ""
        db.execute(
            f"UPDATE peserta SET nim = ?, nama = ?, sertifikat = ?{sumber_sql} WHERE id = ?",
            (nim, nama, sertifikat, pid),
        )
        db.commit()
        flash(f"Data {nim} berhasil diperbarui.", "success")
    except sqlite3.IntegrityError:
        flash(f"NIM {nim} sudah dipakai peserta lain.", "error")
    return redirect(url_for("admin"))


@app.route("/admin/hapus/<int:pid>", methods=["POST"])
@login_required
def admin_hapus(pid):
    db = get_db()
    db.execute("DELETE FROM peserta WHERE id = ?", (pid,))
    db.commit()
    flash("Data peserta dihapus.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/import", methods=["POST"])
@login_required
def admin_import():
    """Import CSV dengan header: nim[,nama][,sertifikat]"""
    file = request.files.get("file")
    if not file or not file.filename.lower().endswith(".csv"):
        flash("Pilih file .csv terlebih dahulu.", "error")
        return redirect(url_for("admin"))

    text = file.read().decode("utf-8-sig", errors="replace")
    sample = text[:2048]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    reader.fieldnames = [(f or "").strip().lower() for f in (reader.fieldnames or [])]

    if "nim" not in reader.fieldnames:
        flash("CSV harus punya kolom header 'nim' (opsional: nama, sertifikat).", "error")
        return redirect(url_for("admin"))

    db = get_db()
    ok = skipped = 0
    for r in reader:
        nim = normalize_nim(r.get("nim"))
        nama = (r.get("nama") or "").strip()
        sertifikat = (r.get("sertifikat") or "").strip()
        if not NIM_PATTERN.match(nim) or (sertifikat and not valid_url(sertifikat)):
            skipped += 1
            continue
        # Upsert: NIM yang sudah ada diperbarui (kolom kosong tidak menimpa data lama)
        db.execute(
            """INSERT INTO peserta (nim, nama, sertifikat) VALUES (?, ?, ?)
               ON CONFLICT(nim) DO UPDATE SET
                 nama = CASE WHEN excluded.nama != '' THEN excluded.nama ELSE nama END,
                 sertifikat = CASE WHEN excluded.sertifikat != '' THEN excluded.sertifikat ELSE sertifikat END""",
            (nim, nama, sertifikat),
        )
        ok += 1
    db.commit()
    flash(f"Import selesai: {ok} data tersimpan, {skipped} baris dilewati.", "success")
    return redirect(url_for("admin"))


init_db()
threading.Thread(target=sync_from_sheet, kwargs={"force": True}, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
