"""Discord wiring buat Ai-sosmed.

Layer kontrol/approval khusus Threads — di-extract dari BIMA_CORE `core/discord_bot.py`,
dibuang semua hal non-Threads (saham, arsip, qc, music, langgraph, dll).

Alur:
  • `!threads` / `!thread`  -> bikin draf + minta approval lewat DM
  • Reaksi 👍/👎 di DM       -> approve / tolak (publish atau batal)
  • Balas teks di DM         -> revisi draf (debounce 5 detik, tetap butuh 👍/👎)
  • Scheduler                -> auto-post jadwal acak + scan & balas komentar
"""
import asyncio
import logging
import os
import re
from datetime import datetime

import discord
from dotenv import load_dotenv

from core.permission_gate import (
    register_send_handler,
    resolve_approval,
    set_main_loop,
)
from core.threads_scheduler import start_threads_scheduler

logger = logging.getLogger("ai_sosmed")

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
STATUS_CHANNEL_ID = os.getenv("BOT_STATUS_CHANNEL_ID")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Rate limit per user (detik)
_rate_limit: dict[int, float] = {}

# Anti-duplikat startup notif (on_ready bisa fire ulang saat reconnect)
_startup_notified = False

# Debounce DM revisi (mencegah resolve kepagian saat masih ngetik)
_dm_debounce_timers: dict[str, asyncio.Task] = {}  # user_id -> debounce task
_dm_debounce_texts: dict[str, str] = {}            # user_id -> teks revisi terbaru

# message_id -> (req_id, user_id, action_type, details)
_discord_approval_messages: dict[int, tuple] = {}


def _build_startup_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🧵 Ai-sosmed — Online",
        description=(
            "Bot autoposter Threads aktif.\n"
            "Ketik `!threads` buat liat tren, atau `!threads <topik>` buat nulis draf."
        ),
        color=0x000000,
        timestamp=datetime.now(),
    )
    embed.add_field(
        name="Perintah",
        value=(
            "`!threads` — tren terkini\n"
            "`!threads <nomor>` — pilih tren\n"
            "`!threads <topik bebas>` — tulis ide sendiri\n"
            "`!threads <topik> --image` — sertakan gambar"
        ),
        inline=False,
    )
    embed.set_footer(text="Approval lewat DM • 👍 publish / 👎 batal / balas = revisi")
    return embed


