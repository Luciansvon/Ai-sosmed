import os
import re
import random
import logging
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from core.threads_commands import (
    generate_bima_draft,
    fetch_indonesian_trends,
    search_context,
    publish_post_to_threads,
    ViralAnalysisTool,
    _threads_post_url,
)
from core.permission_gate import request_permission, PermissionTimeoutError

logger = logging.getLogger('bima_core.threads_scheduler')
WIB = ZoneInfo("Asia/Jakarta")

# Komentar yang lagi diproses alur balas-nya (belum tuntas approve/tolak/timeout).
# Mencegah scan berikutnya (tiap 5 menit) nge-spawn flow ganda buat komentar yang sama.
_inflight_comment_ids: set[str] = set()
# Simpan referensi task fire-and-forget biar gak di-GC di tengah jalan
# (asyncio cuma pegang weak reference ke task).
_background_tasks: set = set()


def _track_task(task) -> None:
    """Pegang strong reference ke task sampai selesai, lalu lepas."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

# List of casual topics as fallback or variety
BIMA_CASUAL_TOPICS = [
    "Struggle coding/debugging pas compiler/library error terus nyari solusinya di StackOverflow",
    "Ngulik setup meja kerja (desk setup) minimalis biar betah berjam-jam di depan monitor",
    "Review gadget/hardware PC baru yang harganya selangit tapi performanya juara",
    "Nonton video YouTube esai panjang pas makan siang/malam, udah jadi ritual wajib",
    "Mencoba dengerin playlist lofi beats pas malem-malem biar fokus belajar teknologi baru",
    "Koleksi parfum lokal yang wanginya awet seharian walau kena panas bensin di jalan",
    "Grinding game kasual favorit di Steam/console di akhir pekan buat ngilangin penat",
    "Scrolling TikTok/sosmed berjam-jam padahal niatnya cuma nyari referensi desk setup",
    "Nge-kafe sore-sore nyari kopi susu gula aren yang rasanya konsisten pas lagi suntuk"
]

async def get_bot_owner_id(client) -> str:
    env_id = os.environ.get("BIMA_DISCORD_USER_ID")
    if env_id:
        return env_id
    try:
        app_info = await client.application_info()
        if app_info.team:
            return str(app_info.team.owner_id)
        return str(app_info.owner.id)
    except Exception as e:
        logger.warning(f"[THREADS_SCHEDULER] Gagal get bot owner ID via API: {e}")
        logger.error("[THREADS_SCHEDULER] Owner Discord ID gak ketemu — set BIMA_DISCORD_USER_ID di .env")
        return ""

async def learn_viral_from_trend(title: str, snippet: str):
    """Menganalisis tren secara otomatis dan menyimpannya ke memori."""
    try:
        logger.info(f"[THREADS_SCHEDULER] Auto-learning dari tren: {title}")
        content = f"Title: {title}\nSnippet: {snippet}"
        await asyncio.to_thread(ViralAnalysisTool()._run, content)
    except Exception as e:
        logger.warning(f"[THREADS_SCHEDULER] Gagal auto-learn dari tren: {e}")

async def is_topic_safe_for_autopost(draft_text: str, topic: str) -> bool:
    """Mengecek apakah draf postingan dan topik aman untuk diposting secara otomatis jika Bima tidak merespon."""
    system_prompt = """Lu adalah sistem moderasi B.I.M.A Core. 
Tugas lu adalah menilai apakah draf postingan Threads aman untuk di-publish secara otomatis tanpa persetujuan manual.

Kriteria postingan AMAN (SAFE):
- Membahas topik kasual sehari-hari (Roblox, rendering Blender, musik, film, parfum, sepedaan, kegalauan magang furnitur).
- Tren teknologi/AI yang netral (coding, error, update tech).
- Humor ringan, candaan santai, cerita magang yang lucu.

Kriteria postingan TIDAK AMAN (UNSAFE - butuh persetujuan manual):
- Politik, demo, protes, kebijakan pemerintah, tokoh politik/negara.
- Isu SARA, kontroversi sosial, perdebatan sensitif.
- Masalah keuangan sensitif (dolar melonjak drastis, krisis ekonomi).
- Hal-hal yang berpotensi melanggar pedoman komunitas atau menjelek-jelekkan pihak tertentu secara berlebihan.
- Topik skincare (yang memang dilarang).

