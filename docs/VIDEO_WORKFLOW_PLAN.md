# 🎬 Rancangan Workflow Otomasi Video Horror (YouTube Shorts + TikTok)

> Status: **DESAIN** (belum diimplementasi). Dokumen ini rencana teknis sebelum coding.
> Target: ekstensi dari Bot_thread (Ai-sosmed) biar selain auto-post Threads, bot juga
> bisa bikin **video cerita horror 9:16** otomatis, di-approve lewat Discord, lalu
> auto-upload ke **YouTube Shorts** (TikTok = generate file, upload manual).

## 1. Konsep & keputusan desain (disepakati)

Format: **narasi cerita horror (TTS) di atas footage gameplay** (Minecraft parkour /
Subway Surfers / dsb) + subtitle sinkron. Ini format Shorts/TikTok yang terbukti viral
dan **tidak butuh AI video generation** — jadi bebas dari kebutuhan GPU & HF Space yang
rapuh di free tier.

| Topik | Pilihan | Konsekuensi |
|---|---|---|
| Tema konten | **Horror** (cerita serem / creepypasta, Bahasa Indonesia) | Persona/prompt LLM khusus horror |
| Visual | **Footage gameplay** sebagai latar (bukan AI video gen) | Gratis, ringan, tanpa GPU |
| Voiceover | **F5-TTS** (finetune Indo, voice clone) + **edge-tts** fallback | Reuse pipeline BIMA_CORE; F5 butuh GPU/CUDA, edge-tts jalan tanpa GPU |
| Subtitle | Burn-in sinkron (karaoke-style) | Timing via **faster-whisper** (sudah ada di stack BIMA) — bukan dari word-boundary TTS |
| Upload | **Auto YouTube, manual TikTok** | YouTube Data API v3; TikTok = file siap |
| Mulai dari | **Desain dulu** → lalu Fase 1 | Coding bertahap |

> Catatan: opsi **AI video generation** (HF Space: Wan2.2 / LTX-2.3) di-arsip sebagai
> kemungkinan masa depan, bukan jalur utama — lihat §10.

## 2. Kenapa pendekatan ini pas buat "gratis"

- **Tanpa model video AI** — visual cuma footage yang sudah ada, dipotong & di-crop
  pakai ffmpeg.
- **TTS gratis & berkualitas** — pakai **F5-TTS** yang sudah jalan di laptop user
  (finetune Indo `Eempostor/F5-TTS-INDO-FINETUNE-V2`, bisa **voice cloning** → narator
  horror konsisten), dengan **edge-tts** sebagai fallback gratis tanpa GPU.
- **Subtitle presisi tanpa biaya** — `faster-whisper` (sudah dipakai BIMA_CORE buat STT)
  meng-align narasi jadi timing kata/segmen → subtitle sinkron, apa pun TTS-nya.
- **LLM sudah ada** — `core/llm_config.py` (OpenRouter) buat nulis naskah horror.
- **Render ringan** — penggabungan akhir cuma ffmpeg.

> F5-TTS perlu **GPU/CUDA**. Laptop user sudah menjalankannya (BIMA_CORE pakai subprocess
> terisolasi `tts_worker.py` biar VRAM dilepas & crash CUDA gak menular). Kalau GPU gak
> tersedia, pipeline otomatis turun ke **edge-tts** (tanpa GPU).

### Catatan hardware (target: RTX 3050 Laptop, 4GB VRAM)
- **Durasi total bukan beban VRAM.** F5-TTS memecah teks per kalimat jadi chunk pendek
  (~10–30 dtk), render satu-satu, lalu disambung. VRAM yang dipakai = ukuran 1 chunk,
  bukan total durasi → narasi 1–2 menit tetap aman.
- **Aman di 4GB karena:** (1) subprocess `tts_worker.py` lepas VRAM tiap selesai;
  (2) F5 dan `faster-whisper` jalan **bergantian**, gak barengan; (3) LLM di cloud
  (OpenRouter), gak makan VRAM lokal; (4) fallback edge-tts kalau OOM.
- **Harga utama = waktu, bukan kapasitas.** Di 3050, narasi 1–2 menit ≈ 1–3 menit
  kompute (chunked). Karena ada approval gate (non-realtime), ini dapat diterima.
