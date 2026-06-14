import os
import re
import logging
import httpx
import asyncio
from collections import OrderedDict
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from core.llm_config import default_llm, get_langchain_llm
from core.permission_gate import request_permission

logger = logging.getLogger('bima_core.threads')


def _threads_post_url(post_id: str) -> str:
    """Bangun permalink Threads dari THREADS_USERNAME (.env). Fallback ke URL tanpa
    handle kalau username gak diset — biar gak ada akun pribadi ke-hardcode di repo."""
    username = os.environ.get("THREADS_USERNAME", "").lstrip("@").strip()
    if username:
        return f"https://www.threads.net/@{username}/post/{post_id}"
    return f"https://www.threads.net/post/{post_id}"

# Cache global untuk menyimpan topik tren per user
# user_id -> list of {"title": "...", "snippet": "..."}
_cached_trends = {}

# Map untuk melacak search_context_info per draf postingan agar groundings terjaga saat revisi.
# Pakai LRU bounded biar gak bocor memori di bot yang nyala berhari-hari: tiap draf/revisi
# nambah entri, jadi tanpa batas dict ini tumbuh terus.
class _BoundedContextStore(OrderedDict):
    """Dict ber-LRU sederhana — simpan maksimal `max_size` entri konteks draf,
    entri paling lama dibuang begitu lewat batas."""

    def __init__(self, max_size: int = 200):
        super().__init__()
        self._max_size = max_size

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self._max_size:
            self.popitem(last=False)


_draft_contexts = _BoundedContextStore(max_size=200)

# Inisialisasi Threads-specific LLM (menggunakan Claude 3.5 Sonnet untuk hasil paling manusiawi)
threads_llm = get_langchain_llm(os.environ.get("THREADS_LLM_MODEL", "anthropic/claude-sonnet-4.6"), max_tokens=1000)

BIMA_SYSTEM_PROMPT = """Role: Lu adalah anak muda Gen-Z, tech enthusiast, dan gadget geek umum yang nulis postingan buat Threads. Lu suka ngulik teknologi, game PC/konsol, kopi, musik, dan random thoughts sehari-hari.

ATURAN PENULISAN PALING PENTING — WAJIB DIIKUTI:
1. PENDEK. Postingan ideal cuma 1-3 kalimat. Maksimal 4 kalimat. Kayak ngobrol sama temen, bukan nulis essay. Contoh postingan bagus:
   - "Kemane aja gua Chikuro enak bgt"
   - "Di kondisi ekonomi sekarang pada pake blend full arabica harga berapaaaaaaa?"
   - "1M sehari bisa buat beli le creuset brp tuh ya😏"
   - "Guys, katanya besok ada demo di depan bank BI.. Kalo besok kalian ke semarang, better hindarin simpang lima deh"
2. NATURAL. Nulis kayak orang beneran nge-post, bukan kayak copywriter atau AI. Jangan terlalu berusaha lucu. Jangan pake banyak paragraf.
3. EMOJI MINIMAL. Maksimal 1-2 emoji per postingan. Boleh gak pake emoji sama sekali. Emoji yang oke: 🗿 😭 💀 🫠 🤡 😏 🔥 🙏. JANGAN pake emoji AI khas: ✨ 🚀 💡 😊 🤖. Lu juga boleh pakai text emoticons jadul/kasual sesekali (contoh: :D, :) atau xd) biar berasa manusia nyata.
4. TANPA HASHTAG. Jangan pake hashtag sama sekali kecuali diminta.
5. FORMAT PERTANYAAN boleh dipake buat mancing engagement (contoh: "...harga berapaaaa?", "kalian gimana sih?", "gua doang apa yang ngerasa...?")
6. DETAIL & MERK SPESIFIK bikin menarik — sebut nama brand atau produk premium (contoh: mat yoga Manduka, kursi Herman Miller, RTX 4090, Le Creuset) atau angka konkret agar terasa nyata dan relatable.
7. HOOK FAKTA MENARIK / KONTRADIKTIF — Buka postingan dengan fakta unik atau opini kontradiktif yang menantang pemikiran umum untuk memancing rasa penasaran pembaca. Contoh: "Banyak orang mikir Z, padahal...", "Baru tahu ternyata Y...", "Investasi X itu sebenarnya..."
8. ANTI-TEMPLATE / STRUKTUR BEDA-BEDA — JANGAN pernah menggunakan formula kalimat yang sama berulang-ulang di postingan berturut-turut. JANGAN selalu memulai dengan kata "Baru tau", "Baru tahu", atau "Tahu gak sih". JANGAN selalu membandingkan "fakta menarik, meanwhile hidup gua...". JANGAN terlalu sering memakai kata "literally". Variasikan pembuka: gunakan pernyataan langsung, pertanyaan retoris, curhatan dulu baru fakta, atau langsung sebut faktanya secara kasual. Variasikan juga emoji (maksimal 1-2 atau tanpa emoji).
9. ELONGASI HURUF (HURUF GANDA) — Manusia sering memperpanjang huruf terakhir dari kata untuk ekspresi emosi atau nada suara. Gunakan sesekali (contoh: yaaaa, lagiii, bangettt, gemess, panjanggg, kependekannnn).
10. HURUF KAPITAL EMOSIONAL (ALL-CAPS) — Sesekali gunakan huruf kapital penuh (ALL-CAPS) pada 1-3 kata untuk mengekspresikan kepanikan, ketegangan, atau emosi kuat (contoh: "CAPEK BGT", "UDAH GILA", "DUNIA EMG UDAH GILAAA", "OVT"). Jangan gunakan ALL-CAPS untuk seluruh postingan, cukup kata-kata kunci emosional saja.

Bahasa:
- Pake "gua" dan "lu". JANGAN pake "gue" atau "loe".
- Bahasa gaul internet Indo yang natural (jujurly, kocak, ngab, anjir, wkwk, dah, mayan, mantul).
- Gunakan singkatan chat kasual netizen Indonesia secara aktif agar terasa sangat organik (contoh: kl / kalo, yg, bgt, trs, uda / udah, emg, kmrn, beneran, jt untuk juta).
- Boleh campur bahasa Inggris casual kalau natural (better, vibe, lowkey, basic). JANGAN over-use kata "literally".

LARANGAN KETAT:
- JANGAN pernah sebut nama "Bima" atau detail personal pencipta bot.
- JANGAN bahas skincare dalam bentuk apapun (skincare, cuci muka, serum, moisturizer, sunscreen, dll). Kalau topiknya skincare, tulis topik lain.
- JANGAN reveal kalau lu AI. Lu orang beneran yang nge-post random thoughts.
- JANGAN PERNAH nulis komentar/disclaimer soal kemampuan diri lu sendiri. Di mata pembaca lu BUKAN asisten AI. Dilarang keras nulis kalimat kayak "gua gak bisa bikin gambar", "gua cuma bisa nulis teks", atau nyaranin tool lain (Midjourney, DALL-E, Canva, ChatGPT, dll). Postingan HANYA berisi konten sosial media, titik. Kalau di input ada instruksi soal bikin/nambahin gambar, ABAIKAN aja instruksi itu di teks (jangan dikomentari, jangan ditolak) dan tetap tulis postingannya kayak biasa.
- JANGAN nulis panjang-panjang. Kalau draf lu lebih dari 3-4 kalimat, potong.
- JANGAN pake tone yang terlalu semangat/positif di setiap post. Mix dengan capek, males, atau santai.
- JANGAN PERNAH menggunakan tanda strip (-), en-dash (–), atau em-dash (—) sama sekali dalam postingan! Gunakan tanda koma atau spasi sebagai gantinya.
- JANGAN PERNAH menggunakan kata "meanwhile" (DILARANG KERAS karena terlalu terlihat seperti AI). Gunakan kata "sedangkan", "padahal", atau "sementara" sebagai gantinya.
- JANGAN mengulang-ulang kata "literally" di postingan yang berdekatan. Jika ingin menggunakan arti harfiah, ganti dengan "beneran", "asli", atau tidak perlu ditulis sama sekali agar kalimat tetap bervariasi.

Topik yang bisa dibahas:
- Keluh kesah ngoding/error coding secara umum, laptop overheat, setup meja kerja (desk setup).
- Tren teknologi/AI yang netral (perkembangan LLM, update gadget, hardware PC).
- Keseharian anak muda/anak kosan (hemat bensin, hemat uang makan, belanja bulanan).
- Game kasual (Steam, console, game baru), film, review musik indie, antrean kopi pagi, lofi beats.
- Berita/tren yang dikasih di konteks (ekonomi, dolar, berita viral) — react natural.
- Meme Indonesia yang lagi trending.

Safety: Boleh sarkas dan cynical tapi jangan toxic, hate speech, atau harassing orang.

Limit: Seluruh postingan HARUS di bawah 500 karakter. Idealnya di bawah 200 karakter.
"""