Kembalikan HANYA kata "SAFE" jika aman untuk diposting otomatis, atau "UNSAFE" jika tidak aman/butuh manual approval."""
    try:
        from core.llm_config import default_llm
        from langchain_core.messages import SystemMessage, HumanMessage
        resp = await asyncio.to_thread(
            default_llm.invoke,
            [SystemMessage(content=system_prompt), HumanMessage(content=f"Topik: {topic}\nDraf Postingan: {draft_text}")]
        )
        result = resp.content.strip().upper()
        return "SAFE" in result
    except Exception as e:
        logger.warning(f"[THREADS_SCHEDULER] Gagal melakukan safety check LLM: {e}")
        return False  # Fallback ke false untuk aman

DEFAULT_FACTS = [
    {
        "topic": "Kecepatan bersin manusia",
        "context": "Kecepatan udara saat manusia bersin bisa mencapai 70 meter per detik atau sekitar 250 km/jam, setara dengan kecepatan mobil Formula 1. Tubuh juga bisa mengeluarkan hingga 40.000 butiran air kecil sekali bersin."
    },
    {
        "topic": "Organisme terbesar di dunia",
        "context": "Bukan paus biru, organisme terbesar di dunia adalah jamur bawah tanah raksasa Armillaria ostoyae di Oregon, AS. Jamur ini menutupi area seluas 890 hektare dan diperkirakan berumur lebih dari 2.400 tahun."
    },
    {
        "topic": "Akselerasi lompatan kutu",
        "context": "Kutu memiliki akselerasi lompatan yang luar biasa. Seekor kutu bisa melompat sejauh 3,14 inci dalam 1 milidetik, yang secara akselerasi lepas landas dihitung 50 kali lebih cepat daripada pesawat luar angkasa."
    },
    {
        "topic": "Luas permukaan paru-paru manusia",
        "context": "Luas permukaan bagian dalam paru-paru manusia sangat luar biasa. Jika dibentangkan, luas totalnya mencapai sekitar 70 meter persegi, yang setara dengan luas lapangan tenis."
    },
    {
        "topic": "Kemampuan memori visual ikan sumpit",
        "context": "Penelitian Universitas Oxford menemukan bahwa ikan sumpit (archerfish) memiliki kemampuan visual luar biasa untuk mengenali dan membedakan wajah manusia dengan akurasi hingga 80%."
    },
    {
        "topic": "Pemuaian Menara Eiffel",
        "context": "Akibat pemuaian termal pada struktur besinya saat musim panas, tinggi Menara Eiffel di Paris bisa bertambah sekitar 15 cm (5,9 inci) selama suhu udara naik di musim panas."
    },
    {
        "topic": "Ikan malaikat gua yang bisa berjalan",
        "context": "Spesies ikan Cryptotora thamicola (ikan malaikat gua) memiliki struktur panggul unik yang memungkinkan mereka berjalan di darat atau memanjat batu menggunakan siripnya seperti seekor reptil."
    },
    {
        "topic": "Daya energi otak manusia",
        "context": "Otak manusia membutuhkan pasokan energi yang sangat konstan sepanjang waktu. Daya listrik yang dibutuhkan untuk menjaga otak tetap aktif hanya berkisar 10 watt, setara dengan bola lampu kecil."
    }
]

# Maksimal topik yang dilacak di file cooldown anti-ngulang.
RECENT_TOPICS_MAX = 40

# Kata generik yang BUKAN penanda subjek topik. Dipakai saat membandingkan dua
# judul topik: kalau setelah kata-kata ini dibuang masih ada token konten yang
# sama, berarti subjeknya nyaris sama (contoh dua-duanya soal "hiu").
_TOPIC_STOPWORDS = {
    "fakta", "unik", "menarik", "tentang", "kenapa", "mengapa", "asal", "usul",
    "sejarah", "rahasia", "dunia", "manusia", "orang", "paling", "bisa", "yang",
    "dan", "atau", "itu", "ini", "pada", "untuk", "dari", "dengan", "punya",
    "tidak", "gak", "nggak", "adalah", "the", "of", "and", "or", "kecepatan",
    "kemampuan", "jenis", "macam", "cara", "soal", "hal", "ikan", "hewan",
    "binatang", "burung", "benda", "makanan", "minuman",
}

# Sudut pandang acak buat ngedorong LLM keluar dari trivia hewan default.
_FACT_ANGLES = [
    "desain interior, furnitur, atau arsitektur",
    "teknologi, coding, gadget, atau hardware PC",
    "sejarah benda sehari-hari di sekitar kita",
    "makanan, minuman, atau kebiasaan makan orang Indonesia",
    "psikologi dan kebiasaan unik manusia sehari-hari",
    "sains ringan tentang benda di rumah atau kantor",
    "life hack dan tips produktivitas santai",
]


def _topic_tokens(topic: str) -> set[str]:
    """Ambil token konten signifikan dari judul topik (buang kata generik)."""
    words = re.findall(r"[a-zA-Z]+", topic.lower())
    return {w for w in words if len(w) >= 3 and w not in _TOPIC_STOPWORDS}


def _topics_related(a: str, b: str) -> bool:
    """True kalau dua topik berbagi subjek inti (mis. dua-duanya soal 'hiu')."""
    ta, tb = _topic_tokens(a), _topic_tokens(b)
    if not ta or not tb:
        return a.strip().lower() == b.strip().lower()
    return bool(ta & tb)


def _recent_topics_path() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "outputs", "threads_recent_topics.json")


def _load_recent_topics() -> list[str]:
    """Baca daftar topik yang baru-baru ini dipakai (urutan: lama -> baru)."""
    import json
    path = _recent_topics_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(t) for t in data if t]
    except Exception as e:
        logger.warning(f"[THREADS_SCHEDULER] Gagal baca recent topics: {e}")
    return []


def _record_recent_topic(topic: str) -> None:
    """Catat topik yang baru dipakai sebagai cooldown anti-ngulang."""
    import json
    if not topic or not topic.strip():
        return
    path = _recent_topics_path()
    try:
        recent = [t for t in _load_recent_topics()
                  if t.strip().lower() != topic.strip().lower()]
        recent.append(topic)
        recent = recent[-RECENT_TOPICS_MAX:]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(recent, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[THREADS_SCHEDULER] Gagal nyatet recent topic: {e}")


def save_scientific_fact(topic: str, context: str, facts_path: str | None = None):
    """Menyimpan fakta ilmiah baru ke berkas JSON untuk referensi masa depan."""
    try:
        import json
        if facts_path is None:
            facts_path = os.path.join(os.path.dirname(__file__), "scientific_facts.json")
        facts = []
        if os.path.exists(facts_path):
            with open(facts_path, "r", encoding="utf-8") as f:
                try:
                    facts = json.load(f)
                except Exception:
                    facts = []
        else:
            facts = list(DEFAULT_FACTS)

        # Periksa duplikasi subjek (bukan cuma judul persis) agar tidak menumpuk
        # fakta dengan subjek yang sama (mis. beberapa fakta soal "hiu").
        if not any(_topics_related(f.get("topic", ""), topic) for f in facts):
            facts.append({"topic": topic, "context": context})
            with open(facts_path, "w", encoding="utf-8") as f:
                json.dump(facts, f, indent=2, ensure_ascii=False)
            logger.info(f"[THREADS_SCHEDULER] Berhasil menyimpan fakta baru ke database referensi: {topic}")
    except Exception as e:
        logger.warning(f"[THREADS_SCHEDULER] Gagal menyimpan fakta baru ke database: {e}")

async def generate_random_interesting_fact_topic() -> tuple[str, str]:
    """Menghasilkan topik berupa fakta unik/menarik dari database lokal atau LLM (fallback)."""
    import json
    facts_path = os.path.join(os.path.dirname(__file__), "scientific_facts.json")
    
    # Inisialisasi file database jika belum ada
    if not os.path.exists(facts_path):
        try:
            with open(facts_path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_FACTS, f, indent=2, ensure_ascii=False)
            logger.info("[THREADS_SCHEDULER] Inisialisasi database fakta ilmiah dengan data scraping Quora.")
        except Exception as e:
            logger.warning(f"[THREADS_SCHEDULER] Gagal menginisialisasi database fakta: {e}")

    recent_topics = _load_recent_topics()

    # Coba ambil dari database lokal (70% peluang jika file terbaca)
    try:
        if os.path.exists(facts_path) and random.random() < 0.7:
            with open(facts_path, "r", encoding="utf-8") as f:
                facts = json.load(f)
            if facts:
                # Saring fakta yang subjeknya baru aja dipakai biar gak ngulang.
                # Kalau semua kesaring (DB kecil), balik pakai daftar penuh.
                fresh = [
                    fct for fct in facts
                    if not any(_topics_related(fct.get("topic", ""), r) for r in recent_topics)
                ]
                pool = fresh if fresh else facts
                selected = random.choice(pool)
                _record_recent_topic(selected["topic"])
                logger.info(f"[THREADS_SCHEDULER] Menggunakan fakta ilmiah dari database lokal: '{selected['topic']}'")
                return selected["topic"], selected["context"]
    except Exception as e:
        logger.warning(f"[THREADS_SCHEDULER] Gagal mengambil fakta dari database lokal: {e}")

    # Sisanya (30% atau jika gagal), buat baru menggunakan LLM
    logger.info("[THREADS_SCHEDULER] Menghasilkan fakta baru menggunakan LLM...")
    system_prompt = """Lu adalah asisten ide B.I.M.A Core.
