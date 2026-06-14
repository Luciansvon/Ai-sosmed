# 🧵 Bot_thread (Ai-sosmed)

Bot autoposter **Threads** mandiri dengan persona Gen-Z + approval lewat **Discord**.
Di-extract dari proyek BIMA_CORE biar bisa jalan & di-ulik sendiri.

## Fitur

- **Posting manual** — `!threads` (lihat tren), `!threads <nomor>` (pilih tren),
  `!threads <topik bebas>` (ide sendiri), tambah `--image` buat sertakan gambar.
- **Auto-post scheduler** — jadwal acak pagi/siang/malam (WIB). Kalau owner AFK 5 menit,
  postingan yang dinilai aman (SAFE) auto-publish; yang sensitif dibatalkan.
- **Scan & auto-reply komentar** — tiap 5 menit. Komentar spam/toxic di-skip; komentar
  ringan dibalas otomatis; sisanya minta approval.
- **Generate gambar** — via OpenRouter (Gemini Flash Image), di-host ke Catbox → Discord CDN.
- **Viral learning** — analisa pola postingan viral, simpan ke agentmemory (opsional Obsidian).
- **Approval gate** — draf dikirim ke DM Discord: 👍 publish, 👎 batal, atau **balas teks**
  buat revisi (minimal-edit, tetap perlu 👍/👎).

## Setup

```bash
# 1. Virtual env + dependency
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Konfigurasi
cp .env.example .env               # lalu isi nilainya

# 3. Jalankan
python main.py
```

### Yang wajib diisi di `.env`
| Variable | Buat apa |
|---|---|
| `OPENROUTER_API_KEY` | Generate draf + gambar |
| `DISCORD_TOKEN` | Bot Discord (approval gate) |
| `BIMA_DISCORD_USER_ID` | Penerima DM approval auto-post |
| `THREADS_ACCESS_TOKEN` | Publish ke Threads API |
| `THREADS_USERNAME` | Username Threads (tanpa @) — link permalink + filter komentar sendiri |

> **Catatan Discord:** aktifkan **Message Content Intent** di
> [Discord Developer Portal](https://discord.com/developers/applications) →
> Bot → Privileged Gateway Intents. Bot juga butuh izin baca/kirim pesan + reaksi.

### Opsional
`SERPER_API_KEY` (tren berita), `THREADS_MEDIA_CHANNEL_ID` (host gambar),
`OBSIDIAN_PATH` (simpan analisa viral), `AGENTMEMORY_URL` (viral memory),
`THREADS_LLM_MODEL`, `IMAGE_GEN_MODEL`, `BOT_STATUS_CHANNEL_ID`.
Set `ENABLE_THREADS_AUTO=false` buat matiin scheduler (manual `!threads` doang).

## Struktur

```
Bot_thread/
├── main.py                  # entry point
├── bot.py                   # Discord wiring (approval gate, reaction, revisi)
├── core/
│   ├── threads_commands.py  # draf persona, publish API, reply komentar, viral
│   ├── threads_scheduler.py # auto-post jadwal acak + scan komentar
│   ├── permission_gate.py   # approval lewat DM
│   ├── llm_config.py        # LLM via OpenRouter
│   ├── image_host.py        # host gambar (Catbox → Discord CDN)
│   ├── image_cache.py       # galeri cache gambar
│   └── agentmemory_client.py# viral memory (REST, safe no-op)
├── tools/
│   └── image_gen.py         # generate gambar (OpenRouter multimodal)
└── outputs/                 # runtime: draf, gambar, state json
```

## Asal-usul

Di-extract dari [BIMA_CORE](https://github.com/Luciansvon) (bot "Anisa"). Logika inti
(draf, publish, scheduler, viral) identik; coupling CrewAI/LangGraph dilepas biar mandiri.
