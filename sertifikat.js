// Membuat e-certificate PDF (nama + NIM) langsung di browser dari sertifikat/template.pdf.
// Posisi & ukuran diambil dari desain asli (contoh_sertifikat_kaderisasi.pdf, A4 landscape).
const SERTIFIKAT = {
  template: "sertifikat/template.pdf",
  fontNama: "sertifikat/Lora-Bold.ttf",
  fontNim: "sertifikat/Poppins-Medium.ttf",
  nama: { baseline: 312.96, size: 34, maxWidth: 600, color: [1, 1, 1] },
  nim: { baseline: 276.21, size: 11, spacing: 2.42, color: [0xcf / 255, 0xe3 / 255, 1] },
};

const loadedScripts = {};
function loadScript(src) {
  loadedScripts[src] ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => { delete loadedScripts[src]; reject(new Error(`Gagal memuat ${src}`)); };
    document.head.appendChild(s);
  });
  return loadedScripts[src];
}

async function fetchBytes(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Gagal memuat ${url}`);
  return res.arrayBuffer();
}

async function buatPdfSertifikat(nama, nim) {
  // Library (±1,3 MB) baru dimuat saat tombol ditekan agar halaman tetap ringan
  await Promise.all([loadScript("vendor/pdf-lib.min.js"), loadScript("vendor/fontkit.umd.min.js")]);
  const { PDFDocument, rgb } = PDFLib;

  const [tpl, fNama, fNim] = await Promise.all([
    fetchBytes(SERTIFIKAT.template), fetchBytes(SERTIFIKAT.fontNama), fetchBytes(SERTIFIKAT.fontNim),
  ]);
  const pdf = await PDFDocument.load(tpl);
  pdf.registerFontkit(fontkit);
  const fontNama = await pdf.embedFont(fNama, { subset: true });
  const fontNim = await pdf.embedFont(fNim, { subset: true });
  const page = pdf.getPage(0);
  const cx = page.getWidth() / 2;

  // Nama: di tengah, diperkecil otomatis jika terlalu panjang
  const n = SERTIFIKAT.nama;
  let size = n.size;
  let w = fontNama.widthOfTextAtSize(nama, size);
  if (w > n.maxWidth) { size = size * n.maxWidth / w; w = fontNama.widthOfTextAtSize(nama, size); }
  page.drawText(nama, { x: cx - w / 2, y: n.baseline, size, font: fontNama, color: rgb(...n.color) });

  // NIM: huruf per huruf agar spasi antarhuruf sama seperti desain
  const m = SERTIFIKAT.nim;
  const teks = `NIM ${nim}`;
  const widths = [...teks].map((c) => fontNim.widthOfTextAtSize(c, m.size));
  const total = widths.reduce((a, b) => a + b, 0) + m.spacing * (teks.length - 1);
  let x = cx - total / 2;
  [...teks].forEach((c, i) => {
    page.drawText(c, { x, y: m.baseline, size: m.size, font: fontNim, color: rgb(...m.color) });
    x += widths[i] + m.spacing;
  });

  pdf.setTitle(`Sertifikat Kelulusan Kaderisasi HMTT 2026 - ${nama}`);
  pdf.setAuthor("Badan Pengurus HMTT 2026 - Kabinet Parivarta");

  return pdf.save();
}

async function buatSertifikat(nama, nim) {
  const blob = new Blob([await buatPdfSertifikat(nama, nim)], { type: "application/pdf" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `E-Certificate Kaderisasi HMTT 2026 - ${nama.replace(/[\\/:*?"<>|]/g, "")}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
