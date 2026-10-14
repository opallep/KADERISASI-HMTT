const form = document.getElementById("cekForm");
const input = document.getElementById("nim");
const btn = document.getElementById("cekBtn");
const result = document.getElementById("result");

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
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
        <div class="icon">⚠️</div>
        <h3>${res.status === 503 ? "Mohon Tunggu" : "NIM Tidak Valid"}</h3>
        <p>${escapeHtml(data.message)}</p>`);
    } else if (data.lulus) {
      const nama = data.nama ? `<strong>${escapeHtml(data.nama)}</strong><br>` : "";
      const sertifikat = data.sertifikat
        ? `<a class="btn btn-success" href="${escapeHtml(data.sertifikat)}" target="_blank" rel="noopener noreferrer">
             📄 Lihat E-Certificate
           </a>`
        : `<p style="margin-top:12px">Link e-certificate akan segera dibagikan.</p>`;
      show("lulus", `
        <div class="icon">🎉</div>
        <h3>${escapeHtml(data.message)}</h3>
        <p>${nama}NIM ${escapeHtml(data.nim)}</p>
        ${sertifikat}`);
    } else {
      show("gagal", `
        <div class="icon">😔</div>
        <h3>${escapeHtml(data.message)}</h3>
        <p>NIM <strong>${escapeHtml(data.nim)}</strong> tidak ditemukan dalam daftar kelulusan.</p>`);
    }
  } catch (err) {
    show("error", `
      <div class="icon">⚠️</div>
      <h3>Terjadi Kesalahan</h3>
      <p>Tidak dapat terhubung ke server. Coba lagi nanti.</p>`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Cek Sekarang";
  }
});