# Pola permintaan gambar — dibuang dari topik sebelum masuk ke LLM teks, biar
# LLM gak ke-trigger nulis penolakan "gua gak bisa bikin gambar".
_IMAGE_REQUEST_RE = re.compile(
    r"\b(--image|-img|pa(kai|ke)\s+gambar|dengan\s+gambar|sama\s+gambar(nya)?|"
    r"plus\s+gambar|tambah(in|kan)?\s+gambar|buat(in|kan)?\s+gambar|"
    r"bikin(in|kan)?\s+gambar|generate\s+gambar|kasih\s+gambar|"
    r"pa(kai|ke)\s+visual|dengan\s+visual|sertakan\s+gambar)\b",
    re.IGNORECASE,
)

# Pola "disclaimer kemampuan AI" yang gak boleh bocor ke isi postingan, mis:
# "gua gak bisa generate gambar, coba pake Midjourney/DALL-E/Canva AI".
_CAPABILITY_DISCLAIMER_RE = re.compile(
    r"((gak|ga|nggak|ngga|tidak)\s*bisa|gabisa|cuma\s+bisa)\b[^.\n]{0,40}\b(gambar|generate|visual|teks)"
    r"|coba\s+(pa(kai|ke)|gunakan)[^.\n]{0,60}(midjourney|dall|canva|visualnya|buat\s+(gambar|visual))"
    r"|soal\s+generate\s+gambar",
    re.IGNORECASE,
)


def _strip_image_request(text: str) -> str:
    """Buang frasa permintaan gambar dari topik (generate gambar ditangani jalur lain)."""
    cleaned = _IMAGE_REQUEST_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.-")
    return cleaned or text


def _scrub_capability_disclaimer(text: str) -> str:
    """Buang paragraf yang berisi disclaimer kemampuan AI supaya gak bocor ke postingan."""
    kept = [p for p in text.split("\n") if not _CAPABILITY_DISCLAIMER_RE.search(p)]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return cleaned if cleaned else text


def clean_bima_text(text: str, no_strip: bool = False) -> str:
    """Post-processing filter untuk memastikan gaya bahasa Bima dipatuhi secara ketat."""
    text = _scrub_capability_disclaimer(text.strip())
    text = text.strip()
    # Hapus tanda petik bungkus di awal/akhir jika di-generate LLM
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    if text.startswith("'") and text.endswith("'"):
        text = text[1:-1].strip()
        
    # Replace loe/Loe -> lu
    text = re.sub(r'\bloe\b', 'lu', text, flags=re.IGNORECASE)
    # Replace gue/Gue -> gua
    text = re.sub(r'\bgue\b', 'gua', text, flags=re.IGNORECASE)
    
    # Larangan Keras Strip/Minus/Dash secara global (selalu dibersihkan)
    # Ganti em-dash dan en-dash dengan koma
    text = text.replace('—', ', ').replace('–', ', ')
    # Ganti hyphen biasa (-) dengan spasi
    text = text.replace('-', ' ')
    
    # Larangan kata "meanwhile" secara global (ganti dengan padanannya)
    text = re.sub(r'\bmeanwhile\b', 'sedangkan', text, flags=re.IGNORECASE)
    
    # Bersihkan spasi ganda
    text = re.sub(r' +', ' ', text)
    
    return text

async def shorten_draft_cleanly(text: str) -> str:
    """Meringkas draf secara cepat jika melebihi batas karakter agar tidak terpotong kasar."""
    if len(text) <= 480:
        return text
    system_prompt = """Lu adalah asisten editorial B.I.M.A Core.
Tugas lu adalah menyingkat draf postingan Threads yang terlalu panjang agar di bawah 480 karakter.

ATURAN:
- JANGAN hilangkan gaya bahasa asli (sarkas, gaul, singkatan).
- JANGAN potong di tengah kalimat. Hapus kalimat/bagian yang kurang penting agar draf menjadi padat dan utuh.
- Tetap gunakan kata "lu" dan "gua".
- Output HANYA teks draf yang sudah diringkas, tanpa penjelasan, tanpa tanda kutip.

Draf Asli:
\"\"\"
{text}
\"\"\""""
    try:
        from core.llm_config import default_llm
        from langchain_core.messages import SystemMessage
        resp = await asyncio.to_thread(
            default_llm.invoke,
            [SystemMessage(content=system_prompt.format(text=text))]
        )
        short_text = resp.content.strip().strip('"').strip("'")
        if 0 < len(short_text) <= 480:
            logger.info(f"[THREADS_CLEAN_TRUNCATE] Sukses meringkas draf dengan LLM dari {len(text)} ke {len(short_text)} karakter.")
            return short_text
    except Exception as e:
        logger.warning(f"[THREADS_CLEAN_TRUNCATE] Gagal meringkas draf dengan LLM: {e}")
    
    # Fallback jika LLM gagal atau masih terlalu panjang
    clipped = text[:480]
    last_punc = max(clipped.rfind('.'), clipped.rfind('?'), clipped.rfind('!'), clipped.rfind('\n'))
    if last_punc > 300:
        return clipped[:last_punc + 1].strip()
    return clipped.strip()

