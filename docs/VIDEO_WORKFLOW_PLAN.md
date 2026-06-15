# 🎬 Rancangan Workflow Otomasi Video (YouTube Shorts + TikTok)

> Status: **DESAIN** (belum diimplementasi). Dokumen ini rencana teknis sebelum coding.
> Target: ekstensi dari Bot_thread (Ai-sosmed) biar selain auto-post Threads, bot juga
> bisa bikin **video pendek 9:16** otomatis, di-approve lewat Discord, lalu auto-upload
> ke **YouTube Shorts** (TikTok = generate file, upload manual).

## 1. Keputusan desain (sudah disepakati)

| Topik | Pilihan | Konsekuensi |
|---|---|---|
| Gaya video | **AI video generation** (klip bergerak, bukan slideshow) | Butuh model video; pilih jalur gratis |
| Biaya | **Gratis** | Pakai model open-source di **Hugging Face** (bukan Veo/Kling/Runway) |
| Voiceover | **Gratis** (TTS open-source) | Kokoro TTS / edge-tts / OmniVoice (Bahasa Indonesia) |
| Upload | **Auto YouTube, manual TikTok** | YouTube Data API v3; TikTok = file siap + notif |
| Mulai dari | **Desain dulu** | Dokumen ini; coding bertahap menyusul |

## 2. Kenapa "AI video gratis" = Hugging Face

API video komersial (Veo, Kling, Runway) mahal & berbayar. Jalur gratis yang realistis:

1. **HF Space via `gradio_client`** — panggil API publik Space dari Python. Gratis,
   tapi kena antrean & kuota **ZeroGPU**, kadang down. Cocok sebagai sumber utama.
2. **HF Inference Providers** — sebagian model video ada free-tier terbatas.
3. **Jalankan model lokal** — butuh GPU kuat (Wan2.2 14B / LTX butuh VRAM gede).
   Tidak diasumsikan tersedia.

**Strategi:** sumber utama = HF Space (image→video), **fallback = ffmpeg Ken Burns**
(pan/zoom pada gambar AI) yang 100% gratis & tanpa GPU. Jadi pipeline tetap jalan
walau Space lagi antre/mati.