- **Mitigasi OOM:** batasi panjang chunk per kalimat (default F5) + fp16.

⚠️ **Hak cipta footage**: pakai gameplay **milik sendiri** atau pack **no-copyright**
(banyak "Minecraft parkour no copyright" / "gameplay for editing"). Jangan pakai video
orang sembarangan — bisa kena Content ID YouTube. File disimpan user di `assets/gameplay/`.

## 3. Komponen existing yang di-reuse

| Modul existing | Peran di workflow video |
|---|---|
| `core/llm_config.py` | Generate **naskah horror** + judul + deskripsi + hashtag |
| `core/permission_gate.py` + `bot.py` | **Approval gate**: preview MP4 ke DM, 👍 publish / 👎 batal / balas = revisi |
| `core/threads_scheduler.py` | Pola **scheduler** apscheduler buat auto-post terjadwal |
| `tools/image_gen.py` | (Opsional) thumbnail/cover horror |

## 4. Modul baru yang dibangun

```
core/
  video_commands.py      # handler perintah !horror / !video (mirror threads_commands)
  video_scheduler.py     # jadwal auto-post video (mirror threads_scheduler) — fase lanjut
  youtube_uploader.py    # upload YouTube Data API v3 (OAuth), tag #Shorts
core/ (lanjutan — port dari BIMA_CORE)
  tts.py                 # PORT: wrapper TTS (F5-TTS utama, edge-tts fallback) → narasi.wav
  tts_worker.py          # PORT: subprocess F5-TTS terisolasi (lepas VRAM, aman dari crash CUDA)
tools/
  story_gen.py           # naskah horror via LLM (hook 3 dtk pertama, ending nge-twist)
  subtitle.py            # faster-whisper align narasi → word/segment timing → .ass burn-in
  gameplay_bg.py         # pilih footage acak, potong sepanjang narasi, crop center 9:16
  video_assembly.py      # ffmpeg: gabung bg + subtitle + narasi + ambient → MP4 9:16
outputs/
  video/                 # hasil render + artefak antara (auto-prune)
assets/
  gameplay/              # footage gameplay no-copyright (disiapkan user)
  sfx/                   # ambient/jumpscare/whoosh (opsional, bebas-royalti)
  voice/                 # reference audio + transkrip buat voice clone F5-TTS (narator horror)
docs/
  VIDEO_WORKFLOW_PLAN.md # dokumen ini
```

## 5. Alur pipeline

```
[Topik/ide horror]
   │  (LLM: story_gen)
   ▼
[Naskah]  ── hook kuat 3 dtk pertama + body + ending nge-twist, total ~150–200 kata
   │
   ├─► TTS (core/tts: F5-TTS clone / edge-tts) ──► narasi.wav
   │            │
   │            ▼
   │     faster-whisper align ──► word/segment timing ──► [subtitle.ass] (karaoke-style)
   │
   ├─► gameplay_bg ──► pilih footage acak ──► potong = durasi narasi ──► crop 9:16 (1080×1920)
   │
   ▼
[video_assembly ffmpeg]
   • background gameplay (loop kalau kurang panjang)
   • overlay subtitle .ass
   • mix: narasi (utama) + ambient horror pelan (ducking) + sfx opsional
   • normalize loudness, total < 60 dtk
   ▼
[Preview MP4] ──► DM Discord (permission_gate) ──► 👍 / 👎 / balas = revisi naskah
   │
   ├─ 👍 ─► YouTube auto-upload (Shorts)  +  simpan file utk TikTok manual
   └─ 👎 ─► batal
```

### Detail penting
- **Durasi**: total < 60 dtk (Shorts). Naskah dibatasi ±180 kata biar muat.
- **Rasio**: render final **1080×1920 (9:16)**. Gameplay 16:9 → crop tengah vertikal.
- **Subtitle**: timing via `faster-whisper` (transkrip + align narasi yang sudah jadi).
  Gaya teks gede, kontras tinggi, 1–3 kata per baris (gaya TikTok horror).
- **Audio**: narasi sebagai track utama; ambient horror pelan di latar (volume ~15%,
  ducking saat ada suara); sfx jumpscare opsional di klimaks.