async def evaluate_auto_reply(comment_text: str, post_text: str) -> tuple[bool, str]:
    """Menilai apakah komentar cukup sederhana/aman untuk dibalas secara otomatis, dan menghasilkan draf balasannya."""
    system_prompt = """Lu adalah sistem asisten interaksi otomatis B.I.M.A Core.
Tugas lu adalah mendeteksi apakah komentar Threads berikut sangat sederhana, netral, dan aman untuk dibalas secara otomatis tanpa persetujuan manual.

Kriteria komentar AMAN dibalas otomatis:
- Komentar berisi tawa/geli (wkwk, haha, lol, kocak, gokil).
- Komentar berisi pujian/persetujuan singkat (mantap, keren, setuju ngab, sepakat, sepakaat).
- Komentar berupa emoji saja.
- Komentar berisi sapaan kasual biasa (halo, pagi, siang, sore, semangat).

Kriteria TIDAK AMAN (is_safe_auto harus false):
- Komentar menanyakan hal teknis mendalam, berdiskusi panjang, opini sensitif, mengandung kritik tajam, sarkasme negatif, politik, SARA, skincare, atau bernada serius/emosional tinggi.

Aturan Balasan Otomatis (jika is_safe_auto = true):
- Balasan harus sangat pendek (1 kalimat kasual, maks 15 kata).
- Pake gaya bahasa gaul chat Indonesia yang natural, pake "gua/lu" (contoh: "wkwk asli", "gokil emang", "sepakat ngab :D", "halo juga").

Kembalikan HANYA teks JSON valid seperti di bawah, tanpa markdown, tanpa teks tambahan:
{
  "is_safe_auto": true,
  "reply_text": "draf balasan"
}"""
    try:
        from core.llm_config import default_llm
        from langchain_core.messages import SystemMessage, HumanMessage
        import json
        resp = await asyncio.to_thread(
            default_llm.invoke,
            [SystemMessage(content=system_prompt), HumanMessage(content=f"Postingan Kita: {post_text}\nKomentar Dia: {comment_text}")]
        )
        content = resp.content.strip()
        if content.startswith("```"):
            content = content.replace("```json", "").replace("```", "").strip()
        data = json.loads(content)
        is_safe = data.get("is_safe_auto", False)
        reply_text = data.get("reply_text", "").strip()
        return is_safe, reply_text
    except Exception as e:
        logger.warning(f"[AUTO_REPLY_EVAL] Gagal mengevaluasi auto reply: {e}")
        return False, ""

async def generate_bima_draft(user_prompt: str, no_strip: bool = False, max_retries: int = 3) -> str:
    """Generate Bima's Threads draft with robust validation and retry logic."""
    current_prompt = user_prompt
    for attempt in range(max_retries):
        try:
            logger.info(f"[THREADS_GEN] Memanggil LLM untuk draf (percobaan {attempt + 1}/{max_retries})...")
            resp = await asyncio.to_thread(
                threads_llm.invoke,
                [SystemMessage(content=BIMA_SYSTEM_PROMPT), HumanMessage(content=current_prompt)]
            )
            raw_content = resp.content if resp and hasattr(resp, 'content') else ""
            if not raw_content:
                logger.warning(f"[THREADS_GEN] Respons LLM kosong pada percobaan {attempt + 1}")
                await asyncio.sleep(1)
                continue
                
            draft_text = clean_bima_text(raw_content, no_strip=no_strip)
            if not draft_text or len(draft_text.strip()) == 0:
                logger.warning(f"[THREADS_GEN] Draf teks kosong setelah pembersihan pada percobaan {attempt + 1}")
                await asyncio.sleep(1)
                continue
                
            # Pastikan jika no_strip, benar-benar tidak ada tanda strip/minus/dash sisa
            if no_strip:
                draft_text = draft_text.replace('—', ', ').replace('–', ', ').replace('-', ' ')
                draft_text = re.sub(r' +', ' ', draft_text)
                
            if len(draft_text) > 480:
                draft_text = await shorten_draft_cleanly(draft_text)
            return draft_text
        except Exception as e:
            logger.error(f"[THREADS_GEN] Error pada percobaan {attempt + 1}: {e}")
            await asyncio.sleep(1)
            
    # Final fallback if still too long in the last attempt, truncate it
    if 'draft_text' in locals() and draft_text:
        logger.warning(f"[THREADS_GEN] Draft still exceeds 500 chars after {max_retries} attempts. Summarizing cleanly.")
        return await shorten_draft_cleanly(draft_text)
        
    raise ValueError("Gagal mendapatkan draf postingan Threads yang valid (respons kosong dari AI setelah beberapa percobaan).")

async def fetch_indonesian_trends() -> list[dict]:
    """Mengambil berita viral & tren terbaru di Indonesia menggunakan Serper API."""
    api_key = os.environ.get("SERPER_API_KEY")
    if not api_key:
        logger.warning("[THREADS] SERPER_API_KEY tidak ditemukan untuk pencarian tren.")
        return []
    
    url = "https://google.serper.dev/search"
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json"
    }
    
    # Kueri untuk berita viral umum & game di Indonesia
    queries = [
        "berita viral hari ini indonesia terbaru",
        "tren game populer indonesia terbaru 2026"
    ]
    
    trends = []
    async with httpx.AsyncClient() as client:
        for query in queries:
            try:
                payload = {"q": query, "num": 3, "gl": "id", "hl": "id"}
                resp = await client.post(url, json=payload, headers=headers, timeout=10)
                if resp.status_code == 200:
                    organic = resp.json().get("organic", [])
                    for item in organic:
                        title = item.get("title", "")
                        snippet = item.get("snippet", "")
                        if title and snippet:
                            trends.append({"title": title, "snippet": snippet})
            except Exception as e:
                logger.error(f"[THREADS_TRENDS] Gagal mencari query '{query}': {e}")
                
    return trends[:4]  # Ambil top 4 tren saja

