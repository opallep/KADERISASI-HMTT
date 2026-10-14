// Pengaturan web cek kelulusan — edit file ini langsung di GitHub lalu commit.
window.KADERISASI_CONFIG = {
  // ID & gid Google Sheet kelulusan (dari link .../spreadsheets/d/<ID>/edit?gid=<GID>).
  // Sheet harus di-share "Anyone with the link → Viewer".
  sheetId: "1sOJ2-r1G0W9Rhq_6RZRy1Equln1u6VcRG1klr5SP6A4",
  gid: "0",

  // true = web membuat e-certificate PDF otomatis (nama + NIM) dari sertifikat/template.pdf
  // untuk peserta yang namanya ada di peserta.js.
  sertifikatOtomatis: true,

  // Link e-certificate umum (dipakai jika sertifikat otomatis mati / nama peserta tidak ada).
  // Tulisan {nim} akan diganti NIM peserta, contoh: "https://drive.google.com/.../{nim}.pdf"
  sertifikatDefault: "",

  // Link e-certificate khusus per NIM (opsional), contoh:
  // "101012500002": "https://drive.google.com/file/d/xxxx/view",
  sertifikatPerNim: {
  },
};
