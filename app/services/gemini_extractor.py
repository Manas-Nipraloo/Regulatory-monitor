import base64
import json
import time
from io import BytesIO

import httpx

from app.config import get_settings
from app.services.groq_extractor import (
    MAX_TEXT_CHARS,
    _bad_ai_output,
    _clean_heading,
    _clean_summary,
    _prompt,
    _sample_pdf_text,
)


GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def extract_text_pdf_with_gemini(filename: str, text: str) -> tuple[str, str] | None:
    settings = get_settings()

    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY is missing.")
        return None

    cleaned_text = " ".join(text.split())

    if not cleaned_text:
        print("ERROR: No text found for Gemini text extraction.")
        return None

    sampled_text = _sample_pdf_text(cleaned_text, MAX_TEXT_CHARS)
    prompt_text = f"{_prompt(filename)}\n\nPDF text from first pages:\n{sampled_text}"
    parts = [{"text": prompt_text}]
    return _request_gemini(parts)


def extract_scanned_pdf_with_gemini(filename: str, content: bytes) -> tuple[str, str] | None:
    settings = get_settings()

    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY is missing.")
        return None

    image_parts = _render_pdf_pages(content, max_pages=5)

    if not image_parts:
        print("ERROR: No page images rendered for Gemini.")
        return None

    parts = [{"text": _prompt(filename)}, *image_parts]
    return _request_gemini(parts)


def _request_gemini(parts: list[dict[str, object]]) -> tuple[str, str] | None:
    settings = get_settings()
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": parts,
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            # Gemini 3 models spend reasoning tokens from this same budget, so keep it
            # well above the JSON size and hold thinking low or the reply gets truncated.
            "maxOutputTokens": 2048,
            "thinkingConfig": {"thinkingLevel": "low"},
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["heading", "summary"],
            },
        },
    }
    headers = {
        "x-goog-api-key": settings.gemini_api_key,
        "Content-Type": "application/json",
    }
    url = GEMINI_API_URL.format(model=settings.gemini_model)
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        try:
            print(f"Sending request to Gemini... attempt {attempt}/{max_attempts}")
            print("Gemini model:", settings.gemini_model)

            with httpx.Client(timeout=120) as client:
                response = client.post(url, headers=headers, json=payload)

            if response.status_code == 429 or response.status_code >= 500:
                print("GEMINI RETRYABLE ERROR:", response.status_code, response.text[:3000])
                wait_seconds = 20 * attempt
                print(f"Waiting {wait_seconds} seconds before retry...")
                time.sleep(wait_seconds)
                continue

            if response.status_code >= 400:
                print("GEMINI ERROR STATUS:", response.status_code)
                print("GEMINI ERROR BODY:", response.text[:3000])
                return None

            data = response.json()
            raw = _extract_response_text(data)
            print("GEMINI RAW RESPONSE:", raw[:3000])

            parsed = json.loads(raw)
            heading = str(parsed.get("heading") or "").strip()
            summary = str(parsed.get("summary") or "").strip()

            print("GEMINI HEADING:", heading)
            print("GEMINI SUMMARY:", summary)

            if not heading and not summary:
                print("ERROR: Gemini returned empty heading and summary.")
                return None

            heading = _clean_heading(heading)
            summary = _clean_summary(summary)

            if _bad_ai_output(heading, summary):
                print("GEMINI OUTPUT BLOCKED:", heading, summary)
                return None

            return heading, summary

        except Exception as exc:
            print("GEMINI REQUEST FAILED:", repr(exc))
            wait_seconds = 10 * attempt
            print(f"Waiting {wait_seconds} seconds before retry...")
            time.sleep(wait_seconds)

    return None


def _extract_response_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini returned no candidates.")

    finish_reason = candidates[0].get("finishReason")
    if finish_reason and finish_reason not in ("STOP", "MAX_TOKENS"):
        raise ValueError(f"Gemini stopped early with finishReason={finish_reason}.")

    parts = candidates[0].get("content", {}).get("parts") or []
    text_parts = [str(part.get("text") or "") for part in parts if part.get("text")]

    if not text_parts:
        raise ValueError("Gemini returned no text parts.")

    if finish_reason == "MAX_TOKENS":
        raise ValueError("Gemini hit maxOutputTokens; JSON is truncated. Raise the limit.")

    return "".join(text_parts).strip()


def _render_pdf_pages(content: bytes, max_pages: int) -> list[dict[str, object]]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        print("ERROR: pypdfium2 is not installed. Run: python -m pip install pypdfium2 pillow")
        return []

    image_parts: list[dict[str, object]] = []

    try:
        document = pdfium.PdfDocument(content)
    except Exception as exc:
        print("ERROR: PDF render failed with pypdfium2:", repr(exc))
        return image_parts

    total_pages = min(max_pages, len(document))
    print(f"Rendering {total_pages} page(s) for Gemini vision using pypdfium2.")

    for page_index in range(total_pages):
        try:
            page = document[page_index]
            bitmap = page.render(scale=2)
            pil_image = bitmap.to_pil()

            buffer = BytesIO()
            pil_image.save(buffer, format="PNG")

            image_parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
                    }
                }
            )
            page.close()

        except Exception as exc:
            print(f"ERROR: Failed to render page {page_index + 1}:", repr(exc))

    document.close()
    print(f"Rendered {len(image_parts)} image(s) for Gemini.")
    return image_parts