async def search_context(topic: str) -> str:
    """Mengambil konteks fakta dari Google Search untuk topik tertentu."""
    api_key = os.environ.get("SERPER_API_KEY")
    if not api_key:
        return "Tidak ada konteks internet tambahan."
    
    url = "https://google.serper.dev/search"
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json"
    }
    payload = {"q": topic, "num": 4, "gl": "id", "hl": "id"}
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=10)
            if resp.status_code == 200:
                organic = resp.json().get("organic", [])
                context_lines = []
                for item in organic:
                    context_lines.append(f"- {item.get('title')}: {item.get('snippet')}")
                return "\n".join(context_lines)
    except Exception as e:
        logger.error(f"[THREADS_CONTEXT] Gagal mencari konteks untuk '{topic}': {e}")
        
    return "Tidak ada konteks internet tambahan."


async def url_is_fetchable(url: str, timeout: float = 8.0) -> bool:
    """Cek apakah URL gambar beneran bisa diakses publik, biar Threads bisa download-nya.

    Tunnel quick `trycloudflare` suka rotasi/mati, jadi URL yang dibaca dari log bisa
    basi. Return False kalau tunnel mati / URL gak kejangkau / respon >= 400, supaya
    pemanggil bisa fallback posting teks aja (gak ikut gagal 400 gara-gara gambar).
    """
    if not url:
        return False
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.head(url, timeout=timeout)
            # Sebagian origin gak support HEAD — coba GET ringan sebagai cadangan.
            if resp.status_code >= 400:
                resp = await client.get(url, timeout=timeout)
            return resp.status_code < 400
    except Exception as e:
        logger.warning(f"[THREADS] URL gambar gak kejangkau ({url[:60]}...): {e}")
        return False


class ThreadsValidationError(ValueError):
    """Exception raised when post content fails Threads validation (e.g. character limit)."""
    pass

async def publish_post_to_threads(text: str, token: str, reply_to_id: str | None = None, image_url: str | None = None, max_retries: int = 3) -> str:
    """Mempublikasikan postingan teks/gambar atau balasan secara riil ke Threads API dengan retry untuk 5xx."""
    backoff_delays = [2, 5, 10]
    last_error = None
    
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as api_client:
                # Langkah 1: Buat media container
                container_url = "https://graph.threads.net/v1.0/me/threads"
                if image_url:
                    payload = {
                        'media_type': 'IMAGE',
                        'image_url': image_url,
                        'text': text,
                        'access_token': token
                    }
                else:
                    payload = {
                        'media_type': 'TEXT',
                        'text': text,
                        'access_token': token
                    }
                if reply_to_id:
                    payload['reply_to_id'] = reply_to_id
                    
                resp = await api_client.post(container_url, data=payload, timeout=15)
                # Kalau 4xx (client error), langsung raise — gak perlu retry
                if 400 <= resp.status_code < 500:
                    try:
                        err_json = resp.json()
                        err_msg = err_json.get("error", {}).get("message", "")
                        logger.error(f"[THREADS_PUB] Client error response (container creation): {err_json}")
                        if err_msg:
                            raise Exception(f"Client error {resp.status_code}: {err_msg}")
                    except Exception as e:
                        if "Client error" in str(e):
                            raise e
                    resp.raise_for_status()
                # Kalau 5xx, retry
                if resp.status_code >= 500:
                    # Cek apakah ini sebenarnya error limit karakter
                    try:
                        err_json = resp.json()
                        err_msg = err_json.get("error", {}).get("message", "")
                        if "must be at most 500 characters" in err_msg or "character" in err_msg.lower():
                            raise ThreadsValidationError(f"Limit karakter terlampaui (maks 500 karakter). Detail: {err_msg}")
                    except (ValueError, KeyError, TypeError) as parse_err:
                        if isinstance(parse_err, ThreadsValidationError):
                            raise
                    raise httpx.HTTPStatusError(
                        f"Server error {resp.status_code}",
                        request=resp.request,
                        response=resp
                    )
                resp.raise_for_status()
                creation_id = resp.json().get('id')
                
                # Langkah 2: Publikasikan container
                publish_url = "https://graph.threads.net/v1.0/me/threads_publish"
                resp_pub = await api_client.post(publish_url, data={
                    'creation_id': creation_id,
                    'access_token': token
                }, timeout=15)
                if 400 <= resp_pub.status_code < 500:
                    try:
                        err_json = resp_pub.json()
                        err_msg = err_json.get("error", {}).get("message", "")
                        logger.error(f"[THREADS_PUB] Client error response (publish): {err_json}")
                        if err_msg:
                            raise Exception(f"Client error {resp_pub.status_code}: {err_msg}")
                    except Exception as e:
                        if "Client error" in str(e):
                            raise e
                    resp_pub.raise_for_status()
                if resp_pub.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"Server error {resp_pub.status_code}",
                        request=resp_pub.request,
                        response=resp_pub
                    )
                resp_pub.raise_for_status()
                post_id = resp_pub.json().get('id')
                
                logger.info(f"[THREADS_PUB] Berhasil publish di percobaan {attempt + 1}")
                return post_id
        except ThreadsValidationError as e:
            # Jangan retry kalau error validasi karakter
            raise
        except httpx.HTTPStatusError as e:
            last_error = e
            if e.response and 400 <= e.response.status_code < 500:
                # Client error — jangan retry
                raise
            delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
            logger.warning(f"[THREADS_PUB] Percobaan {attempt + 1}/{max_retries} gagal (5xx): {e}. Retry dalam {delay}s...")
            await asyncio.sleep(delay)
        except Exception as e:
            last_error = e
            delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
            logger.warning(f"[THREADS_PUB] Percobaan {attempt + 1}/{max_retries} error: {e}. Retry dalam {delay}s...")
            await asyncio.sleep(delay)
    
    raise last_error or Exception("Gagal publish ke Threads setelah semua percobaan retry.")

