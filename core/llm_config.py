"""LLM config ramping buat Ai-sosmed.

Pakai OpenRouter lewat langchain `ChatOpenAI`. Dipisah dari BIMA_CORE biar bot ini
mandiri — cuma expose yang dibutuhin modul Threads: `get_langchain_llm()` + `default_llm`.
"""
import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

logger = logging.getLogger("ai_sosmed.llm")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def get_langchain_llm(
    model_name: str = "deepseek/deepseek-v4-flash",
    max_tokens: int | None = None,
) -> ChatOpenAI:
    """Bikin `ChatOpenAI` yang nge-route ke OpenRouter.

    Nama model dikirim apa adanya ke OpenRouter — JANGAN pakai prefix `openrouter/`
    (itu khusus CrewAI/LiteLLM). Kalau kebawa prefix-nya, dibuang otomatis.
    """
    if model_name.startswith("openrouter/"):
        model_name = model_name.split("/", 1)[1]

    kwargs: dict = {
        "model": model_name,
        "openai_api_key": OPENROUTER_API_KEY,
        "openai_api_base": OPENROUTER_BASE_URL,
        "max_retries": 2,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return ChatOpenAI(**kwargs)


# LLM default (routing/standar) buat node-node ringan.
default_llm = get_langchain_llm()
