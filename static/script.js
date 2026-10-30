const form = document.getElementById("cekForm");
const input = document.getElementById("nim");
const btn = document.getElementById("cekBtn");
const result = document.getElementById("result");

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const ICON_DOWNLOAD = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';

async function unduh(button, nama, nim, token) {
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> Menyiapkan sertifikat...';
  try {
    const query = token ? `?token=${encodeURIComponent(token)}` : "";
    const res = await fetch(`/api/sertifikat/${encodeURIComponent(nim)}${query}`, { cache: "no-store" });
    if (!res.ok) throw new Error("Sertifikat tidak tersedia");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const safeNama = String(nama || nim).replace(/[\\/:*?"<>|]/g, "").trim() || nim;
    a.download = `E-Certificate Kaderisasi HMTT 2026 - ${safeNama}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    button.innerHTML = `${ICON_DOWNLOAD} Sertifikat terunduh — unduh lagi`;
  } catch (err) {
    console.error(err);
    button.textContent = "Gagal mengunduh sertifikat — coba lagi";
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
  const nim = input.value.trim();
  if (!nim) return;

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Memeriksa...';

  try {
    const res = await fetch("/api/cek", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nim }),
    });
    const data = await res.json();

    if (!data.ok) {
      show("error", `
        <h3>${res.status === 503 || res.status === 429 ? "Mohon Tunggu" : "NIM Tidak Valid"}</h3>
        <p>${escapeHtml(data.message)}</p>`);
    } else if (data.lulus) {
      const nama = data.nama ? `<strong class="nama">${escapeHtml(data.nama)}</strong><br>` : "";
      let sertifikat;
      if (data.sertifikat) {
        sertifikat = `<a class="btn btn-success" href="${escapeHtml(data.sertifikat)}" target="_blank" rel="noopener noreferrer">
             ${ICON_DOWNLOAD} Lihat E-Certificate
           </a>`;
      } else if (data.sertifikat_ready) {
        sertifikat = `<button type="button" class="btn btn-success" id="unduhSertifikat">${ICON_DOWNLOAD} Download E-Certificate</button>`;
      } else {
        sertifikat = `<p style="margin-top:12px">Link e-certificate akan segera dibagikan.</p>`;
      }
      show("lulus", `
        <h3>${escapeHtml(data.message).replace(/\n/g, "<br>")}</h3>
        <p>${nama}NIM ${escapeHtml(data.nim)}</p>
        ${sertifikat}`);
      document.getElementById("unduhSertifikat")?.addEventListener("click", (ev) => unduh(ev.currentTarget, data.nama, data.nim, data.download_token));
    } else {
      show("gagal", `
        <h3>${escapeHtml(data.message)}</h3>
        <p>NIM <strong>${escapeHtml(data.nim)}</strong> tidak ditemukan dalam daftar kelulusan.</p>`);
    }
  } catch (err) {
    show("error", `
      <h3>Terjadi Kesalahan</h3>
      <p>Tidak dapat terhubung ke server. Coba lagi nanti.</p>`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Cek Sekarang";
  }
});