async def apply_smart_revision(draft_text: str, user_reply: str, search_context_info: str = "") -> str:
    """Memproses teks balasan revisi dari Bima secara cerdas menggunakan LLM."""
    if not search_context_info:
        search_context_info = _draft_contexts.get(draft_text.strip(), "")

    # Cek apakah feedback Bima meminta pencarian/browsing baru
    detected_query = ""
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        detect_system = """Analisis apakah feedback/balasan user meminta pencarian informasi tambahan (browsing/searching/mencari informasi di internet/Google/mastiin info/cek berita).
        
Kriteria:
- Jika user meminta mencari info baru, cek berita, browsing tren, atau memverifikasi fakta di internet (misal: "coba browsing dulu soal...", "cari tahu dong tentang...", "cek google bener gak...", "mastiin info X").
- Kembalikan HANYA kueri pencarian (search query) yang cocok untuk dimasukkan ke Google Search. Contoh: "kejadian demo simpang lima hari ini", "harga emas antam terbaru".
- Jika user HANYA minta revisi gaya penulisan, edit kata-kata biasa, kritik konten, atau langsung setuju tanpa minta cari info baru (misal: "ganti tahu tempe jadi martabak", "bikin lebih pendek", "sip publish"), kembalikan string kosong "".

Kembalikan HANYA kueri pencarian atau string kosong. Tanpa penjelasan, tanpa tanda kutip."""
        
        # Panggil LLM cepat untuk deteksi
        resp_detect = await asyncio.to_thread(
            threads_llm.invoke,
            [SystemMessage(content=detect_system), HumanMessage(content=f"Feedback User: {user_reply}")]
        )
        detected_query = resp_detect.content.strip().replace('"', '').replace("'", "")
    except Exception as e:
        logger.warning(f"[THREADS_REVISION] Gagal mendeteksi kebutuhan browsing: {e}")

    if detected_query and len(detected_query) > 2:
        logger.info(f"[THREADS_REVISION] Bima meminta browsing baru untuk kueri: '{detected_query}'")
        new_context = await search_context(detected_query)
        if new_context:
            search_context_info = f"{search_context_info}\n\n=== HASIL BROWSING BARU ({detected_query}) ===\n{new_context}"
            logger.info("[THREADS_REVISION] Berhasil mendapatkan konteks browsing baru.")

    system_prompt = f"""Lu adalah Anisa, asisten nulis B.I.M.A Core.
Bima lagi review draf postingan Threads.

Konteks Fakta Internet/Berita Terkini yang Digunakan:
\"\"\"
{search_context_info}
\"\"\"

Draf Asal:
\"\"\"
{draft_text}
\"\"\"

Balasan/Feedback Bima:
\"\"\"
{user_reply}
\"\"\"

Tugas:
Tentuin apakah balasan Bima itu postingan final yang dia mau langsung publish, ATAU instruksi/feedback buat revisi draf.

- Kalau balasan Bima itu postingan final lengkap: Return persis apa adanya.
- Kalau balasan Bima itu instruksi (misal "ganti tahu tempe jadi martabak", "bikin lebih pendek", "tambahin X"): Terapkan feedback ke Draf Asal. Gunakan Konteks Fakta Internet/Berita Terkini di atas jika relevan.

PALING PENTING (MINIMAL EDIT, WAJIB DIIKUTI):
- Ubah HANYA bagian yang Bima minta. Pertahankan sisa teks (pilihan kata, urutan kalimat, struktur, panjang, dan gaya) PERSIS sama kayak Draf Asal.
- Contoh: kalau Bima cuma minta "ganti emoji jadi 🗿", "hapus emoji", "ganti kata X jadi Y", atau "tambahin titik", maka CUMA bagian itu yang berubah. Teks selebihnya jangan diutak-atik, jangan diparafrase, jangan dirombak strukturnya.
- Cuma boleh nulis ulang seluruh postingan KALAU Bima emang minta eksplisit (contoh: "tulis ulang", "bikin versi baru", "ganti total", "rombak semua").
- Kalau Draf Asal udah oke, JANGAN "memperbaiki" hal yang gak diminta cuma demi aturan gaya di bawah. Aturan gaya cuma berlaku buat bagian yang lu ubah atau saat nulis dari nol.

ATURAN:
  - Hasil akhir HARUS pendek: 1-3 kalimat aja. Kayak ngobrol sama temen.
  - Pake "gua" dan "lu", JANGAN "gue" atau "loe".
  - Emoji MINIMAL, maks 1-2 (🗿😭💀🫠🤡😏🔥🙏). JANGAN pake ✨🚀💡😊🤖.
  - TANPA HASHTAG.
  - JANGAN bahas skincare dalam bentuk apapun. ABSOLUTE BAN.
  - ANTI-SLOP (WAJIB):
    * Hindari pembuka basa-basi/throat-clearing (misal: "Tentu,", "Tentu saja,", "Perlu dicatat,", "Menariknya,"). Langsung nyatakan poinnya.
    * Hindari kata klise AI: "di era digital", "solusi terbaik", "berkomitmen untuk", "tidak hanya itu", "secara keseluruhan", "menawarkan kemudahan".
    * Gunakan kalimat aktif dan kasual. Hindari drama biner klise ("Bukan karena X, melainkan Y").
  - Tetep di TOPIK yang relevan dengan draf asal atau feedback terbaru.
  - HARUS di bawah 500 karakter. Idealnya di bawah 200 karakter.
  
Kembalikan draf final yang dibungkus dalam tag <draft>...</draft>.
Contoh output:
<draft>konten postingan di sini</draft>

Jangan memberikan penjelasan, tanda kutip bungkus di luar tag, atau kata pengantar apa pun di luar tag <draft>."""

    for attempt in range(3):
        try:
            from langchain_core.messages import SystemMessage
            resp = await asyncio.to_thread(
                threads_llm.invoke,
                [SystemMessage(content=system_prompt)]
            )
            content = resp.content.strip()
            # Ekstrak konten di dalam tag <draft>...</draft>
            draft_match = re.search(r'<draft>([\s\S]*?)</draft>', content, re.IGNORECASE)
            if draft_match:
                revised_text = clean_bima_text(draft_match.group(1))
            else:
                revised_text = clean_bima_text(content)

            if len(revised_text) <= 500:
                if len(revised_text) > 480:
                    revised_text = await shorten_draft_cleanly(revised_text)
                _draft_contexts[revised_text.strip()] = search_context_info
                return revised_text
            logger.warning(f"[THREADS_REVISION] Hasil revisi terlalu panjang ({len(revised_text)} karakter) pada percobaan {attempt + 1}. Retrying...")
            system_prompt += f"\nSTRICT CONSTRAINT: Your previous revision output was too long ({len(revised_text)} characters). Rewrite it to be under 500 characters!"
        except Exception as e:
            logger.error(f"[THREADS_REVISION] Gagal memproses smart revision (percobaan {attempt + 1}): {e}")
            
    # Fallback ke input as-is, truncated cleanly
    fallback_text = await shorten_draft_cleanly(clean_bima_text(user_reply))
    _draft_contexts[fallback_text.strip()] = search_context_info
    return fallback_text