- **Suara TTS**: **F5-TTS** clone dari reference audio narator horror (di `assets/voice/`),
  finetune Indo. Fallback `edge-tts` (`id-ID-ArdiNeural`) kalau GPU/F5 gak tersedia.
  Pitch/tempo bisa diturunkan via ffmpeg `asetrate`/`atempo` biar makin mencekam.

## 6. Dependensi baru

```
# TTS utama (voice clone, finetune Indo) — sudah dipakai di BIMA_CORE
f5-tts                    # + torch/torchaudio (CUDA build) buat GPU
# TTS fallback (tanpa GPU)
edge-tts>=6.1
# Subtitle timing (align narasi) — sudah ada di stack BIMA_CORE
faster-whisper>=1.0

# YouTube upload
google-api-python-client>=2.0
google-auth-oauthlib>=1.2
google-auth-httplib2>=0.2
```

**Binary sistem:** `ffmpeg` wajib di PATH (potong, crop, mux, subtitle burn-in).
Cek saat startup; kasih pesan jelas kalau belum ada.

> F5-TTS + faster-whisper akan **di-port dari BIMA_CORE** (`core/tts.py`, `core/tts_worker.py`)
> — sama seperti `image_gen`/`image_host` yang sudah di-extract. Model F5 (~1.2 GB) &
> whisper (~390 MB) ke-download otomatis saat pertama dipakai. MoviePy dihindari — pakai
> `ffmpeg` langsung via subprocess biar ringan & terkontrol.

## 7. Variabel `.env` baru

```dotenv
# === TTS ===
TTS_PROVIDER=f5                       # f5 (voice clone, butuh GPU) | edge (fallback, no GPU)
F5_MODEL=Eempostor/F5-TTS-INDO-FINETUNE-V2   # model finetune Indo
F5_REF_AUDIO=assets/voice/narator_horror.wav # reference clip buat clone
F5_REF_TEXT=assets/voice/narator_horror.txt  # transkrip reference (wajib F5)
EDGE_TTS_VOICE=id-ID-ArdiNeural       # dipakai kalau TTS_PROVIDER=edge / F5 gagal
TTS_PITCH=-3Hz                        # opsional (edge), bikin lebih dalam
TTS_RATE=-5%                          # opsional (edge), lebih pelan/mencekam

# === SUBTITLE ===
WHISPER_MODEL=small                   # faster-whisper buat align timing subtitle

# === BACKGROUND / AUDIO ===
GAMEPLAY_DIR=assets/gameplay          # folder footage no-copyright milik user
AMBIENT_PATH=assets/sfx/ambient.mp3   # ambient horror latar (opsional)
VIDEO_MAX_SECONDS=58                  # batas aman < 60 dtk

# === STORY ===
STORY_MAX_WORDS=180                   # batas panjang naskah biar muat < 60 dtk

# === YOUTUBE UPLOAD ===
YOUTUBE_CLIENT_SECRET_FILE=secrets/yt_client_secret.json
YOUTUBE_TOKEN_FILE=secrets/yt_token.json
YOUTUBE_PRIVACY=public                # public | unlisted | private
YOUTUBE_CATEGORY_ID=24                # 24 = Entertainment

# === SCHEDULER (fase lanjut) ===
ENABLE_VIDEO_AUTO=false               # true = auto-post video terjadwal
```

## 8. Rencana bertahap (phasing)

| Fase | Isi | Bisa dites? |
|---|---|---|
| **0** | Scaffolding modul + dependensi + dokumen ini | — |
| **1** | `story_gen → tts → subtitle → gameplay_bg → assembly` = MP4 lokal (tanpa upload) | ✅ lokal; F5-TTS butuh GPU (atau pakai edge-tts) |
| **2** | Integrasi `!horror <ide>` + approval gate (preview MP4 ke DM) | ✅ butuh Discord |
| **3** | YouTube auto-upload (OAuth + upload Shorts) | ✅ butuh GCloud OAuth |
| **4** | Scheduler auto-post + handoff TikTok manual (notif file siap) | ✅ |

**Rekomendasi:** mulai **Fase 1**. Output langsung kelihatan (MP4 jadi), dependensi minim,
dan bisa diiterasi (kualitas subtitle, ducking audio, pacing) sebelum nyentuh upload.

> Prasyarat Fase 1 dari user: taruh minimal 1 file gameplay no-copyright di
> `assets/gameplay/` (mis. `minecraft_parkour_01.mp4`).