@client.event
async def on_ready():
    global _startup_notified
    logger.info(f"🧵 Ai-sosmed online sebagai {client.user}!")

    # Register main loop buat permission gate (dipanggil dari thread scheduler/tool)
    set_main_loop(asyncio.get_running_loop())

    if _startup_notified:
        logger.info("Reconnect terdeteksi, skip startup notif duplikat")
        return
    _startup_notified = True

    # Mulai scheduler auto-post + scan komentar (opt-in via ENABLE_THREADS_AUTO=true)
    try:
        start_threads_scheduler(client)
    except Exception as e:
        logger.error(f"Gagal start threads scheduler: {e}", exc_info=True)

    if not STATUS_CHANNEL_ID:
        logger.info("BOT_STATUS_CHANNEL_ID belum di-set, skip startup notif")
        return
    try:
        channel = client.get_channel(int(STATUS_CHANNEL_ID))
        if channel is None:
            channel = await client.fetch_channel(int(STATUS_CHANNEL_ID))
        await channel.send(embed=_build_startup_embed())
        logger.info(f"✅ Startup notif terkirim ke channel {STATUS_CHANNEL_ID}")
    except ValueError:
        logger.error(f"BOT_STATUS_CHANNEL_ID bukan angka valid: {STATUS_CHANNEL_ID}")
    except Exception as e:
        logger.warning(f"Gagal kirim startup notif (non-fatal): {e}")


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user or message.author.bot:
        return

    # ── Interception DM buat revisi approval yang lagi nggantung ──
    # Debounce 5 detik: kalau owner kirim DM lagi dalam 5 detik, teks baru replace lama.
    # PENTING: revisi TIDAK auto-approve — tetap harus klik 👍/👎 di preview revisi.
    if isinstance(message.channel, discord.DMChannel):
        from core.permission_gate import get_pending_req_id_by_user
        req_id = get_pending_req_id_by_user(str(message.author.id))
        # Jangan telan command eksplisit (!threads ...) jadi "revisi" walau ada approval nggantung
        is_command = message.content.strip().lower().startswith(("!threads", "!thread"))
        if req_id and not is_command:
            revised_text = message.content.strip()
            user_id_str = str(message.author.id)

            # Cek perintah pembatalan/tolak via teks
            cancel_keywords = {"tolak", "batal", "cancel", "no", "reject", "jangan", "pembatalan", "tidak"}
            clean_text = re.sub(r"[^\w\s]", "", revised_text.lower()).strip()
            if clean_text in cancel_keywords:
                logger.info(f"[DM_APPROVAL] Pembatalan request {req_id} via teks: '{revised_text}'")
                if user_id_str in _dm_debounce_timers:
                    _dm_debounce_timers[user_id_str].cancel()
                    _dm_debounce_timers.pop(user_id_str, None)
                _dm_debounce_texts.pop(user_id_str, None)
                resolve_approval(req_id, False)
                await message.reply("❌ **Tindakan dibatalkan/ditolak.**")
                return

            # Simpan revisi terbaru + cancel timer sebelumnya (last-write-wins)
            if user_id_str in _dm_debounce_timers:
                _dm_debounce_timers[user_id_str].cancel()
            _dm_debounce_texts[user_id_str] = revised_text

            async def _finalize_revision(uid: str, rid: str):
                """Setelah 5 detik tanpa pesan baru, generate preview revisi & kirim
                pesan BARU dengan 👍/👎. TIDAK auto-approve."""
                await asyncio.sleep(5)
                final_revision_input = _dm_debounce_texts.pop(uid, revised_text)
                _dm_debounce_timers.pop(uid, None)

                # Lookup draf asli dari pesan approval tersimpan
                original_draft = None
                for _msg_id, (stored_req_id, _stored_uid, _action_type, details) in _discord_approval_messages.items():
                    if stored_req_id == rid:
                        original_draft = details
                        break

                # Generate preview revisi pakai smart revision
                try:
                    from core.threads_commands import apply_smart_revision
                    if original_draft:
                        revised_draft = await apply_smart_revision(original_draft, final_revision_input)
                    else:
                        revised_draft = final_revision_input
                except Exception as e:
                    logger.error(f"[DM_REVISION] Gagal generate smart revision: {e}")
                    revised_draft = final_revision_input

                # Simpan revised text buat di-consume saat approved
                from core.permission_gate import _revised_texts
                _revised_texts[uid] = revised_draft

                # Kirim preview revisi sebagai pesan BARU dengan 👍/👎
                try:
                    user = await client.fetch_user(int(uid))
                    if user:
                        dm_channel = user.dm_channel or await user.create_dm()
                        preview_msg = await dm_channel.send(
                            f"📝 **DRAF REVISI THREADS** 📝\n\n"
                            f"Hasil revisi berdasarkan masukan lu:\n\n"
                            f"{revised_draft[:1800]}\n\n"
                            f"👍 **Setuju & Publish**  |  👎 **Tolak/Batal**\n"
                            f"💬 Atau **balas lagi** buat revisi ulang!"
                        )
                        await preview_msg.add_reaction("👍")
                        await preview_msg.add_reaction("👎")
                        _discord_approval_messages[preview_msg.id] = (rid, uid, "THREADS_POST", revised_draft)
                        logger.info(f"[DM_REVISION] Preview revisi terkirim ke {uid}, nunggu 👍/👎")
                except Exception as e:
                    logger.error(f"[DM_REVISION] Gagal kirim preview revisi ke DM: {e}")

            task = asyncio.create_task(_finalize_revision(user_id_str, req_id))
            _dm_debounce_timers[user_id_str] = task
            await message.reply("📝 **Revisi diterima!** Tunggu 5 detik — kalau mau nambah/ganti, kirim lagi aja...")
            return

    # ── Rate limit: max 1 request / 3 detik per user ──
    uid = message.author.id
    now_ts = datetime.now().timestamp()
    if uid in _rate_limit and now_ts - _rate_limit[uid] < 3:
        return
    _rate_limit[uid] = now_ts
    for k in list(_rate_limit.keys()):
        if now_ts - _rate_limit[k] > 60:
            del _rate_limit[k]

    perintah = message.content or ""

    # ── !threads / !thread command ──
    low = perintah.lower()
    if low.startswith("!threads") or low.startswith("!thread"):
        slice_len = 8 if low.startswith("!threads") else 7
        args = perintah[slice_len:].strip()
        from core.threads_commands import handle_threads_command
        try:
            await handle_threads_command(message, args, bot_client=client)
        except Exception as e:
            logger.error(f"[THREADS] handle_threads_command error: {e}", exc_info=True)
            try:
                await message.reply(f"❌ Error: `{e}`")
            except Exception:
                pass
        return