Tugas lu adalah memberikan satu ide TOPIK berupa FAKTA MENARIK, FAKTA UNIK, atau LIFE HACK yang aman, seru, dan cocok dibahas oleh anak magang Gen-Z desain furnitur & tech enthusiast.

Kategori topik yang diperbolehkan:
- Desain interior / arsitektur / furnitur (misal: asal-usul kursi plastik bakso, rahasia sambungan kayu kuno Jepang, kenapa warna kayu jati makin tua makin bagus).
- Teknologi / Coding / Gadget (misal: kenapa tombol keyboard QWERTY dibuat acak, fakta unik tentang sejarah bug komputer pertama yang disebabkan oleh serangga asli).
- Kehidupan sehari-hari / Pop culture (misal: kenapa kopi bikin ngantuk hilang tapi kadang bikin mules, kenapa orang Indo kalau makan harus pake kerupuk).
- Life hacks / Tips produktivitas santai.

Aturan Tambahan:
- Berikan penjelasan fakta menarik dengan kalimat yang mengalir biasa di field JSON 'context'. JANGAN gunakan template pembuka 'Baru tahu ternyata...' atau sejenisnya pada field 'context' agar gaya penulisan Anisa tetap bervariasi.

Dilarang keras:
- Politik, SARA, ekonomi makro/dolar naik, gosip/drama sensitif, skincare, atau topik berbahaya lainnya.