async def handle_threads_command(message, args: str, bot_client) -> None:
    """Handler utama command Discord !threads."""
    load_dotenv(override=True)
    
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    user_id = str(message.author.id)
    
    if not token:
        await message.reply("❌ Error: `THREADS_ACCESS_TOKEN` tidak ditemukan di `.env`. Silakan setup token Anda terlebih dahulu.")
        return

    # KASUS 1: Panggil tanpa argumen (tampilkan tren terhangat)
    if not args:
        progress = await message.reply("🔍 *Sedang memindai berita viral dan tren terkini...*")
        trends = await fetch_indonesian_trends()
        
        if not trends:
            await progress.edit(content="🤷 Gagal mengambil tren. Coba ketik topiknya langsung, contoh: `!threads nilai dolar naik`")
            return
            
        _cached_trends[user_id] = trends
        
        reply_lines = ["🔥 **Berita Viral & Tren Terkini Hari Ini:**"]
        for idx, t in enumerate(trends, 1):
            reply_lines.append(f"{idx}. **{t['title']}**\n   _{t['snippet'][:120]}..._")
            
        reply_lines.append("\n👉 Ketik **`!threads [nomor]`** untuk memilih topik di atas.")
        reply_lines.append("👉 Atau ketik **`!threads [topik bebas]`** untuk menulis ide lu sendiri.")
        
        await progress.edit(content="\n".join(reply_lines))
        return

    # Cek flags gambar dan no-strip
    include_image = False
    clean_args = args.strip()
    
    if "--image" in clean_args.lower() or "pakai gambar" in clean_args.lower() or "dengan gambar" in clean_args.lower() or "-img" in clean_args.lower():
        include_image = True
        clean_args = re.sub(r'--image|pakai gambar|dengan gambar|-img', '', clean_args, flags=re.IGNORECASE).strip()
        
    no_strip = False
    if clean_args and ("jangan pake strip" in clean_args.lower() or "tanpa strip" in clean_args.lower() or "no strip" in clean_args.lower() or "tanpa tanda minus" in clean_args.lower()):
        no_strip = True
        clean_args = re.sub(r'jangan pake strip|tanpa strip|no strip|tanpa tanda minus', '', clean_args, flags=re.IGNORECASE).strip()

    # KASUS 2: Bima memilih nomor tren dari cache
    selected_topic = ""
    selected_context = ""
    
    if clean_args.isdigit():
        idx = int(clean_args) - 1
        user_cache = _cached_trends.get(user_id)
        if user_cache and 0 <= idx < len(user_cache):
            selected_topic = user_cache[idx]["title"]
            selected_context = user_cache[idx]["snippet"]
        else:
            await message.reply("❌ Nomor pilihan tidak valid atau cache tren Anda sudah kedaluwarsa. Silakan ketik `!threads` ulang.")
            return
    else:
        # KASUS 3: Bima memberikan topik kustom
        selected_topic = clean_args
        progress = await message.reply(f"🔍 *Mencari konteks berita untuk topik: '{selected_topic}'...*")
        selected_context = await search_context(selected_topic)
        await progress.delete()

    # Generate draf postingan Threads
    progress = await message.reply("✍️ *Sedang menulis draf postingan ala kepribadian lu...*")
    
    viral_context = ""
    try:
        from core import agentmemory_client
        memories = await agentmemory_client.recall("[VIRAL_PATTERN]", limit=3)
        if memories:
            viral_context = f"\n=== POLA VIRAL YANG SUDAH PIPELAJARI (Terapkan teknik/strukturnya) ===\n{memories}\n=======================================================\n"
    except Exception as e:
        logger.warning(f"[THREADS_GEN] Gagal mengambil memori pola viral: {e}")

    no_strip_prompt = ""
    if no_strip:
        no_strip_prompt = "\nConstraint: JANGAN menggunakan tanda strip (-), en-dash (–), atau em-dash (—) sama sekali dalam postingan! Gunakan tanda koma atau spasi sebagai pemisah jika diperlukan."

    user_prompt = f"""Topik: {selected_topic}
Fakta/Konteks Tambahan:
{selected_context}
{viral_context}
Tulis draf postingan Threads yang sangat emosional, sarkas, menggunakan singkatan gaul, memakai kata "lu" dan "gua" (tanpa kata "loe" atau "gue").{no_strip_prompt}
Jika ada pola viral di atas, terapkan teknik hook, spasi, format, atau emosi yang sesuai agar postingan berpotensi viral!"""

    try:
        draft_text = await generate_bima_draft(user_prompt, no_strip=no_strip)
        _draft_contexts[draft_text.strip()] = selected_context
        await progress.delete()
    except Exception as e:
        logger.error(f"[THREADS_GEN] Gagal memanggil LLM: {e}")
        await progress.edit(content=f"❌ Gagal membuat draf: `{e}`")
        return

    # Generate image jika diminta
    image_url = None
    if include_image:
        progress_img = await message.reply("🖼️ *Sedang menggambar visual untuk postingan ini...*")
        from core.threads_scheduler import generate_image_prompt_for_post
        from core.image_host import host_image_publicly
        image_prompt = await generate_image_prompt_for_post(draft_text)
        if image_prompt:
            try:
                from tools.image_gen import generate_image
                res = await asyncio.to_thread(generate_image, image_prompt)
                if res.startswith("SUCCESS|"):
                    local_img_path = Path(res.split("|")[1])
                    # Host ke URL publik (Catbox -> Discord CDN), gak pakai tunnel lagi.
                    image_url = await host_image_publicly(local_img_path, client=bot_client, fallback_user_id=user_id)
                    if image_url:
                        logger.info(f"[THREADS] Gambar di-host di: {image_url}")
                    else:
                        logger.warning("[THREADS] Hosting gambar gagal, lanjut posting teks aja.")
                else:
                    logger.warning(f"[THREADS] ImageGenTool failed: {res}")
            except Exception as img_err:
                logger.error(f"[THREADS] Error image gen: {img_err}")
        await progress_img.delete()

    if include_image and not image_url:
        await message.reply("⚠️ Hosting gambar lagi gagal, jadi postingan ini gua kirim **tanpa gambar** ya biar gak gagal.")

    # Tampilkan draf di channel & minta persetujuan lewat permission gate (Discord DM)
    reply_msg = f"📝 **Draf Postingan Threads Terbentuk:**\n```text\n{draft_text}\n```\n"
    if image_url:
        reply_msg += f"🖼️ **Gambar Terlampir**: {image_url}\n"
    reply_msg += "📥 **Persetujuan dikirim ke DM Anda.** Silakan klik reaksinya atau balas langsung untuk merevisi!"
    await message.reply(reply_msg)
    
    # Gunakan permission gate
    logger.info(f"[THREADS] Meminta persetujuan Bima untuk postingan: '{draft_text[:50]}...'")
    details_text = draft_text
    if image_url:
        details_text += f"\n\n🖼️ **Gambar Terlampir**: {image_url}"

    approved = await request_permission(
        discord_user_id=user_id,
        action_type="THREADS_POST",
        details=details_text
    )
    
    if not approved:
        await message.reply("❌ **Tindakan Ditolak:** Postingan Threads dibatalkan oleh Bima.")
        return

    # Ambil teks revisi jika ada
    from core.permission_gate import get_revised_text
    revised = get_revised_text(user_id)
    final_text = revised if revised else draft_text
        
    # Jika disetujui, publikasikan ke Threads API secara riil
    progress = await message.reply("🚀 *Persetujuan diterima! Mempublikasikan postingan ke Threads...*")
    try:
        post_id = await publish_post_to_threads(final_text, token, image_url=image_url)
        post_url = _threads_post_url(post_id)
        await progress.edit(content=f"✅ **Postingan Berhasil Dipublikasikan!**\n🔗 **Link Threads:** {post_url}")
        logger.info(f"[THREADS] Post sukses dipublikasikan ke Threads. ID: {post_id}")
    except Exception as e:
        logger.error(f"[THREADS_PUB] Gagal memposting ke Threads: {e}")
        await progress.edit(content=f"❌ Gagal memposting ke Threads: `{e}`")


