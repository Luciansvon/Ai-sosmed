"""Image generation via OpenRouter (default: Gemini Flash Image / Nano Banana).

Versi standalone dari `ImageGenTool` BIMA_CORE — diubah jadi fungsi `generate_image()`
biar gak butuh CrewAI. Mendukung text-to-image dan image-to-image (reference).

Output: ``"SUCCESS|<filepath>|<msg>"`` atau ``"FAILED|<error>"``.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("ai_sosmed.image_gen")

_MODEL = os.environ.get("IMAGE_GEN_MODEL", "google/gemini-3.1-flash-image-preview").strip()
_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs"
_OUTPUT_DIR.mkdir(exist_ok=True)

_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def _guess_mime(path: str) -> str:
    return _MIME_MAP.get(Path(path).suffix.lower(), "image/png")


def _prune_outputs(keep: int = 15) -> None:
    """Simpan cuma `keep` file anisa_img_*.png terbaru biar outputs/ gak numpuk tanpa batas."""
    try:
        files = sorted(
            _OUTPUT_DIR.glob("anisa_img_*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in files[keep:]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception as e:
        logger.debug(f"[IMAGE_GEN] prune skip: {e}")


def generate_image(prompt: str, reference_image_paths: list[str] | None = None) -> str:
    """Generate gambar dari prompt teks ATAU dari gambar referensi (image-to-image).

    Args:
        prompt: deskripsi gambar.
        reference_image_paths: opsional, maksimal 3 path gambar referensi (img2img).

    Returns:
        ``"SUCCESS|<filepath>|<msg>"`` kalau sukses, ``"FAILED|<error>"`` kalau gagal.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return "FAILED|Prompt kosong"

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return "FAILED|OPENROUTER_API_KEY belum diset"

    try:
        from openai import OpenAI
    except ImportError:
        return "FAILED|Package 'openai' belum terinstall — pip install openai"

    # Build multimodal content kalau ada reference image (img2img mode)
    ref_used: list[str] = []
    if reference_image_paths:
        content_parts: list[dict] = []
        for img_path in reference_image_paths[:3]:  # cap 3 ref images
            try:
                mime = _guess_mime(img_path)
                with open(img_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
                ref_used.append(img_path)
            except Exception as e:
                logger.warning(f"[IMAGE_GEN] Skip ref image {img_path}: {e}")
        content_parts.append({"type": "text", "text": prompt})
        user_content: str | list[dict] = content_parts
    else:
        user_content = prompt

    try:
        client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": user_content}],
            extra_body={"modalities": ["image", "text"]},
        )
    except Exception as e:
        logger.exception("[IMAGE_GEN] OpenRouter call gagal")
        return f"FAILED|OpenRouter call error: {e}"

    # Extract image dari response (struktur: choices[0].message.images[0].image_url.url)
    try:
        msg = resp.choices[0].message
        images = getattr(msg, "images", None)
        if not images:
            # Defensive: cek dict access
            msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else {}
            images = msg_dict.get("images") or []
        if not images:
            content_preview = (getattr(msg, "content", "") or "")[:200]
            return f"FAILED|Model gak return image (text only: {content_preview})"
        first = images[0]
        url = first["image_url"]["url"] if isinstance(first, dict) else first.image_url.url
    except Exception as e:
        return f"FAILED|Parse response error: {e}"

    if not url.startswith("data:image"):
        return f"FAILED|Format image_url tidak diharapkan: {url[:80]}"

    try:
        b64data = url.split(",", 1)[1]
        raw = base64.b64decode(b64data)
    except Exception as e:
        return f"FAILED|Base64 decode error: {e}"

    slug = hashlib.md5(prompt.encode()).hexdigest()[:8]
    fp = _OUTPUT_DIR / f"anisa_img_{slug}_{int(time.time())}.png"
    fp.write_bytes(raw)
    _prune_outputs(keep=15)

    size_kb = len(raw) // 1024
    ref_info = f" ref={len(ref_used)}" if ref_used else ""
    mode = "img2img" if ref_used else "txt2img"
    logger.info(f"[IMAGE_GEN] Saved {fp} ({size_kb} KB) model={_MODEL} mode={mode}{ref_info}")
    meta_extra = f", {mode}" if ref_used else ""
    return f"SUCCESS|{fp}|Gambar siap ({size_kb} KB, {_MODEL.split('/')[-1]}{meta_extra})"
