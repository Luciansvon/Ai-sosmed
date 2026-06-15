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
| Voiceover | **TTS gratis** (edge-tts, suara ID) | Tanpa API key, ada timing kata buat subtitle |
| Subtitle | Burn-in sinkron (karaoke-style) | Dari word-boundary TTS |
| Upload | **Auto YouTube, manual TikTok** | YouTube Data API v3; TikTok = file siap |
| Mulai dari | **Desain dulu** → lalu Fase 1 | Coding bertahap |

> Catatan: opsi **AI video generation** (HF Space: Wan2.2 / LTX-2.3) di-arsip sebagai
> kemungkinan masa depan, bukan jalur utama — lihat §10.

## 2. Kenapa pendekatan ini pas buat "gratis"

- **Tanpa GPU / tanpa model video** — visual cuma footage yang sudah ada, dipotong &
  di-crop pakai ffmpeg.
- **TTS gratis tanpa kuota** — `edge-tts` (suara neural Microsoft) gratis, tanpa API key,
  dan **mengembalikan timing per kata** → subtitle otomatis presisi.
- **LLM sudah ada** — `core/llm_config.py` (OpenRouter) buat nulis naskah horror.
- **Ringan** — semua proses berat (render) cuma ffmpeg, jalan di mesin biasa.

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
tools/
  story_gen.py           # naskah horror via LLM (hook 3 dtk pertama, ending nge-twist)
  tts.py                 # edge-tts → narasi.mp3 + daftar word-timing
  subtitle.py            # word-timing → .ass burn-in (gaya horror, tengah-bawah)
  gameplay_bg.py         # pilih footage acak, potong sepanjang narasi, crop center 9:16
  video_assembly.py      # ffmpeg: gabung bg + subtitle + narasi + ambient → MP4 9:16
outputs/
  video/                 # hasil render + artefak antara (auto-prune)
assets/
  gameplay/              # footage gameplay no-copyright (disiapkan user)
  sfx/                   # ambient/jumpscare/whoosh (opsional, bebas-royalti)
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
   ├─► TTS (tools/tts: edge-tts) ──► narasi.mp3 + word-timing[]
   │                                        │
   │                                        ▼
   │                                 [subtitle.ass]  (karaoke-style, sinkron kata)
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
- **Subtitle**: timing dari word-boundary edge-tts (gratis & presisi, tanpa Whisper).
  Gaya teks gede, kontras tinggi, 1–3 kata per baris (gaya TikTok horror).
- **Audio**: narasi sebagai track utama; ambient horror pelan di latar (volume ~15%,
  ducking saat ada suara); sfx jumpscare opsional di klimaks.
- **Suara TTS**: `id-ID-ArdiNeural` (pria) cocok buat horror; bisa turunkan pitch/tempo
  sedikit via ffmpeg `asetrate`/`atempo` biar makin nyeremin.

## 6. Dependensi baru

```
# TTS gratis (default) — sekaligus sumber timing subtitle
edge-tts>=6.1

# YouTube upload
google-api-python-client>=2.0
google-auth-oauthlib>=1.2
google-auth-httplib2>=0.2
```

**Binary sistem:** `ffmpeg` wajib di PATH (potong, crop, mux, subtitle burn-in).
Cek saat startup; kasih pesan jelas kalau belum ada.

> Tidak butuh `gradio_client`/`huggingface_hub`/GPU di jalur utama. MoviePy dihindari —
> pakai `ffmpeg` langsung via subprocess biar ringan & terkontrol.

## 7. Variabel `.env` baru

```dotenv
# === TTS ===
TTS_VOICE=id-ID-ArdiNeural            # suara narasi horror (pria); GadisNeural utk wanita
TTS_PITCH=-3Hz                        # opsional, bikin lebih dalam
TTS_RATE=-5%                          # opsional, lebih pelan/mencekam

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
| **1** | `story_gen → tts → subtitle → gameplay_bg → assembly` = MP4 lokal (tanpa upload) | ✅ lokal, no GPU, no API kecuali LLM+edge-tts |
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
| Subtitle meleset dari suara | Pakai word-timing edge-tts (presisi); fallback faster-whisper kalau perlu |
| Durasi > 60 dtk → ditolak Shorts | Batasi naskah (`STORY_MAX_WORDS`) + hard-trim di assembly |
| **TikTok API restriktif** | Sesuai keputusan: TikTok = generate file + upload manual |
| YouTube OAuth ribet | One-time consent; simpan refresh token di `YOUTUBE_TOKEN_FILE` |
| Konten horror terlalu ekstrem | Naskah lewat approval gate Discord sebelum publish |
| edge-tts kadang rate-limit/diblok | Retry + fallback voice; opsi provider TTS lain (Kokoro) menyusul |

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
2. **Suara narasi** — pria (`ArdiNeural`) atau wanita (`GadisNeural`)? Mau efek pitch
   lebih dalam/serem?
3. **Sumber ide cerita** — LLM ngarang bebas tiap kali, atau dari daftar tema/seed yang
   kamu kasih (mis. "hantu kos", "pocong sawah", "cerita pengalaman pembaca")?
4. **Ambient/sfx** — sudah punya, atau perlu rekomendasi pack bebas-royalti?

---
*Setelah Fase 1 disetujui & ada ≥1 file di `assets/gameplay/`, modul `tools/story_gen.py`,
`tools/tts.py`, `tools/subtitle.py`, `tools/gameplay_bg.py`, dan `tools/video_assembly.py`
dibuat lebih dulu (tanpa upload) supaya langsung menghasilkan MP4 untuk dites.*