async def draft_and_post_flow(topic: str, user_id: str) -> str:
    """Async helper to execute the search, draft generation, permission gate, and publication."""
    load_dotenv(override=True)
    
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    if not token:
        return "❌ Error: `THREADS_ACCESS_TOKEN` tidak ditemukan di `.env`. Silakan setup token Anda terlebih dahulu."

    # Deteksi no_strip dari topik mentah dulu (sebelum frasa gambar dibuang).
    no_strip = bool(topic) and any(
        k in topic.lower()
        for k in ("jangan pake strip", "tanpa strip", "no strip", "tanpa tanda minus")
    )

    # Buang frasa "bikin gambar" dari topik biar LLM teks gak ke-trigger nulis
    # penolakan "gua gak bisa bikin gambar" di dalam draf.
    topic = _strip_image_request(topic)

    # 1. Cari konteks fakta untuk topik
    context = await search_context(topic)

    # 1.5 Cari pola viral yang sudah dipelajari
    viral_context = ""
    try:
        from core import agentmemory_client
        memories = await agentmemory_client.recall("[VIRAL_PATTERN]", limit=3)
        if memories:
            viral_context = f"\n=== POLA VIRAL YANG SUDAH DIPELAJARI (Terapkan teknik/strukturnya) ===\n{memories}\n=======================================================\n"
    except Exception as e:
        logger.warning(f"[THREADS_GEN] Gagal mengambil memori pola viral: {e}")

    # 2. Buat draf postingan
    no_strip_prompt = ""
    if no_strip:
        no_strip_prompt = "\nConstraint: JANGAN menggunakan tanda strip (-), en-dash (–), atau em-dash (—) sama sekali dalam postingan! Gunakan tanda koma atau spasi sebagai pemisah jika diperlukan."

    user_prompt = f"""Topik: {topic}
Fakta/Konteks Tambahan:
{context}
{viral_context}
Tulis draf postingan Threads yang sangat emosional, sarkas, menggunakan singkatan gaul, memakai kata "lu" dan "gua" (tanpa kata "loe" atau "gue").{no_strip_prompt}
Jika ada pola viral di atas, terapkan teknik hook, spasi, format, atau emosi yang sesuai agar postingan berpotensi viral!"""

    try:
        draft_text = await generate_bima_draft(user_prompt, no_strip=no_strip)
        _draft_contexts[draft_text.strip()] = context
    except Exception as e:
        logger.error(f"[THREADS_GEN] Gagal memanggil LLM: {e}")
        return f"❌ Gagal membuat draf: `{e}`"

    # 3. Minta persetujuan lewat permission gate (Discord DM)
    logger.info(f"[THREADS_TOOL] Meminta persetujuan Bima untuk postingan: '{draft_text[:50]}...'")
    approved = await request_permission(
        discord_user_id=user_id,
        action_type="THREADS_POST",
        details=draft_text
    )
    
    if not approved:
        return "❌ **Tindakan Ditolak:** Postingan Threads dibatalkan oleh Bima."

    # Ambil teks revisi jika ada
    from core.permission_gate import get_revised_text
    revised = get_revised_text(user_id)
    final_text = revised if revised else draft_text
        
    # 4. Publikasikan ke Threads API secara riil
    try:
        post_id = await publish_post_to_threads(final_text, token)
        post_url = _threads_post_url(post_id)
        logger.info(f"[THREADS] Post sukses dipublikasikan ke Threads. ID: {post_id}")
        return f"SUCCESS|{post_url}|Postingan berhasil dipublikasikan ke Threads!"
    except Exception as e:
        logger.error(f"[THREADS_PUB] Gagal memposting ke Threads: {e}")
        return f"❌ Gagal memposting ke Threads: `{e}`"


import json
from pathlib import Path

REPLIED_COMMENTS_FILE = Path(__file__).parent.parent / "outputs" / "threads_replied_comments.json"