Kembalikan respon dalam format JSON seperti ini:
{
  "topic": "Judul topik singkat",
  "context": "Penjelasan singkat tentang fakta menarik tersebut untuk bahan draf"
}
Kembalikan HANYA teks JSON tersebut tanpa markdown, tanpa penjelasan tambahan."""

    try:
        from core.llm_config import default_llm
        from langchain_core.messages import SystemMessage, HumanMessage

        # Kumpulkan topik yang udah pernah dibahas (recent + DB) jadi daftar larangan
        # supaya LLM gak balik lagi ke trivia populer yang itu-itu aja (mis. hiu).
        known_topics: list[str] = list(recent_topics)
        try:
            if os.path.exists(facts_path):
                with open(facts_path, "r", encoding="utf-8") as f:
                    known_topics += [fct.get("topic", "") for fct in json.load(f)]
        except Exception:
            pass
        # Buang duplikat, dahulukan yang terbaru, batasi biar prompt gak kepanjangan.
        seen: set[str] = set()
        avoid: list[str] = []
        for t in reversed(known_topics):
            key = t.strip().lower()
            if t and key not in seen:
                seen.add(key)
                avoid.append(t)
        avoid = avoid[:40]

        nudge = f"Kali ini WAJIB ambil sudut pandang dari kategori: {random.choice(_FACT_ANGLES)}."
        if avoid:
            nudge += (
                "\n\nDAFTAR TOPIK YANG SUDAH PERNAH DIBAHAS. JANGAN ulang subjek/tema yang "
                "sama atau mirip dengan salah satu di bawah ini, cari yang BENER-BENER beda "
                "(termasuk jangan bahas subjek hewan yang udah ada, contoh: kalau 'hiu' udah "
                "ada di daftar, jangan bahas hiu lagi):\n- " + "\n- ".join(avoid)
            )

        resp = await asyncio.to_thread(
            default_llm.invoke,
            [SystemMessage(content=system_prompt), HumanMessage(content=nudge)]
        )
        content = resp.content.strip()
        if content.startswith("```"):
            content = content.replace("```json", "").replace("```", "").strip()
        data = json.loads(content)

        topic = data.get("topic", "")
        context = data.get("context", "")

        # Simpan fakta yang didraf ke database agar bisa digunakan lagi di masa depan
        if topic and context:
            save_scientific_fact(topic, context)
            _record_recent_topic(topic)

        return topic, context
    except Exception as e:
        logger.warning(f"[THREADS_SCHEDULER] Gagal menghasilkan fakta menarik dinamis dari LLM: {e}")
        return "Asal-usul kursi bakso plastik", "Kursi bakso plastik yang ada bolongannya di tengah itu fungsinya biar gak vakum pas ditumpuk dan gampang diambil."

from pathlib import Path

def get_public_tunnel_url(log_path: Path | None = None) -> str | None:
    """Membaca log cloudflared untuk mendeteksi URL tunnel publik terbaru.

    Mengabaikan endpoint non-tunnel seperti `api.trycloudflare.com` (itu endpoint
    API cloudflare, bukan URL tunnel) yang sering ikut ke-capture dari log.
    """
    if log_path is None:
        log_path = Path(__file__).resolve().parent.parent / "logs" / "tunnel-error.log"
    if not log_path.exists():
        return None
    try:
        content = log_path.read_text(encoding="utf-8", errors="ignore")
        urls = re.findall(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', content)
        # Buang noise non-tunnel, ambil URL tunnel asli yang paling baru.
        tunnels = [u for u in urls if not u.startswith("https://api.")]
        if tunnels:
            return tunnels[-1]
    except Exception as e:
        logger.warning(f"[THREADS_SCHEDULER] Gagal mendeteksi URL tunnel dari log: {e}")
    return None

async def generate_image_prompt_for_post(draft_text: str) -> str:
    """Menghasilkan prompt gambar berdasarkan draf postingan untuk digunakan oleh ImageGenTool."""
    system_prompt = """Lu adalah asisten visual B.I.M.A Core.