# ============================================================
# INTERACTIVE PERMISSION GATE INTEGRATION
# ============================================================
async def send_discord_approval(
    req_id: str,
    discord_user_id: str,
    action_type: str,
    details: str,
    attachment_paths: list[str] | None = None,
) -> bool:
    try:
        user = await client.fetch_user(int(discord_user_id))
        if not user:
            logger.warning(f"[PERMISSION_GATE] User {discord_user_id} not found.")
            return False

        dm_channel = user.dm_channel or await user.create_dm()
        if action_type in ("THREADS_POST", "THREADS_REPLY"):
            msg_text = (
                f"📝 **DRAF POSTINGAN THREADS** 📝\n\n"
                f"Draf postingan buat Threads lu:\n\n"
                f"{details[:1800]}\n\n"
                f"👍 **Setuju & Publish**  |  👎 **Tolak**\n"
                f"💬 Atau **balas langsung** di sini buat revisi draftnya!"
            )
        else:
            msg_text = (
                f"⚠️ **IZIN AKSES** ⚠️\n\n"
                f"• **Tindakan**: `{action_type}`\n"
                f"• **Detail**:\n{details[:1500]}\n\n"
                f"👍 **Setuju**  |  👎 **Tolak**"
            )

        files = [discord.File(str(fp)) for fp in attachment_paths] if attachment_paths else None
        msg = await dm_channel.send(msg_text, files=files)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")

        _discord_approval_messages[msg.id] = (req_id, discord_user_id, action_type, details)
        return True
    except Exception as e:
        logger.error(f"[PERMISSION_GATE] Gagal kirim DM approval ke {discord_user_id}: {e}", exc_info=True)
        return False


register_send_handler(send_discord_approval)


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == client.user.id:
        return
    msg_id = payload.message_id
    if msg_id not in _discord_approval_messages:
        return

    req_id, target_user_id, action_type, details = _discord_approval_messages[msg_id]
    if str(payload.user_id) != target_user_id:
        return

    emoji_str = str(payload.emoji)
    try:
        channel = client.get_channel(payload.channel_id)
        if not channel:
            channel = await client.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(msg_id)
    except Exception as e:
        logger.error(f"Gagal fetch message untuk raw reaction: {e}")
        return

    if emoji_str == "👍":
        resolve_approval(req_id, True)
        try:
            await message.edit(content=f"✅ **DRAF THREADS DISETUJUI**\n\n{details[:1800]}\n\nStatus: Sukses dipublikasikan.")
        except Exception as e:
            logger.warning(f"Gagal edit approval message: {e}")
    elif emoji_str == "👎":
        resolve_approval(req_id, False)
        try:
            await message.edit(content=f"❌ **DRAF THREADS DIBATALKAN**\n\n{details[:1800]}\n\nStatus: Batal/Ditolak.")
        except Exception as e:
            logger.warning(f"Gagal edit approval message: {e}")


def run_bot():
    if DISCORD_TOKEN:
        client.run(DISCORD_TOKEN)
    else:
        logger.critical("DISCORD_TOKEN tidak ditemukan di .env!")
        raise SystemExit(1)
