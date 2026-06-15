"""Entry point Ai-sosmed.

Jalankan:  python main.py
Pastikan `.env` udah diisi (lihat `.env.example`).
"""
import logging
import sys

from dotenv import load_dotenv


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # discord.py terlalu cerewet di INFO — turunin ke WARNING
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    load_dotenv()
    _setup_logging()
    logging.getLogger("ai_sosmed").info("Booting Ai-sosmed...")
    from bot import run_bot
    run_bot()


if __name__ == "__main__":
    main()