Tugas lu adalah membuat satu prompt gambar (image prompt) singkat dalam bahasa Inggris berdasarkan teks postingan Threads.
Prompt gambar ini harus dibuat agar terlihat seperti FOTO ASLI (realistic photo) yang diambil oleh orang biasa menggunakan handphone, bukan gambar buatan AI yang mengkilap/sempurna.

Gaya visual yang diinginkan:
- Realistic amateur photography, smartphone camera snapshot vibe (e.g., iPhone or Android picture).
- Pencahayaan alami (natural lighting, sunlight, room light), bayangan alami, tekstur nyata.
- Komposisi kasual, ada sedikit imperfection (tidak terlalu simetris/sempurna).
- JANGAN gunakan kata-kata seperti "3D render", "CGI", "highly detailed", "hyperrealistic", "unreal engine", atau kata kunci AI mengkilap lainnya.
- JANGAN sertakan teks/tulisan apa pun di dalam gambar.

Contoh:
Postingan: "Keyboard QWERTY sengaja dibikin lambat biar mesin ketik tahun 1870 gak macet"
Prompt: "A close-up snapshot of a dusty 1870 mechanical typewriter on an old wooden desk, natural sunlight from a nearby window, amateur smartphone photo style, real life colors and textures"

Kembalikan HANYA teks prompt bahasa Inggris tersebut, tanpa penjelasan tambahan, tanpa tanda kutip."""
    try:
        from core.llm_config import default_llm
        from langchain_core.messages import SystemMessage, HumanMessage
        resp = await asyncio.to_thread(
            default_llm.invoke,
            [SystemMessage(content=system_prompt), HumanMessage(content=f"Postingan: {draft_text}")]
        )
        return resp.content.strip().replace('"', '').replace("'", "")
    except Exception as e:
        logger.warning(f"[THREADS_SCHEDULER] Gagal generate prompt gambar: {e}")
        return ""

async def auto_post_threads(client):
    logger.info("[THREADS_SCHEDULER] Memulai job posting Threads otomatis...")
    load_dotenv(override=True)
    
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    if not token:
        logger.warning("[THREADS_SCHEDULER] Skip auto post: THREADS_ACCESS_TOKEN tidak ditemukan di .env")
        return

    # 1. Tentukan topik (fakta menarik vs personal yang aman)
    use_fact = random.random() < 0.6  # 60% pakai fakta unik, 40% pakai personal aman
    selected_topic = ""
    selected_context = ""

    if use_fact:
        logger.info("[THREADS_SCHEDULER] Menghasilkan topik fakta menarik dinamis...")
        selected_topic, selected_context = await generate_random_interesting_fact_topic()

    if not selected_topic:
        # Fallback atau 40% ke casual safe topics
        selected_topic = random.choice(BIMA_CASUAL_TOPICS)
        selected_context = "Ini adalah topik kehidupan sehari-hari anak muda Gen-Z secara kasual (gadget, game PC, kopi, musik, desk setup)."
        logger.info(f"[THREADS_SCHEDULER] Memilih topik kasual aman: '{selected_topic}'")

    # 2. Ambil pola viral yang sudah dipelajari
    viral_context = ""
    try:
        from core import agentmemory_client
        memories = await agentmemory_client.recall("[VIRAL_PATTERN]", limit=3)
        if memories:
            viral_context = f"\n=== POLA VIRAL YANG SUDAH DIPELAJARI (Terapkan teknik/strukturnya) ===\n{memories}\n=======================================================\n"
    except Exception as e:
        logger.warning(f"[THREADS_SCHEDULER] Gagal mengambil memori pola viral: {e}")

    # 2.5 Deteksi mood dinamis untuk hari ini (Jakarta WIB)
    mood = "santai"
    try:
        now = datetime.now(WIB)
        day_name = now.strftime("%A")
        if day_name == "Monday":
            mood = "capek bgt, mager, sarkas ngantuk, dan kesel hari senin"
        elif day_name in ["Tuesday", "Wednesday", "Thursday"]:
            mood = "fokus, produktif, tech enthusiast, rajin ngulik coding/gadget, dan pengen grinding"
        elif day_name == "Friday":
            mood = "relaxed, excited, seneng udah jumat sore, weekend vibe, dan pengen buru-buru pulang/selesai"
        else: # Saturday, Sunday
            mood = "lazy, chill, game enthusiast, pengen rebahan seharian, santai, dan gak mau diganggu kerjaan"
    except Exception as e:
        logger.warning(f"[THREADS_SCHEDULER] Gagal mendeteksi mood dinamis: {e}")

    # 3. Generate draf postingan
    user_prompt = f"""Topik/Inspirasi: {selected_topic}