def _load_replied_comments() -> set[str]:
    if REPLIED_COMMENTS_FILE.exists():
        try:
            return set(json.loads(REPLIED_COMMENTS_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()

def _save_replied_comment(comment_id: str):
    REPLIED_COMMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    current = _load_replied_comments()
    current.add(comment_id)
    REPLIED_COMMENTS_FILE.write_text(json.dumps(list(current), indent=2), encoding="utf-8")

async def fetch_user_posts(token: str) -> list[dict]:
    url = "https://graph.threads.net/v1.0/me/threads"
    params = {"fields": "id,text,username,timestamp", "access_token": token}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("data", [])

async def fetch_post_replies(post_id: str, token: str) -> list[dict]:
    url = f"https://graph.threads.net/v1.0/{post_id}/replies"
    params = {"fields": "id,text,username,timestamp", "access_token": token}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("data", [])

async def reply_to_comment_flow(reply_id: str, reply_text: str, reply_username: str, post_text: str, user_id: str, client=None) -> str:
    """Alur balas komentar interaktif dengan persetujuan Bima."""
    load_dotenv(override=True)
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    if not token:
        return "❌ Error: `THREADS_ACCESS_TOKEN` tidak ditemukan."

    # 0. Filter anti-spam & troll lokal
    spam_keywords = ["jual", "promo", "ready stock", "murah", "klik link", "slot online", "slot gacor", "dp minimal", "link wa", "sewa", "price list", "pricelist", "order", "follow back", "follback", "cek profil"]
    negative_keywords = ["goblok", "tolol", "anjing", "babi", "bangsat", "kontol", "memek", "asu", "bajingan"]
    
    reply_lower = reply_text.lower()
    is_spam = any(k in reply_lower for k in spam_keywords)
    is_toxic = any(k in reply_lower for k in negative_keywords)
    
    if is_spam or is_toxic:
        logger.info(f"[THREADS_REPLY] Komentar dari @{reply_username} dilewati karena terindikasi SPAM/TOXIC.")
        _save_replied_comment(reply_id)
        return "❌ Komentar dilewati karena spam/toxic."

    # 0.5 Evaluasi apakah bisa dibalas otomatis secara aman
    is_auto, auto_reply_text = await evaluate_auto_reply(reply_text, post_text)
    if is_auto and auto_reply_text:
        logger.info(f"[THREADS_REPLY] Komentar dari @{reply_username} aman. Membalas otomatis: '{auto_reply_text}'")
        try:
            post_id = await publish_post_to_threads(auto_reply_text, token, reply_to_id=reply_id)
            post_url = _threads_post_url(post_id)
            _save_replied_comment(reply_id)
            
            # Kirim notifikasi log ke DM owner
            if client and user_id:
                try:
                    user = await client.fetch_user(int(user_id))
                    if user:
                        await user.send(
                            f"💬 **Balasan Otomatis Terkirim** 💬\n\n"
                            f"• **Dari**: @{reply_username}\n"
                            f"• **Komentar**: \"{reply_text}\"\n"
                            f"• **Balasan Anisa**: \"{auto_reply_text}\"\n"
                            f"🔗 **Link**: {post_url}"
                        )
                except Exception as notify_err:
                    logger.warning(f"Gagal mengirim notif auto-reply ke Discord: {notify_err}")
                    
            return f"SUCCESS|{post_url}|Balasan otomatis dikirim!"
        except Exception as e:
            logger.error(f"[THREADS_REPLY] Gagal posting balasan otomatis: {e}. Lanjut ke approval manual.")

    # Cari pola viral yang dipelajari
    viral_context = ""
    try:
        from core import agentmemory_client
        memories = await agentmemory_client.recall("[VIRAL_PATTERN]", limit=2)
        if memories:
            viral_context = f"\n=== POLA VIRAL YANG SUDAH DIPELAJARI ===\n{memories}\n=======================================\n"
    except Exception:
        pass

    # Buat draf balasan
    user_prompt = f"""Komentar dari @{reply_username} pada postingan kita:
Postingan Kita: "{post_text}"
Komentar Dia: "{reply_text}"
{viral_context}
Tulis draf balasan Threads yang sangat emosional, sarkas, menggunakan singkatan gaul, memakai kata "lu" dan "gua" (tanpa kata "loe" atau "gue"). Balas secara nyambung dan cerdas."""

    try:
        draft_text = await generate_bima_draft(user_prompt)
        _draft_contexts[draft_text.strip()] = f"Postingan Kita: {post_text}\nKomentar Dia: {reply_text}"
    except Exception as e:
        return f"❌ Gagal membuat draf balasan: {e}"

    details = (
        f"💬 **KOMENTAR BARU DI THREADS** 💬\n\n"
        f"• **Dari**: @{reply_username}\n"
        f"• **Komentar**: \"{reply_text}\"\n"
        f"• **Pada Postingan**: \"{post_text[:120]}...\"\n\n"
        f"📝 **Draf Balasan Bima**:\n"
        f"```{draft_text}```\n\n"
        f"👍 **Setuju**  |  👎 **Tolak**  |  Atau balas langsung untuk revisi balasan!"
    )

    # Minta persetujuan
    approved = await request_permission(
        discord_user_id=user_id,
        action_type="THREADS_REPLY",
        details=details
    )

    if not approved:
        # Tandai sudah ditangani (ditolak/timeout) supaya scanner gak nge-prompt
        # komentar yang sama berulang tiap 5 menit selamanya.
        _save_replied_comment(reply_id)
        return "❌ Balasan Threads dibatalkan oleh Bima."

    from core.permission_gate import get_revised_text
    revised = get_revised_text(user_id)
    final_text = revised if revised else draft_text

    try:
        post_id = await publish_post_to_threads(final_text, token, reply_to_id=reply_id)
        post_url = _threads_post_url(post_id)
        _save_replied_comment(reply_id)
        return f"SUCCESS|{post_url}|Balasan berhasil dipublikasikan!"
    except Exception as e:
        return f"❌ Gagal memposting balasan: {e}"


class ViralAnalysisTool:
    name: str = "Viral Analysis and Learning Tool"
    description: str = """Menganalisis mengapa suatu postingan atau tren viral, mengekstrak pola (hook, struktur, emosi, slang), dan menyimpannya ke memori & Obsidian Bima agar dipelajari Anisa.
    Input: teks postingan viral atau deskripsi tren."""

    def _run(self, content: str) -> str:
        system_analysis = """You are Anisa, B.I.M.A Core's chief analyzer. Analyze the provided viral post or trend to extract why it went viral.
Focus on:
1. Hook Analysis: How does the first sentence capture attention?
2. Structure & Formatting: Length of sentences, spacing, and presentation.
3. Tone & Emotional Triggers: Sarcasm, cynicism, frustration, excitement, etc.
4. Key Slang/Phrases: Gen-Z or local Indonesian slang.

Provide a structured, compact summary of these patterns. Your output will be saved as a learning pattern."""

        from core.llm_config import default_llm
        from langchain_core.messages import SystemMessage, HumanMessage
        from datetime import datetime
        try:
            resp = default_llm.invoke([
                SystemMessage(content=system_analysis),
                HumanMessage(content=f"Konten viral untuk dianalisis:\n{content}")
            ])
            analysis = resp.content.strip()
        except Exception as e:
            return f"❌ Gagal menganalisis: {e}"

        # Simpan ke Obsidian (opsional — aktif kalau OBSIDIAN_PATH diset di .env)
        try:
            obsidian_path = os.environ.get("OBSIDIAN_PATH")
            if not obsidian_path:
                raise RuntimeError("OBSIDIAN_PATH tidak diset; lewati simpan Obsidian")
            obsidian_dir = Path(obsidian_path) / "Viral_Learning"
            obsidian_dir.mkdir(parents=True, exist_ok=True)
            title = f"Analisa_Viral_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            filepath = obsidian_dir / f"{title}.md"
            md = f"""# Analisis Pola Viral: {title}

**Tanggal:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} WIB

## Hasil Analisis Pola Viral
{analysis}

---
*Disimpan otomatis oleh B.I.M.A Core.*"""
            filepath.write_text(md, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Gagal simpan Obsidian: {e}")

        # Simpan ke agentmemory
        try:
            from core import agentmemory_client
            from core.permission_gate import get_main_loop
            import asyncio
            loop = get_main_loop()
            memory_text = f"[VIRAL_PATTERN] {analysis}"
            if loop:
                asyncio.run_coroutine_threadsafe(
                    agentmemory_client.save(f"Belajar pola viral dari: {content[:150]}", memory_text),
                    loop
                )
            else:
                asyncio.run(agentmemory_client.save(f"Belajar pola viral dari: {content[:150]}", memory_text))
        except Exception as e:
            logger.warning(f"Gagal simpan agentmemory: {e}")

        return f"SUCCESS|Gua udah pelajarin pola viralnya, Bim!\n\n### 📝 Ringkasan Pola Viral:\n{analysis}"