## 9. Risiko & mitigasi

| Risiko | Mitigasi |
|---|---|
| **Hak cipta footage** (Content ID) | Wajib gameplay milik sendiri / no-copyright; dokumentasikan sumber |
| Hak cipta musik/sfx | Pakai ambient & sfx bebas-royalti di `assets/sfx/` |
| Subtitle meleset dari suara | Align pakai `faster-whisper` pada narasi yang sudah jadi (presisi, apa pun TTS-nya) |
| F5-TTS rakus VRAM / crash CUDA | Pola subprocess terisolasi `tts_worker.py` (sudah terbukti di BIMA_CORE); fallback edge-tts |
| Durasi > 60 dtk → ditolak Shorts | Batasi naskah (`STORY_MAX_WORDS`) + hard-trim di assembly |
| **TikTok API restriktif** | Sesuai keputusan: TikTok = generate file + upload manual |
| YouTube OAuth ribet | One-time consent; simpan refresh token di `YOUTUBE_TOKEN_FILE` |
| Konten horror terlalu ekstrem | Naskah lewat approval gate Discord sebelum publish |
| **Monetisasi ditolak** (reused/AI-slop) | Naskah orisinal + Deslop + footage no-copyright + disclosure AI (lihat §9b) |
| edge-tts kadang rate-limit/diblok | Cuma fallback; F5-TTS lokal sebagai utama. Provider lain bisa ditambah (lihat §12) |

## 9b. Monetisasi & kebijakan YouTube (penting)

**Bisa monet?** Ya, Shorts bisa dimonetisasi setelah masuk **YouTube Partner Program**:
- **1.000 subscriber** + (**10 juta views Shorts / 90 hari** ATAU **4.000 jam tonton / 12 bln**).
- Shorts dibayar dari pool iklan Shorts (~45% share setelah lisensi musik). **RPM kecil** →
  cuan dari volume.

**Risiko utama format ini** (AI voice + footage gameplay): kebijakan **"reused / inauthentic /
mass-produced content"** (diperketat 2025) bisa **menolak monetisasi** kalau dinilai
konten daur ulang tanpa nilai tambah / produksi massal repetitif.

**Cara lolos (jadikan syarat desain):**
- ✅ **Naskah orisinal & variatif** — `story_gen` wajib hindari template generik; pakai
  topic-variety guard (pola `threads_recent_topics.json` sudah ada di BIMA_CORE).
- ✅ **Reuse Deslop / anti-AI-slop** dari BIMA_CORE biar naskah gak terasa "AI slop".
- ✅ **Footage no-copyright / rekaman sendiri** (sudah jadi syarat §9).
- ✅ **Editing berciri** (subtitle, sfx, pacing) + **voice-clone khas** (F5-TTS), bukan TTS robotik.
- ✅ **Disclosure AI** — set flag "altered/synthetic content" saat upload (lihat `youtube_uploader`).
- ✅ **Musik bebas-royalti** biar gak kena klaim yang motong revenue.

### Audiens, "Made for Kids", & age-restriction
- **Horror = bukan "Made for Kids"** → tandai **not made for kids** saat upload → iklan
  personalisasi tetap aktif (revenue lebih baik). `youtube_uploader` set `selfDeclaredMadeForKids=false`.
- **Jaga advertiser-friendly**: bikin **atmospheric/suspense**, hindari gore eksplisit,
  jumpscare ekstrem, & tema sensitif (mis. bunuh diri) biar gak kena **age-restriction 18+**
  / demonetisasi. Approval gate Discord jadi filter terakhir.
- Audiens horror short-form = remaja–dewasa muda (niche kuat di TikTok/Shorts), bukan anak kecil.

### Implikasi durasi (kenapa pertimbangkan 1–2 menit juga)
- **YouTube Shorts (<60 dtk)**: monet (RPM kecil), kuat buat **discovery/reach**.
- **Video 1–2 menit**: RPM YouTube lebih tinggi, DAN **syarat TikTok Creator Rewards**
  (cuma bayar video **>1 menit**; perlu 10rb follower + 100rb views/30 hari).
- **Strategi funnel disarankan**: Shorts sebagai umpan algoritma → versi 1–2 menit sebagai
  sumber revenue utama. Pipeline sama, beda `STORY_MAX_WORDS` + batas durasi per mode.

