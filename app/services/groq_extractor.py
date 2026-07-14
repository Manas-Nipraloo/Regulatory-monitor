import base64
import json
from typing import Any

import httpx
import re

from app.config import get_settings


GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_TEXT_CHARS = 18000


def extract_text_pdf_with_groq(filename: str, text: str) -> tuple[str, str] | None:
    settings = get_settings()
    if not settings.groq_api_key:
        return None

    cleaned_text = " ".join(text.split())
    if not cleaned_text:
        return None

    content_parts: list[dict[str, object]] = [
        {
            "type": "text",
            "text": f"{_prompt(filename)}\n\nPDF text:\n{cleaned_text[:MAX_TEXT_CHARS]}",
        }
    ]
    return _request_groq(content_parts)


def extract_scanned_pdf_with_groq(filename: str, content: bytes) -> tuple[str, str] | None:
    settings = get_settings()
    if not settings.groq_api_key:
        return None

    first_page_urls = _render_pdf_pages(content, max_pages=1)
    if first_page_urls:
        result = _request_groq(_vision_content_parts(filename, first_page_urls))
        if result:
            return result

    image_urls = _render_pdf_pages(content, max_pages=max(settings.groq_max_pdf_pages, 6))
    if not image_urls:
        return None

    return _request_groq(_vision_content_parts(filename, image_urls))


def _vision_content_parts(filename: str, image_urls: list[str]) -> list[dict[str, object]]:
    content_parts: list[dict[str, object]] = [{"type": "text", "text": _prompt(filename)}]
    content_parts.extend({"type": "image_url", "image_url": {"url": url}} for url in image_urls)
    return content_parts


def _request_groq(content_parts: list[dict[str, Any]]) -> tuple[str, str] | None:
    settings = get_settings()
    payload = {
        "model": settings.groq_model,
        "messages": [{"role": "user", "content": content_parts}],
        "temperature": 0.1,
        "max_tokens": 700,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=90) as client:
            response = client.post(GROQ_CHAT_URL, headers=headers, json=payload)
            response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
    except Exception:
        return None

    heading = str(parsed.get("heading") or "").strip()
    summary = str(parsed.get("summary") or "").strip()
    if not heading and not summary:
        return None
    return _clean_heading(heading), _clean_summary(summary)


def _prompt(filename: str) -> str:
    return (
        "You are reading a regulatory PDF. Extract the best document heading/title and "
        "write one concise business summary for the whole document. Return only JSON with "
        "keys heading and summary. The heading must be a clean title, not OCR noise. The "
        "summary must be one paragraph only, maximum 2 short sentences, and no bullets "
        "or extra paragraphs. If the PDF page is an image or scan, perform OCR from the "
        "rendered page image first, ignore visual noise, and infer the document title from "
        "the readable content. Focus on the main regulatory action, parties involved, and "
        f"effective date if present. Filename: {filename}"
    )


def _clean_heading(value: str) -> str:
    return " ".join(value.split())[:240]


def _clean_summary(value: str) -> str:
    words = " ".join(value.split()).split()
    if len(words) <= 55:
        return " ".join(words)
    shortened = " ".join(words[:55]).rstrip(" .,;:")
    sentence_match = re.search(r"^(.+[.!?])(?:\s|$)", shortened)
    if sentence_match:
        return sentence_match.group(1).strip()
    return shortened


def _render_pdf_pages(content: bytes, max_pages: int) -> list[str]:
    fitz = _load_fitz()
    if fitz is None:
        return []

    image_urls: list[str] = []
    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception:
        return image_urls

    zoom_matrix = fitz.Matrix(2.0, 2.0)
    for page_index in range(min(max_pages, document.page_count)):
        page = document.load_page(page_index)
        pixmap = page.get_pixmap(matrix=zoom_matrix, alpha=False)
        png_bytes = pixmap.tobytes("png")
        encoded = base64.b64encode(png_bytes).decode("ascii")
        image_urls.append(f"data:image/png;base64,{encoded}")
    document.close()
    return image_urls


def _load_fitz():
    try:
        import fitz
    except ImportError:
        return None
    return fitz
