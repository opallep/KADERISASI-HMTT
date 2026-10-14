const CFG = window.KADERISASI_CONFIG;
const NIM_CELL = /^\d{8,15}$/;

const form = document.getElementById("cekForm");
const input = document.getElementById("nim");
const btn = document.getElementById("cekBtn");
const result = document.getElementById("result");

function sheetUrls() {
  const base = `https://docs.google.com/spreadsheets/d/${CFG.sheetId}`;
  return [
    `${base}/gviz/tq?tqx=out:csv&gid=${CFG.gid}`,
    `${base}/export?format=csv&gid=${CFG.gid}`,
  ];
}

// Ambil semua sel berisi angka NIM dari CSV (tata letak sheet bebas)
function parseNims(csv) {
  const nims = new Set();
  for (const cell of csv.split(/[\r\n,]+/)) {
    const v = cell.replace(/"/g, "").trim();
    if (NIM_CELL.test(v)) nims.add(v);
  }
  return nims;
}

async function fetchNims() {
  let lastErr;
  for (const url of sheetUrls()) {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const res = await fetch(`${url}&t=${Date.now()}`, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const nims = parseNims(await res.text());
        if (nims.size === 0) throw new Error("Tidak ada NIM di spreadsheet");
        return nims;
      } catch (err) {
        lastErr = err;
      }
    }
  }
  throw lastErr;
}

// Muat data sejak halaman dibuka agar pengecekan terasa instan
let dataPromise = fetchNims();

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function certLink(nim) {
  const link = CFG.sertifikatPerNim[nim] || CFG.sertifikatDefault.replace(/\{nim\}/g, nim);
  return /^https?:\/\//.test(link) ? link : "";
}

async function unduh(button, nama, nim) {
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> Menyiapkan sertifikat...';
  try {
    await buatSertifikat(nama, nim);
    button.innerHTML = "✅ Sertifikat terunduh — unduh lagi";
  } catch (err) {
    console.error(err);
    button.innerHTML = "⚠️ Gagal membuat sertifikat — coba lagi";
  } finally {
    button.disabled = false;
  }
}

function show(type, html) {
  result.className = `result ${type}`;
  result.innerHTML = html;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const nim = input.value.replace(/\s+/g, "").toUpperCase();
  if (!nim) return;

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Memeriksa...';

  try {
    let nims;
    try {
      nims = await dataPromise;
    } catch {
      dataPromise = fetchNims(); // coba muat ulang sekali lagi
      nims = await dataPromise;
    }

    if (nims.has(nim)) {
      const nama = (window.KADERISASI_PESERTA || {})[nim];
      // Prioritas: link khusus NIM > sertifikat otomatis (butuh nama) > link umum
      const linkKhusus = CFG.sertifikatPerNim[nim];
      const link = certLink(nim);
      let sertifikat;
      if (!linkKhusus && nama && CFG.sertifikatOtomatis !== false) {
        sertifikat = `<button type="button" class="btn btn-success" id="unduhSertifikat">📄 Download E-Certificate</button>`;
      } else if (link) {
        sertifikat = `<a class="btn btn-success" href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">
             📄 Lihat E-Certificate
           </a>`;
      } else {
        sertifikat = `<p style="margin-top:12px">Link e-certificate akan segera dibagikan.</p>`;
      }
      show("lulus", `
        <div class="icon">🎉</div>
        <h3>SELAMAT ANDA LULUS KADERISASI</h3>
        <p>${nama ? `<strong class="nama">${escapeHtml(nama)}</strong><br>` : ""}NIM ${escapeHtml(nim)}</p>
        ${sertifikat}`);
      document.getElementById("unduhSertifikat")?.addEventListener("click", (ev) => unduh(ev.currentTarget, nama, nim));
    } else {
      show("gagal", `
        <div class="icon">😔</div>
        <h3>MAAF ANDA BELUM LULUS KADERISASI</h3>
        <p>NIM <strong>${escapeHtml(nim)}</strong> tidak ditemukan dalam daftar kelulusan.</p>`);
    }
  } catch {
    // Jangan pernah menyatakan "belum lulus" jika data gagal dimuat
    dataPromise = fetchNims();
    show("error", `
      <div class="icon">⚠️</div>
      <h3>Mohon Tunggu</h3>
      <p>Data kelulusan gagal dimuat. Periksa koneksi internet lalu coba lagi.</p>`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Cek Sekarang";
  }
});
