"""
ASTERISK - Web Pengecekan Kelulusan Kaderisasi
Backend: Flask + SQLite, data kelulusan disinkronkan dari Google Sheets
"""
import csv
import hashlib
import hmac
import io
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.request
from datetime import timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERTIFIKAT_PDF_DIR = os.path.join(BASE_DIR, "sertifikat_pdf")
# Di Vercel hanya /tmp yang bisa ditulis
DB_PATH = os.environ.get(
    "ASTERISK_DB",
    "/tmp/asterisk.db" if os.environ.get("VERCEL") else os.path.join(BASE_DIR, "asterisk.db"),
)
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("ASTERISK_SECRET") or secrets.token_hex(32)
app.config["TEMPLATES_AUTO_RELOAD"] = os.environ.get("FLASK_DEBUG", "0") == "1"
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # batas upload CSV 2 MB
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("ASTERISK_COOKIE_SECURE", "0") == "1"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=4)

ADMIN_USERNAME = os.environ.get("ASTERISK_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ASTERISK_ADMIN_PASS") or secrets.token_urlsafe(24)

DEFAULT_SHEET_URL = os.environ.get("ASTERISK_SHEET_URL", "")
SYNC_INTERVAL = int(os.environ.get("ASTERISK_SYNC_INTERVAL", 300))  # detik
RETRY_AFTER = 20  # detik, jeda coba ulang jika sinkron gagal

NIM_PATTERN = re.compile(r"^[A-Za-z0-9.\-]{3,30}$")
SHEET_NIM_PATTERN = re.compile(r"^\d{8,15}$")  # sel spreadsheet yang dianggap NIM
SHEET_URL_PATTERN = re.compile(
    r"^https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{10,120})(?:/.*)?$"
)

