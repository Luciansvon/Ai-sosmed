import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger("bima_core.image_cache")

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs"
GALLERY_DIR = OUTPUT_DIR / "gallery_cache"
GALLERY_DIR.mkdir(parents=True, exist_ok=True)
METADATA_FILE = GALLERY_DIR / "gallery_metadata.json"

def _load_metadata() -> list[dict]:
    if METADATA_FILE.exists():
        try:
            return json.loads(METADATA_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Gagal memuat metadata galeri: {e}")
            return []
    return []

def _save_metadata(data: list[dict]):
    try:
        METADATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Gagal menyimpan metadata galeri: {e}")

def add_to_gallery(local_img_path: Path, prompt: str, post_text: str):
    """Menyalin gambar yang sukses diposting ke folder galeri cache dan menyimpan metadatanya."""
    if not local_img_path.exists():
        logger.warning(f"Gambar tidak ditemukan untuk disimpan ke galeri: {local_img_path}")
        return

    try:
        dest_path = GALLERY_DIR / local_img_path.name
        # Salin file ke folder galeri cache
        shutil.copy2(local_img_path, dest_path)
        
        # Simpan metadata
        metadata = _load_metadata()
        # Hindari duplikat
        if not any(item.get("filename") == local_img_path.name for item in metadata):
            metadata.append({
                "filename": local_img_path.name,
                "prompt": prompt,
                "post_text": post_text
            })
            _save_metadata(metadata)
            logger.info(f"[IMAGE_CACHE] Berhasil menyimpan gambar ke galeri cache: {local_img_path.name}")
    except Exception as e:
        logger.error(f"[IMAGE_CACHE] Gagal menambahkan gambar ke galeri cache: {e}")

def find_cached_image(post_text: str) -> Path | None:
    """Mencari gambar yang relevan dari galeri cache berdasarkan kata kunci teks postingan."""
    metadata = _load_metadata()
    if not metadata:
        return None

    # Cari kata kunci yang cocok secara sederhana
    words = [w.lower() for w in post_text.split() if len(w) > 3]
    if not words:
        return None

    best_match = None
    max_matches = 0

    for item in metadata:
        filename = item.get("filename")
        prompt = item.get("prompt", "").lower()
        cached_text = item.get("post_text", "").lower()
        
        img_path = GALLERY_DIR / filename
        if not img_path.exists():
            continue

        # Hitung kecocokan kata kunci
        matches = sum(1 for w in words if w in prompt or w in cached_text)
        if matches > max_matches and matches >= 2: # Minimal ada 2 kata yang cocok
            max_matches = matches
            best_match = img_path

    if best_match:
        logger.info(f"[IMAGE_CACHE] Menemukan gambar cache yang cocok ({max_matches} kecocokan): {best_match.name}")
        return best_match

    return None