## 10. Arsip: opsi AI video gen (masa depan, opsional)

Kalau nanti mau visual yang benar-benar "AI-generated" (bukan gameplay), jalur gratisnya:
- **HF Space via `gradio_client`** — image→video: [Wan2.2 14B](https://hf.co/spaces/r3gm/wan2-2-fp8da-aoti-preview-2),
  [LTX-2.3](https://hf.co/spaces/techfreakworm/LTX2.3-Studio) (ada audio native),
  [Cosmos3-Nano](https://hf.co/spaces/multimodalart/Cosmos3-Nano).
- Kendala: antrean & kuota **ZeroGPU**, bisa down → tetap perlu fallback.
- Bisa jadi mode alternatif `gameplay_bg` (swap sumber background) tanpa ubah pipeline lain.

## 11. Pertanyaan terbuka (konfirmasi sebelum coding Fase 1)

1. **Sumber footage** — kamu sudah punya file gameplay no-copyright? Mau Minecraft parkour,
   Subway Surfers, atau campur acak dari beberapa file?
2. **Suara narasi** — F5-TTS voice clone (perlu reference audio narator horror di
   `assets/voice/`) atau cukup edge-tts preset? Provider lain? (lihat §12)
3. **Sumber ide cerita** — LLM ngarang bebas tiap kali, atau dari daftar tema/seed yang
   kamu kasih (mis. "hantu kos", "pocong sawah", "cerita pengalaman pembaca")?
4. **Ambient/sfx** — sudah punya, atau perlu rekomendasi pack bebas-royalti?

## 12. Opsi provider TTS (perbandingan)

Desainnya **pluggable** (`TTS_PROVIDER`), jadi gampang ganti. Ringkasan opsi:

| Provider | Gratis? | Voice clone | Bahasa Indonesia | GPU | Cocok buat auto-YouTube? | Catatan |
|---|---|---|---|---|---|---|
| **F5-TTS** (lokal, Indo finetune) | ✅ unlimited | ✅ | ✅ (finetune) | Perlu | ✅ **utama** | Sudah jalan di laptop user |
| **edge-tts** | ✅ unlimited | ❌ | ✅ preset | ❌ | ✅ **fallback** | Cloud Microsoft, kadang rate-limit |
| **Azure Neural TTS** | Free tier 500rb char/bln | ❌ | ✅ banyak voice | ❌ | ⚠️ butuh akun Azure | Free tier paling royal, kualitas tinggi |
| **Piper** (lokal) | ✅ unlimited | ❌ | ✅ ada voice ID | ❌ (CPU) | ✅ | Ringan, jalan di CPU; suara agak datar |
| **Coqui XTTS-v2** (lokal) | ✅ unlimited | ✅ | ✅ multilingual | Perlu | ✅ | Clone multibahasa; proyek Coqui sudah tutup tapi model jalan |
| **ElevenLabs** | ⚠️ ~10rb kredit/bln (~10 mnt) | ✅ | ✅ | ❌ | ❌ **kurang cocok** | Free tier kecil + **wajib atribusi** & batasan komersial; cepat habis utk posting harian |
| **OpenAI TTS** | ❌ berbayar | ❌ | ✅ | ❌ | — | Per-karakter, bukan gratis |

**Kenapa ElevenLabs kurang pas di sini:** free tier-nya cuma ~10 menit audio/bulan
dan mensyaratkan **atribusi** + ada batasan penggunaan komersial. Untuk channel yang
posting tiap hari (dan tujuannya monetisasi), kuotanya cepat habis dan aturannya ribet.

**Rekomendasi:** tetap **F5-TTS** (lokal, unlimited, voice clone, sudah jalan) sebagai
utama + **edge-tts** fallback. Kalau mau tanpa GPU & kualitas tinggi: **Azure free tier**
opsi terbaik kedua. Semua bisa dipasang via `TTS_PROVIDER` tanpa ubah pipeline lain.

---
*Setelah Fase 1 disetujui & ada ≥1 file di `assets/gameplay/`, modul `tools/story_gen.py`,
`tools/tts.py`, `tools/subtitle.py`, `tools/gameplay_bg.py`, dan `tools/video_assembly.py`
dibuat lebih dulu (tanpa upload) supaya langsung menghasilkan MP4 untuk dites.*