### Kandidat model (per Juni 2026)
- **Video gen**:
  - LTX-2.3 — text/image→video **+ audio native** ([Studio](https://hf.co/spaces/techfreakworm/LTX2.3-Studio), [Turbo](https://hf.co/spaces/alexnasa/ltx-2-TURBO))
  - Wan2.2 14B — image→video, sangat populer ([preview](https://hf.co/spaces/r3gm/wan2-2-fp8da-aoti-preview-2))
  - NVIDIA Cosmos3-Nano — text/image→video + audio ([space](https://hf.co/spaces/multimodalart/Cosmos3-Nano))
- **TTS (narasi ID)**:
  - [Kokoro TTS](https://hf.co/spaces/hexgrad/Kokoro-TTS) — ringan, bisa self-host
  - [OmniVoice](https://hf.co/spaces/k2-fsa/OmniVoice) — voice cloning 600+ bahasa
  - `edge-tts` — gratis, neural ID (`id-ID-ArdiNeural` / `id-ID-GadisNeural`), tanpa GPU

> Catatan: ID Space berubah-ubah. Bikin Space target **configurable lewat `.env`**
> + daftar fallback, jangan hardcode satu.

## 3. Komponen existing yang di-reuse

| Modul existing | Peran di workflow video |
|---|---|
| `core/llm_config.py` | Generate **script/storyboard** + judul + deskripsi + hashtag |
| `tools/image_gen.py` | Generate **gambar per-scene** (sumber untuk image→video) |
| `core/permission_gate.py` + `bot.py` | **Approval gate**: kirim preview MP4 ke DM, 👍 publish / 👎 batal |
| `core/threads_scheduler.py` | Pola **scheduler** apscheduler untuk auto-post video terjadwal |
| `core/image_host.py` | Hosting media (kalau perlu URL publik) |

## 4. Modul baru yang dibangun

```
core/
  video_commands.py      # handler perintah !video (mirror threads_commands)
  video_scheduler.py     # jadwal auto-post video (mirror threads_scheduler) — fase lanjut
  youtube_uploader.py    # upload YouTube Data API v3 (OAuth) — kategori, #Shorts
tools/
  video_gen.py           # image→video via HF Space (gradio_client) + fallback Ken Burns
  tts.py                 # narasi TTS (edge-tts/Kokoro), balikin wav + durasi
  subtitle.py            # bikin .ass/.srt (timing dari durasi TTS / faster-whisper)
  video_assembly.py      # ffmpeg: concat klip + subtitle + mix audio+musik → MP4 9:16
outputs/
  video/                 # hasil render + artefak antara (auto-prune)
assets/
  bgm/                   # musik latar bebas-royalti (disiapkan user)
docs/
  VIDEO_WORKFLOW_PLAN.md # dokumen ini
```

## 5. Alur pipeline (storyboard → render → approval → upload)

```
[Topik/tren]
   │  (LLM: llm_config)
   ▼
[Storyboard JSON]  ── N scene: {narasi, prompt_visual, durasi_detik}
   │
   ├─► per scene: image_gen ──► image→video (HF Space) ──► klip mp4 3–5 dtk
   │                                  └─(gagal)─► fallback ffmpeg Ken Burns
   │
   ├─► per scene: TTS (tools/tts) ──► narasi wav + durasi presisi
   │
   ├─► subtitle (tools/subtitle) ──► .ass burn-in
   │
   ▼
[video_assembly ffmpeg]
   • concat klip sesuai durasi narasi
   • overlay subtitle, crop/scale 1080×1920 (9:16), total < 60 dtk
   • mix voiceover + BGM (ducking), normalize loudness
   ▼
[Preview MP4] ──► DM Discord (permission_gate) ──► 👍 / 👎 / balas = revisi
   │
   ├─ 👍 ─► YouTube auto-upload (youtube_uploader)  +  simpan file utk TikTok manual
   └─ 👎 ─► batal
```

### Detail penting
- **Durasi**: total < 60 dtk (syarat Shorts). Durasi tiap klip = durasi narasi scene-nya.
- **Rasio**: render final **1080×1920 (9:16)**.
- **Sinkronisasi**: durasi audio TTS jadi acuan; klip video di-`tpad`/`trim` agar pas.
- **Subtitle**: gaya besar tengah-bawah (TikTok-friendly), burn-in via `subtitles`/`ass` filter.
- **BGM**: file lokal bebas-royalti di `assets/bgm/`, volume diturunkan saat ada narasi (sidechain/ducking).

## 6. Dependensi baru

```
# Video gen via HF Space
gradio_client>=1.0
huggingface_hub>=0.25

# TTS gratis (default)
edge-tts>=6.1            # alternatif: kokoro / panggil Space OmniVoice via gradio_client

# (Subtitle presisi, opsional) timing kata-per-kata
faster-whisper>=1.0

# YouTube upload
google-api-python-client>=2.0
google-auth-oauthlib>=1.2
google-auth-httplib2>=0.2
```

**Binary sistem:** `ffmpeg` wajib ada di PATH (assembly + Ken Burns). Cek di startup,
kasih pesan jelas kalau belum terpasang.

> MoviePy sengaja dihindari demi kontrol & ringan; pakai `ffmpeg` langsung via subprocess.

## 7. Variabel `.env` baru

```dotenv
# === VIDEO GEN (Hugging Face) ===
HF_TOKEN=hf_xxx                       # token akun HF (untuk gradio_client / inference)
VIDEO_GEN_SPACE=r3gm/wan2-2-fp8da-aoti-preview-2   # Space image→video utama
VIDEO_GEN_FALLBACK=kenburns           # kenburns | none  (kalau Space gagal)

# === TTS ===
TTS_PROVIDER=edge                     # edge | kokoro | omnivoice
TTS_VOICE=id-ID-ArdiNeural            # suara Bahasa Indonesia

# === RENDER ===
VIDEO_BGM_PATH=assets/bgm/default.mp3 # musik latar (opsional)
VIDEO_MAX_SECONDS=58                  # batas aman < 60 dtk

# === YOUTUBE UPLOAD ===
YOUTUBE_CLIENT_SECRET_FILE=secrets/yt_client_secret.json
YOUTUBE_TOKEN_FILE=secrets/yt_token.json
YOUTUBE_PRIVACY=public                # public | unlisted | private
YOUTUBE_CATEGORY_ID=22                # 22 = People & Blogs

# === SCHEDULER (fase lanjut) ===
ENABLE_VIDEO_AUTO=false               # true = auto-post video terjadwal
```

## 8. Rencana bertahap (phasing)

| Fase | Isi | Bisa dites? |
|---|---|---|
| **0** | Scaffolding modul + dependensi + dokumen ini | — |
| **1** | `script → TTS → subtitle → Ken Burns → assembly` = MP4 lokal (tanpa HF & upload) | ✅ lokal, no GPU |
| **2** | AI video gen via HF Space (image→video) + fallback ke Fase 1 | ✅ butuh HF_TOKEN |
| **3** | Integrasi `!video <topik>` + approval gate (preview MP4 ke DM) | ✅ butuh Discord |
| **4** | YouTube auto-upload (OAuth flow + upload Shorts) | ✅ butuh GCloud OAuth |
| **5** | Scheduler auto-post + handoff TikTok manual (notif file siap) | ✅ |

**Rekomendasi:** mulai dari **Fase 1** — paling cepat kelihatan hasilnya, tanpa
ketergantungan GPU/jaringan, dan jadi fallback permanen buat Fase 2.

## 9. Risiko & mitigasi

| Risiko | Mitigasi |
|---|---|
| HF Space antre/down (ZeroGPU) | Fallback ffmpeg Ken Burns; retry + daftar Space cadangan di `.env` |
| Render berat tanpa GPU | Video-gen di-offload ke HF; assembly ffmpeg ringan di lokal |
| **TikTok API restriktif** (perlu app review, sering kepaksa draft/private) | Sesuai keputusan: TikTok = generate file + upload manual |
| YouTube OAuth ribet | One-time consent flow; simpan refresh token di `YOUTUBE_TOKEN_FILE` |
| Hak cipta musik | Pakai BGM bebas-royalti dari `assets/bgm/` yang disiapkan user |
| Klaim "AI video" tapi hasil Ken Burns | Transparan: fallback ditandai di log; kualitas tergantung ketersediaan Space |
| Durasi/rasio salah → ditolak Shorts | Enforce 9:16 & < 60 dtk di `video_assembly` |

## 10. Pertanyaan terbuka (untuk dikonfirmasi sebelum coding)

1. **Niche/persona video** — sama dengan persona Gen-Z Threads, atau tema khusus
   (fakta sains? cerita? tips?). Ada `core/scientific_facts.json` yang bisa jadi sumber konten.
2. **Bahasa** — narasi & subtitle full Indonesia, atau bilingual?
3. **Panjang target** — ~30 dtk atau mendekati 60 dtk?
4. **Punya GPU/akses Colab?** — kalau ya, opsi jalankan model lokal terbuka (kualitas lebih stabil).
5. **Musik latar** — sudah punya koleksi BGM bebas-royalti, atau perlu rekomendasi sumber?

---
*Begitu Fase 1 disetujui, modul `tools/tts.py`, `tools/subtitle.py`, `tools/video_assembly.py`,
dan `core/video_commands.py` akan dibuat lebih dulu (tanpa upload), supaya bisa langsung
menghasilkan MP4 untuk dites.*