_sync_lock = threading.Lock()


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=30)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        db.execute("PRAGMA journal_mode=WAL;")
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
            CREATE TABLE IF NOT EXISTS login_attempts (
                ip           TEXT PRIMARY KEY,
                failures     INTEGER NOT NULL DEFAULT 0,
                locked_until REAL NOT NULL DEFAULT 0,
                updated_at   REAL NOT NULL DEFAULT 0
            );
            """
        )
        db.execute("INSERT OR IGNORE INTO settings VALUES ('sheet_url', ?)", (DEFAULT_SHEET_URL,))
        db.execute("INSERT OR IGNORE INTO settings VALUES ('sertifikat_default', ?)",
                   (os.environ.get("ASTERISK_CERT_DEFAULT", ""),))
        csv_path = os.path.join(BASE_DIR, "peserta_lulus.csv")
        if os.path.exists(csv_path):
            with open(csv_path, encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = [
                    (normalize_nim(r.get("nim")), (r.get("nama") or "").strip())
                    for r in reader
                    if normalize_nim(r.get("nim"))
                ]
            db.executemany(
                """INSERT INTO peserta (nim, nama, sumber) VALUES (?, ?, 'sheet')
                   ON CONFLICT(nim) DO UPDATE SET
                     nama = CASE WHEN excluded.nama != '' THEN excluded.nama ELSE peserta.nama END""",
                rows,
            )


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
    if not url or len(url) > 500 or any(c in url for c in ("\r", "\n", "\x00")):
        return False
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def valid_sheet_url(url):
    return bool(SHEET_URL_PATTERN.match((url or "").strip()))


# ---------------------------------------------------------------- google sheets sync
def sheet_csv_url(url):
    """Ubah link Google Sheets resmi menjadi link export CSV; tolak domain selain docs.google.com."""
    m = SHEET_URL_PATTERN.match((url or "").strip())
    if not m:
        raise ValueError("Link spreadsheet harus berupa URL https://docs.google.com/spreadsheets/d/...")
    gid = re.search(r"[?#&]gid=(\d+)", url)
    return (f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
            f"&gid={gid.group(1) if gid else 0}")


MAX_SHEET_BYTES = 2 * 1024 * 1024  # 2 MB


def fetch_sheet_nims(url, attempts=3):
    """Ambil semua sel berisi angka NIM dari spreadsheet (tata letak bebas)."""
    for i in range(attempts):
        try:
            with urllib.request.urlopen(sheet_csv_url(url), timeout=10) as resp:
                if "text/csv" not in resp.headers.get("Content-Type", ""):
                    raise RuntimeError("Spreadsheet tidak publik. Set share ke 'Anyone with the link'.")
                raw = resp.read(MAX_SHEET_BYTES + 1)
                if len(raw) > MAX_SHEET_BYTES:
                    raise RuntimeError("Ukuran file spreadsheet melebihi batas 2 MB.")
                text = raw.decode("utf-8-sig", errors="replace")
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
        with sqlite3.connect(DB_PATH, timeout=30) as db:
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


def trigger_background_sync():
    """Jalankan sinkronisasi Google Sheet di background thread jika interval sudah lewat."""
    if _sync_lock.locked():
        return
    try:
        db = get_db()
        last = float(get_setting(db, "last_sync", "0") or 0)
        if time.time() - last >= SYNC_INTERVAL:
            threading.Thread(target=sync_from_sheet, daemon=True).start()
    except Exception:
        pass

# ---------------------------------------------------------------- auth
MAX_LOGIN_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 900  # 15 menit
LOGIN_WINDOW_SECONDS = 900   # reset hitungan gagal setelah 15 menit
MAX_LOGIN_ATTEMPT_ROWS = 5000


def client_ip():
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip[:64]
    return (request.remote_addr or "unknown")[:64]


def check_login_throttle(db, ip):
    """Return sisa detik terkunci (>0) jika IP sedang di-lock, selain itu 0."""
    now = time.time()
    row = db.execute(
        "SELECT failures, locked_until, updated_at FROM login_attempts WHERE ip = ?",
        (ip,),
    ).fetchone()
    if not row:
        return 0
    if row["locked_until"] > now:
        return int(row["locked_until"] - now) + 1
    if now - row["updated_at"] > LOGIN_WINDOW_SECONDS:
        db.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
        db.commit()
    return 0


def record_login_failure(db, ip):
    now = time.time()
    db.execute(
        "DELETE FROM login_attempts WHERE updated_at < ? AND locked_until < ?",
        (now - LOGIN_WINDOW_SECONDS, now),
    )
    total_rows = db.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0]
    if total_rows >= MAX_LOGIN_ATTEMPT_ROWS:
        db.execute(
            """DELETE FROM login_attempts WHERE ip IN (
                   SELECT ip FROM login_attempts ORDER BY updated_at ASC LIMIT ?
               )""",
            (total_rows - MAX_LOGIN_ATTEMPT_ROWS + 1,),
        )
    row = db.execute(
        "SELECT failures, updated_at FROM login_attempts WHERE ip = ?",
        (ip,),
    ).fetchone()
    failures = 1 if (not row or now - row["updated_at"] > LOGIN_WINDOW_SECONDS) else row["failures"] + 1
    locked_until = (now + LOGIN_LOCKOUT_SECONDS) if failures >= MAX_LOGIN_FAILURES else 0
    db.execute(
        """INSERT INTO login_attempts (ip, failures, locked_until, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(ip) DO UPDATE SET
             failures = excluded.failures,
             locked_until = excluded.locked_until,
             updated_at = excluded.updated_at""",
        (ip, failures, locked_until, now),
    )
    db.commit()

def clear_login_failures(db, ip):
    db.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
    db.commit()


def verify_admin_credentials(db, username, password):
    user_ok = hmac.compare_digest((username or "").encode("utf-8"), ADMIN_USERNAME.encode("utf-8"))
    stored_hash = get_setting(db, "admin_password_hash", "")
    if stored_hash:
        pass_ok = check_password_hash(stored_hash, password or "")
    else:
        pass_ok = hmac.compare_digest((password or "").encode("utf-8"), ADMIN_PASSWORD.encode("utf-8"))
    return user_ok and pass_ok


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        session["csrf_token"] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token}


@app.before_request
def csrf_protect_admin():
    if request.method == "POST" and request.path.startswith("/admin"):
        expected = session.get("csrf_token", "")
        submitted = request.form.get("csrf_token", "")
        if not expected or not submitted or not hmac.compare_digest(expected, submitted):
            abort(403, description="Token keamanan (CSRF) tidak valid atau sesi telah berakhir. Silakan muat ulang halaman.")


@app.after_request
def set_security_headers(response):
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(), payment=(), usb=()"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    if request.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------- public rate limit & cert token
PUBLIC_CEK_LIMIT = (20, 60)   # maks 20 request / 60 detik per IP
PUBLIC_CERT_LIMIT = (8, 60)   # maks 8 unduhan / 60 detik per IP
CERT_TOKEN_TTL = 900          # 15 menit
MAX_RATE_BUCKETS = 5000
_rate_lock = threading.Lock()
_rate_buckets = {}


def check_rate_limit(bucket_name: str, ip: str, limit: int, window: int) -> bool:
    """Return True jika masih dalam batas, False jika melampaui rate-limit."""
    now = time.time()
    cutoff = now - window
    key = f"{bucket_name}:{ip}"
    with _rate_lock:
        if len(_rate_buckets) > MAX_RATE_BUCKETS:
            expired_keys = [
                k for k, ts_list in _rate_buckets.items()
                if not ts_list or ts_list[-1] < cutoff
            ]
            for k in expired_keys:
                _rate_buckets.pop(k, None)
        hits = [t for t in _rate_buckets.get(key, []) if t > cutoff]
        if len(hits) >= limit:
            _rate_buckets[key] = hits
            return False
        hits.append(now)
        _rate_buckets[key] = hits
        return True


def create_cert_token(nim: str) -> str:
    ts = str(int(time.time()))
    msg = f"{nim}:{ts}".encode("utf-8")
    sig = hmac.new(app.secret_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def verify_cert_token(nim: str, token: str) -> bool:
    if not token or "." not in token:
        return False
    ts_str, sig = token.split(".", 1)
    if not ts_str.isdigit():
        return False
    age = time.time() - int(ts_str)
    if age < -30 or age > CERT_TOKEN_TTL:
        return False
    expected = hmac.new(
        app.secret_key.encode("utf-8"),
        f"{nim}:{ts_str}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


# ---------------------------------------------------------------- public
@app.route("/")
def index():
    return render_template("index.html")


def _pre_rendered_pdf_path(nim):
    if not nim or not NIM_PATTERN.match(nim):
        return None
    if not os.path.isdir(SERTIFIKAT_PDF_DIR):
        return None
    base_real = os.path.realpath(SERTIFIKAT_PDF_DIR)
    full_path = os.path.realpath(os.path.join(SERTIFIKAT_PDF_DIR, f"{nim}.pdf"))
    if os.path.commonpath([full_path, base_real]) != base_real:
        return None
    return full_path if os.path.isfile(full_path) else None


@app.route("/api/sertifikat/<nim>", methods=["GET"])
def api_sertifikat(nim):
    ip = client_ip()
    if not check_rate_limit("cert", ip, *PUBLIC_CERT_LIMIT):
        abort(429, description="Terlalu banyak permintaan unduhan sertifikat. Silakan tunggu 1 menit.")

    nim = normalize_nim(nim)
    if not nim or not NIM_PATTERN.match(nim):
        abort(404)

    token = request.args.get("token", "")
    if not verify_cert_token(nim, token):
        abort(403)

    db = get_db()
    row = db.execute("SELECT nim, nama FROM peserta WHERE nim = ?", (nim,)).fetchone()
    if not row or not _pre_rendered_pdf_path(row["nim"]):
        abort(404)

    safe_nama = re.sub(r'[\\/:*?"<>|\r\n]+', "", (row["nama"] or row["nim"])).strip() or row["nim"]
    download_name = f"E-Certificate Kaderisasi HMTT 2026 - {safe_nama}.pdf"
    resp = send_from_directory(
        SERTIFIKAT_PDF_DIR,
        f"{row['nim']}.pdf",
        mimetype="application/pdf",
        as_attachment=True,
        download_name=download_name,
        max_age=0,
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.route("/api/cek", methods=["POST"])
def api_cek():
    ip = client_ip()
    if not check_rate_limit("cek", ip, *PUBLIC_CEK_LIMIT):
        return jsonify(
            ok=False,
            message="Terlalu banyak permintaan pengecekan. Silakan tunggu 1 menit.",
        ), 429

    data = request.get_json(silent=True) or {}
    nim = normalize_nim(data.get("nim"))

    if not nim:
        return jsonify(ok=False, message="NIM tidak boleh kosong."), 400
    if not NIM_PATTERN.match(nim):
        return jsonify(ok=False, message="Format NIM tidak valid."), 400

    trigger_background_sync()  # sinkronisasi berjalan di background tanpa memblokir request

    db = get_db()
    # Database kosong (sinkron pertama gagal): jangan sampai peserta dinyatakan tidak lulus
    if db.execute("SELECT COUNT(*) FROM peserta").fetchone()[0] == 0:
        return jsonify(ok=False, message="Data kelulusan sedang dimuat. Coba lagi dalam beberapa detik."), 503

    row = db.execute("SELECT nim, nama, sertifikat FROM peserta WHERE nim = ?", (nim,)).fetchone()

    if row:
        sertifikat = row["sertifikat"] or get_setting(db, "sertifikat_default").replace("{nim}", row["nim"])
        sertifikat_ready = bool(_pre_rendered_pdf_path(row["nim"]))
        download_token = create_cert_token(row["nim"]) if sertifikat_ready else ""
        return jsonify(
            ok=True,
            lulus=True,
            message="SELAMAT ANDA LULUS\nKADERISASI 2026",
            nim=row["nim"],
            nama=row["nama"],
            sertifikat=sertifikat,
            sertifikat_ready=sertifikat_ready,
            download_token=download_token,
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
        db = get_db()
        ip = client_ip()
        wait_sec = check_login_throttle(db, ip)
        if wait_sec > 0:
            menit = max(1, (wait_sec + 59) // 60)
            flash(f"Terlalu banyak percobaan login gagal. Coba lagi dalam {menit} menit.", "error")
            return render_template("login.html"), 429

        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if verify_admin_credentials(db, username, password):
            clear_login_failures(db, ip)
            session.clear()
            session.permanent = True
            session["admin"] = True
            session["csrf_token"] = secrets.token_hex(32)
            return redirect(url_for("admin"))

        record_login_failure(db, ip)
        flash("Username atau password salah.", "error")
        return render_template("login.html"), 401
    return render_template("login.html")


@app.route("/admin/logout", methods=["POST"])
@login_required
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
    if sheet_url and not valid_sheet_url(sheet_url):
        flash("Link spreadsheet harus diawali https://docs.google.com/spreadsheets/d/...", "error")
        return redirect(url_for("admin"))
    if default and not valid_url(default):
        flash("Link e-certificate harus diawali http:// atau https://", "error")
        return redirect(url_for("admin"))
    db = get_db()
    set_setting(db, "sheet_url", sheet_url)
    set_setting(db, "sertifikat_default", default)
    db.commit()
    flash("Pengaturan disimpan.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/password", methods=["POST"])
@login_required
def admin_password():
    old_pw = request.form.get("old_password", "")
    new_pw = request.form.get("new_password", "")
    confirm_pw = request.form.get("confirm_password", "")
    db = get_db()
    if not verify_admin_credentials(db, ADMIN_USERNAME, old_pw):
        flash("Password saat ini salah.", "error")
        return redirect(url_for("admin"))
    if len(new_pw) < 8:
        flash("Password baru minimal 8 karakter.", "error")
        return redirect(url_for("admin"))
    if new_pw != confirm_pw:
        flash("Konfirmasi password baru tidak cocok.", "error")
        return redirect(url_for("admin"))
    set_setting(db, "admin_password_hash", generate_password_hash(new_pw))
    db.commit()
    session.clear()
    session.permanent = True
    session["admin"] = True
    session["csrf_token"] = secrets.token_hex(32)
    flash("Password admin berhasil diperbarui.", "success")
    return redirect(url_for("admin"))


def _read_form():
    nim = normalize_nim(request.form.get("nim"))
    nama = request.form.get("nama", "").strip()[:120]
    sertifikat = request.form.get("sertifikat", "").strip()[:500]
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
    db = get_db()
    try:
        db.execute("INSERT INTO peserta (nim, nama, sertifikat, sumber) VALUES (?, ?, ?, 'manual')", data)
        db.commit()
        flash(f"NIM {data[0]} ditambahkan.", "success")
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
    db = get_db()
    try:
        cur = db.execute(
            "UPDATE peserta SET nim = ?, nama = ?, sertifikat = ? WHERE id = ?",
            (*data, pid),
        )
        db.commit()
        if cur.rowcount == 0:
            flash("Data tidak ditemukan.", "error")
        else:
            flash(f"Data NIM {data[0]} diperbarui.", "success")
    except sqlite3.IntegrityError:
        flash(f"NIM {data[0]} sudah dipakai peserta lain.", "error")
    return redirect(url_for("admin"))


@app.route("/admin/hapus/<int:pid>", methods=["POST"])
@login_required
def admin_hapus(pid):
    db = get_db()
    db.execute("DELETE FROM peserta WHERE id = ?", (pid,))
    db.commit()
    flash("Data dihapus.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/import", methods=["POST"])
@login_required
def admin_import():
    """Import CSV dengan header: nim[,nama][,sertifikat]"""
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Pilih file CSV terlebih dahulu.", "error")
        return redirect(url_for("admin"))
    try:
        text = f.stream.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("File harus ber-encoding UTF-8.", "error")
        return redirect(url_for("admin"))

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "nim" not in [h.strip().lower() for h in reader.fieldnames]:
        flash("CSV harus memiliki kolom 'nim'.", "error")
        return redirect(url_for("admin"))

    rows = []
    skipped_urls = 0
    for raw in reader:
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        nim = normalize_nim(row.get("nim"))
        if nim and NIM_PATTERN.match(nim):
            nama = row.get("nama", "")[:120]
            sertifikat = row.get("sertifikat", "")[:500]
            if sertifikat and not valid_url(sertifikat):
                sertifikat = ""
                skipped_urls += 1
            rows.append((nim, nama, sertifikat))

    db = get_db()
    db.executemany(
        """INSERT INTO peserta (nim, nama, sertifikat, sumber) VALUES (?, ?, ?, 'manual')
           ON CONFLICT(nim) DO UPDATE SET
             nama = CASE WHEN excluded.nama != '' THEN excluded.nama ELSE peserta.nama END,
             sertifikat = CASE WHEN excluded.sertifikat != '' THEN excluded.sertifikat ELSE peserta.sertifikat END""",
        rows,
    )
    db.commit()
    extra = f" ({skipped_urls} URL sertifikat tidak valid diabaikan)" if skipped_urls else ""
    flash(f"{len(rows)} baris berhasil diimpor / diperbarui.{extra}", "success")
    return redirect(url_for("admin"))


init_db()
threading.Thread(target=sync_from_sheet, kwargs={"force": True}, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
