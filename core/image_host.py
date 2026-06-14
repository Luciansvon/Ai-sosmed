"""Hosting gambar ke URL publik buat dipost ke Threads.

Threads API gak nerima upload byte langsung — dia nge-download gambar dari URL
publik yang kita kasih. Daripada andelin Cloudflare quick tunnel (rapuh, rotasi,
URL basi), modul ini host gambar secara berlapis:

    Catbox.moe (URL bersih permanen, tanpa akun)
        -> Discord CDN (pakai bot yang udah ada)
        -> None (pemanggil fallback ke posting teks)

URL yang dikembalikan udah diverifikasi kejangkau dulu.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger("bima_core.image_host")

CATBOX_API = "https://catbox.moe/user/api.php"


async def _verify(url: str) -> bool:
    """Pastikan URL beneran kejangkau publik sebelum diserahin ke Threads."""
    if not url:
        return False
    try:
        from core.threads_commands import url_is_fetchable
        return await url_is_fetchable(url)
    except Exception:
        # Kalau verifikator gak tersedia, jangan ngeblok — anggap valid.
        return True


async def upload_to_catbox(local_path: Path, timeout: float = 30.0) -> str | None:
    """Upload anonim ke Catbox.moe, balikin URL publik permanen (atau None)."""
    try:
        data = local_path.read_bytes()
    except Exception as e:
        logger.warning(f"[IMAGE_HOST] Gagal baca file buat Catbox: {e}")
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                CATBOX_API,
                data={"reqtype": "fileupload"},
                files={"fileToUpload": (local_path.name, data)},
            )
        url = resp.text.strip()
        if resp.status_code == 200 and url.startswith("https://"):
            logger.info(f"[IMAGE_HOST] Catbox sukses: {url}")
            return url
        logger.warning(f"[IMAGE_HOST] Catbox respon gak terduga ({resp.status_code}): {url[:120]}")
    except Exception as e:
        logger.warning(f"[IMAGE_HOST] Catbox gagal: {e}")
    return None


async def upload_to_discord(local_path: Path, client, fallback_user_id: str | None = None) -> str | None:
    """Upload gambar ke Discord (channel media via THREADS_MEDIA_CHANNEL_ID atau DM
    owner), balikin URL CDN. URL CDN Discord cukup hidup buat sekali download Threads."""
    if client is None:
        return None
    try:
        import discord
    except Exception:
        return None
    try:
        dest = None
        channel_id = os.environ.get("THREADS_MEDIA_CHANNEL_ID")
        if channel_id:
            dest = client.get_channel(int(channel_id))
            if dest is None:
                try:
                    dest = await client.fetch_channel(int(channel_id))
                except Exception:
                    dest = None
        if dest is None and fallback_user_id:
            dest = await client.fetch_user(int(fallback_user_id))
        if dest is None:
            return None

        msg = await dest.send(file=discord.File(str(local_path)))
        if msg.attachments:
            url = msg.attachments[0].url
            logger.info(f"[IMAGE_HOST] Discord CDN sukses: {url[:80]}...")
            return url
    except Exception as e:
        logger.warning(f"[IMAGE_HOST] Discord upload gagal: {e}")
    return None


async def host_image_publicly(local_path, client=None, fallback_user_id: str | None = None) -> str | None:
    """Host gambar ke URL publik: Catbox -> Discord CDN -> None.

    Balikin URL yang udah terverifikasi kejangkau, atau None kalau semua gagal
    (biar pemanggil posting teks aja, gak gagal total).
    """
    local_path = Path(local_path)
    if not local_path.exists():
        logger.warning(f"[IMAGE_HOST] File gambar gak ada: {local_path}")
        return None

    url = await upload_to_catbox(local_path)
    if url and await _verify(url):
        return url

    url = await upload_to_discord(local_path, client, fallback_user_id)
    if url and await _verify(url):
        return url

    logger.warning("[IMAGE_HOST] Semua provider hosting gambar gagal; fallback ke teks.")
    return None