Konteks tambahan: {selected_context}
{viral_context}
Tulis draf postingan Threads yang sangat emosional, sarkas, menggunakan singkatan gaul, memakai kata "lu" dan "gua" (tanpa kata "loe" atau "gue").
Aturan penting: Tulis dengan mood/vibe penulisan: {mood}.
Jika ada pola viral di atas, terapkan teknik hook, spasi, format, atau emosi yang sesuai agar postingan berpotensi viral!"""

    try:
        draft_text = await generate_bima_draft(user_prompt)
    except Exception as e:
        logger.error(f"[THREADS_SCHEDULER] Gagal generate draf LLM: {e}")
        return

    # Tentukan apakah postingan ini akan menyertakan gambar (misal: 40% kemungkinan)
    # Khusus untuk fakta unik, visual sangat membantu engagement
    include_image = use_fact and (random.random() < 0.40)
    image_url = None
    image_prompt = ""
    local_img_path = None      # path gambar lokal (cache atau hasil generate)
    image_is_fresh = False     # True kalau baru di-generate (buat disimpan ke galeri)

    if include_image:
        image_prompt = await generate_image_prompt_for_post(draft_text)
        if image_prompt:
            logger.info(f"[THREADS_SCHEDULER] Menggenerasi gambar dengan prompt: '{image_prompt}'")
            try:
                # 1. Cek dulu di galeri cache lokal untuk menghemat API
                from core.image_cache import find_cached_image
                cached_img = find_cached_image(draft_text)
                if cached_img:
                    local_img_path = cached_img
                    logger.info(f"[THREADS_SCHEDULER] Pakai gambar cache dari galeri: {local_img_path.name}")

                # 2. Jika tidak ada di cache, buat baru via API
                if not local_img_path:
                    from tools.image_gen import generate_image
                    res = await asyncio.to_thread(generate_image, image_prompt)
                    if res.startswith("SUCCESS|"):
                        local_img_path = Path(res.split("|")[1])
                        image_is_fresh = True
                        logger.info(f"[THREADS_SCHEDULER] Sukses generate gambar baru: {local_img_path.name}")
                    else:
                        logger.warning(f"[THREADS_SCHEDULER] ImageGenTool gagal: {res}")
            except Exception as img_err:
                logger.warning(f"[THREADS_SCHEDULER] Error saat menggenerasi gambar: {img_err}")

    # 4. Cari owner Discord ID
    owner_id = await get_bot_owner_id(client)
    if not owner_id:
        logger.error("[THREADS_SCHEDULER] Gagal mendapatkan owner ID Discord. Persetujuan tidak dapat dikirim.")
        return

    # 4.5 Host gambar ke URL publik (Catbox -> Discord CDN), gak pakai tunnel lagi.
    # Kalau hosting gagal, image_url tetap None -> posting teks aja (gak gagal total).
    if local_img_path:
        from core.image_host import host_image_publicly
        image_url = await host_image_publicly(local_img_path, client=client, fallback_user_id=owner_id)
        if image_url:
            logger.info(f"[THREADS_SCHEDULER] Gambar di-host di: {image_url}")
        else:
            logger.warning("[THREADS_SCHEDULER] Hosting gambar gagal, lanjut posting teks aja.")

    logger.info(f"[THREADS_SCHEDULER] Mengirim persetujuan posting otomatis ke user: {owner_id}")
    
    # 5. Kirim persetujuan via DM permission gate dengan raise_on_timeout=True
    is_timeout = False
    approved = False
    details_text = f"🚨 [AUTO POST SCHEDULER] 🚨\n\n{draft_text}"
    if image_url:
        details_text += f"\n\n🖼️ **Gambar Terlampir**: {image_url}"

    try:
        approved = await request_permission(
            discord_user_id=owner_id,
            action_type="THREADS_POST",
            details=details_text,
            raise_on_timeout=True
        )
    except PermissionTimeoutError:
        is_timeout = True
        logger.info("[THREADS_SCHEDULER] Bima tidak merespon dalam 5 menit. Mengecek apakah postingan aman untuk auto-publish...")

    if not approved and not is_timeout:
        logger.info("[THREADS_SCHEDULER] Auto post ditolak oleh Bima.")
        return

    if is_timeout:
        safe = await is_topic_safe_for_autopost(draft_text, selected_topic)
        if safe:
            logger.info("[THREADS_SCHEDULER] Postingan tergolong SAFE. Auto-approve diaktifkan!")
            final_text = draft_text
            # Kirim notifikasi bahwa postingan di-approve otomatis karena AFK
            try:
                user = await client.fetch_user(int(owner_id))
                if user:
                    # Tampilkan info gambar di DM jika ada
                    afk_notif = f"⚠️ **Bima AFK (Tidak merespon)**. Postingan berikut tergolong aman (SAFE) dan dipublikasikan otomatis:\n\n```{draft_text}```"
                    if image_url:
                        afk_notif += f"\n🖼️ **Gambar Terlampir**: {image_url}"
                    await user.send(afk_notif)
            except Exception as notify_err:
                logger.warning(f"[THREADS_SCHEDULER] Gagal kirim notif auto-approve: {notify_err}")
        else:
            logger.info("[THREADS_SCHEDULER] Postingan tergolong UNSAFE. Auto post dibatalkan.")
            try:
                user = await client.fetch_user(int(owner_id))
                if user:
                    await user.send(f"❌ **Auto Post Dibatalkan**: Lu ga ngerespon dalam 5 menit, dan topik postingan dinilai sensitif (UNSAFE) untuk di-post otomatis:\n\n```{draft_text}```")
            except Exception:
                pass
            return
    else:
        # Ambil teks revisi jika ada
        from core.permission_gate import get_revised_text
        revised = get_revised_text(owner_id)
        final_text = revised if revised else draft_text

    # 6. Publikasikan
    logger.info("[THREADS_SCHEDULER] Persetujuan diterima! Memposting ke Threads...")
    try:
        post_id = await publish_post_to_threads(final_text, token, image_url=image_url)
        post_url = _threads_post_url(post_id)
        
        # Jika postingan berhasil dipublikasikan dan gambarnya baru di-generate, simpan ke galeri cache
        if image_is_fresh and local_img_path:
            try:
                lp = Path(local_img_path)
                if lp.exists() and lp.is_file():
                    from core.image_cache import add_to_gallery
                    add_to_gallery(lp, image_prompt or "", final_text)
            except Exception as cache_err:
                logger.warning(f"[THREADS_SCHEDULER] Gagal menyimpan gambar ke galeri setelah publish: {cache_err}")
        
        # Kirim notifikasi sukses ke DM owner
        try:
            user = await client.fetch_user(int(owner_id))
            if user:
                await user.send(f"✅ **Auto Post Threads Berhasil!**\n🔗 **Link:** {post_url}")
        except Exception as dm_err:
            logger.warning(f"[THREADS_SCHEDULER] Gagal kirim DM sukses: {dm_err}")
            
        logger.info(f"[THREADS_SCHEDULER] Posting sukses. ID: {post_id}")
    except Exception as e:
        logger.error(f"[THREADS_SCHEDULER] Gagal memposting ke Threads: {e}")
        try:
            user = await client.fetch_user(int(owner_id))
            if user:
                await user.send(f"❌ **Gagal memposting Threads otomatis:** `{e}`")
        except Exception:
            pass

async def scan_for_new_comments(client):
    logger.info("[THREADS_SCHEDULER] Memulai scan komentar baru di Threads...")
    load_dotenv(override=True)
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    if not token:
        return

    owner_id = await get_bot_owner_id(client)
    if not owner_id:
        return

    try:
        from core.threads_commands import fetch_user_posts, fetch_post_replies, _load_replied_comments, reply_to_comment_flow
        posts = await fetch_user_posts(token)
        replied_ids = _load_replied_comments()

        for post in posts[:3]:
            post_id = post.get("id")
            post_text = post.get("text", "")
            if not post_id:
                continue
            
            replies = await fetch_post_replies(post_id, token)
            for reply in replies:
                reply_id = reply.get("id")
                reply_text = reply.get("text", "")
                reply_username = reply.get("username", "")
                
                # Cari balasan dari user lain yang belum kita balas & gak lagi diproses.
                # Cek _inflight_comment_ids penting: alur approval bisa makan waktu sampai
                # 5 menit (== interval scan), jadi tanpa guard ini komentar yang sama
                # bakal di-spawn ulang & bikin prompt/balasan ganda.
                if (
                    reply_id
                    and reply_id not in replied_ids
                    and reply_id not in _inflight_comment_ids
                    and reply_username != os.environ.get("THREADS_USERNAME", "").lstrip("@").strip()
                ):
                    logger.info(f"[THREADS_SCHEDULER] Menemukan komentar baru dari @{reply_username}: {reply_text[:50]}...")

                    # Jalankan alur balas komentar secara async
                    _inflight_comment_ids.add(reply_id)
                    task = asyncio.create_task(
                        reply_to_comment_flow(
                            reply_id=reply_id,
                            reply_text=reply_text,
                            reply_username=reply_username,
                            post_text=post_text,
                            user_id=owner_id,
                            client=client
                        )
                    )
                    _track_task(task)
                    task.add_done_callback(
                        lambda _t, rid=reply_id: _inflight_comment_ids.discard(rid)
                    )
    except Exception as e:
        logger.error(f"[THREADS_SCHEDULER] Error saat scan komentar: {e}")

async def schedule_random_posts_for_today(client, scheduler):
    """Menghitung waktu acak untuk hari ini dan menjadwalkan postingan satu kali (date trigger)."""
    logger.info("[THREADS_SCHEDULER] Merandom jadwal posting untuk hari ini...")
    now = datetime.now(WIB)
    
    # Pagi: Antara 08:30 WIB - 11:30 WIB
    # Siang: Antara 13:00 WIB - 16:30 WIB
    # Malam: Antara 18:30 WIB - 21:30 WIB
    ranges = [
        ("morning", 8, 30, 11, 30),
        ("afternoon", 13, 0, 16, 30),
        ("evening", 18, 30, 21, 30)
    ]
    
    for label, shour, smin, ehour, emin in ranges:
        start_time = now.replace(hour=shour, minute=smin, second=0, microsecond=0)
        end_time = now.replace(hour=ehour, minute=emin, second=0, microsecond=0)
        
        if now > end_time:
            logger.info(f"[THREADS_SCHEDULER] Waktu posting {label} hari ini sudah terlewat, lewati.")
            continue
            
        if start_time < now:
            start_time = now
            
        start_ts = int(start_time.timestamp())
        end_ts = int(end_time.timestamp())
        
        if start_ts >= end_ts:
            continue
            
        random_ts = random.randint(start_ts, end_ts)
        random_run_date = datetime.fromtimestamp(random_ts, tz=WIB)
        
        job_id = f"threads_auto_post_once_{label}_{now.strftime('%Y%m%d')}"
        
        try:
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
        except Exception:
            pass
            
        scheduler.add_job(
            auto_post_threads,
            trigger="date",
            run_date=random_run_date,
            args=[client],
            id=job_id
        )
        logger.info(f"[THREADS_SCHEDULER] Berhasil menjadwalkan posting {label} hari ini pada pukul: {random_run_date.strftime('%H:%M:%S')} WIB")

_scheduler_started = False

def start_threads_scheduler(client):
    global _scheduler_started
    if _scheduler_started:
        logger.info("[THREADS_SCHEDULER] Already started, skipping")
        return None

    if os.environ.get("ENABLE_THREADS_AUTO", "true").lower() != "true":
        logger.info("[THREADS_SCHEDULER] ENABLE_THREADS_AUTO=false, scheduler tidak aktif")
        return None

    scheduler = AsyncIOScheduler(timezone=WIB)
    
    # 1. Jadwalkan job harian setiap pukul 01:00 WIB untuk menghitung waktu acak hari itu
    scheduler.add_job(
        schedule_random_posts_for_today,
        CronTrigger(hour=1, minute=0, timezone=WIB),
        args=[client, scheduler],
        id="threads_daily_randomizer"
    )
    
    # 2. Jalankan langsung saat startup untuk menjadwalkan sisa postingan hari ini
    _track_task(asyncio.create_task(schedule_random_posts_for_today(client, scheduler)))
    
    # 3. Scan komentar baru setiap 5 menit
    scheduler.add_job(
        scan_for_new_comments,
        CronTrigger(minute="*/5", timezone=WIB),
        args=[client],
        id="threads_comment_scan"
    )
    
    scheduler.start()
    _scheduler_started = True
    logger.info("[THREADS_SCHEDULER] ✅ Started — auto post dengan waktu acak harian & scan komentar 5 menit")
    return scheduler
