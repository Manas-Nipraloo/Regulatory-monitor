import base64
import json
import time
from io import BytesIO
from typing import Any

import httpx
import re

from app.config import get_settings


GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_TEXT_CHARS = 36000

# Substrings of Groq model ids that accept image inputs. Anything else is text-only.
VISION_MODEL_HINTS = ("llama-4", "scout", "maverick", "vision")


def _model_supports_vision(model: str) -> bool:
    lowered = (model or "").lower()
    return any(hint in lowered for hint in VISION_MODEL_HINTS)


def extract_text_pdf_with_groq(filename: str, text: str) -> tuple[str, str] | None:
    settings = get_settings()

    if not settings.groq_api_key:
        print("ERROR: GROQ_API_KEY is missing.")
        return None

    cleaned_text = " ".join(text.split())

    if not cleaned_text:
        print("ERROR: No text found for Groq text extraction.")
        return None

    sampled_text = _sample_pdf_text(cleaned_text, MAX_TEXT_CHARS)

    content_parts: list[dict[str, object]] = [
        {
            "type": "text",
            "text": f"{_prompt(filename)}\n\nPDF text from first pages:\n{sampled_text}",
        }
    ]

    return _request_groq(content_parts)


def extract_scanned_pdf_with_groq(filename: str, content: bytes) -> tuple[str, str] | None:
    settings = get_settings()

    if not settings.groq_api_key:
        print("ERROR: GROQ_API_KEY is missing.")
        return None

    # Text-only Groq models reject image parts, so skip straight to the Gemini fallback.
    if not _model_supports_vision(settings.groq_model):
        print(f"SKIP: Groq model {settings.groq_model} is text-only; leaving vision to Gemini.")
        return None

    # Groq vision model supports maximum 5 images.
    max_pages = 5
    image_urls = _render_pdf_pages(content, max_pages=max_pages)

    if not image_urls:
        print("ERROR: No page images rendered for Groq.")
        return None

    return _request_groq(_vision_content_parts(filename, image_urls))


def _vision_content_parts(filename: str, image_urls: list[str]) -> list[dict[str, object]]:
    content_parts: list[dict[str, object]] = [
        {
            "type": "text",
            "text": _prompt(filename),
        }
    ]

    content_parts.extend(
        {
            "type": "image_url",
            "image_url": {"url": url},
        }
        for url in image_urls
    )

    return content_parts


def _request_groq(content_parts: list[dict[str, Any]]) -> tuple[str, str] | None:
    settings = get_settings()

    payload = {
        "model": settings.groq_model,
        "messages": [
            {
                "role": "user",
                "content": content_parts,
            }
        ],
        "temperature": 0.1,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
    }

    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }

    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        try:
            print(f"Sending request to Groq... attempt {attempt}/{max_attempts}")
            print("Groq model:", settings.groq_model)

            with httpx.Client(timeout=120) as client:
                response = client.post(GROQ_CHAT_URL, headers=headers, json=payload)

            if response.status_code == 429 or response.status_code >= 500:
                print("GROQ RETRYABLE ERROR:", response.status_code, response.text[:3000])

                wait_seconds = 20 * attempt
                print(f"Waiting {wait_seconds} seconds before retry...")
                time.sleep(wait_seconds)
                continue

            if response.status_code >= 400:
                print("GROQ ERROR STATUS:", response.status_code)
                print("GROQ ERROR BODY:", response.text[:3000])
                return None

            raw = response.json()["choices"][0]["message"]["content"]
            print("GROQ RAW RESPONSE:", raw[:3000])

            parsed = json.loads(raw)

            heading = str(parsed.get("heading") or "").strip()
            summary = str(parsed.get("summary") or "").strip()

            print("GROQ HEADING:", heading)
            print("GROQ SUMMARY:", summary)

            if not heading and not summary:
                print("ERROR: Groq returned empty heading and summary.")
                return None

            heading = _clean_heading(heading)
            summary = _clean_summary(summary)

            if _bad_ai_output(heading, summary):
                print("GROQ OUTPUT BLOCKED:", heading, summary)
                return None

            return heading, summary

        except Exception as exc:
            print("GROQ REQUEST FAILED:", repr(exc))

            wait_seconds = 10 * attempt
            print(f"Waiting {wait_seconds} seconds before retry...")
            time.sleep(wait_seconds)

    return None


def _prompt(filename: str) -> str:
    return (
        "You are an expert regulatory document analyst. "
        "You will receive up to the first 5 pages of a regulatory PDF, sometimes as scanned page images. "
        "Your task is to extract the best clean heading and write a useful two-sentence business summary. "
        "Return ONLY valid JSON with exactly these keys: heading and summary. "
        "Do not return markdown, bullets, explanation, notes, or extra keys. "

        "\n\nHEADING RULES:\n"
        "1. The heading must be the real main document title or main regulatory subject from the PDF. "
        "2. Do NOT use annexure labels such as Annexure A, Annexure C, page numbers, file names, portal titles, website labels, address blocks, CIN blocks, repeated headers, or footer text as the heading. "
        "3. If the PDF is a board resolution, make the heading clear using the company name and the approved subject. "
        "4. If the PDF is a scheme document, use the scheme title and parties involved. "
        "5. The heading should be clean, readable, and specific. "
        "6. Prefer a concise title over copying a very long paragraph. "
        "7. Do NOT use letterhead text such as Reserve Bank of India, Government of India, SEBI, BSE, NSE, logos, separator lines, website text, or department names as the heading when a more specific circular, direction, notification, or document title is present. "

        "\n\nSUMMARY RULES:\n"
        "1. The summary must contain only information from the PDF content. "
        "2. The summary must explain what the document is about, the action proposed/approved, the parties involved, and important legal/regulatory context if visible. "
        "3. The summary must be maximum 2 complete sentences. "
        "4. Do not cut any sentence in the middle. "
        "5. Do not write generic fallback text. "
        "6. Do not say the PDF was uploaded, published, scanned, unclear, unreadable, or should be reviewed. "
        "7. Do not mention source website, Drive folder, extraction quality, OCR, or file processing. "
        "8. If the text is scanned, read the page image carefully and infer the heading and summary from the readable content. "
        "9. If the first page is only a cover page, use the next pages to understand the document. "

        "\n\nSTYLE EXAMPLES:\n"

        "Example 1:\n"
        "Input document: Board resolution of Blue Chip India Limited for reduction of capital.\n"
        "Output JSON:\n"
        "{"
        "\"heading\":\"Blue Chip India Limited - Board Resolution for Reduction of Capital of the Company\","
        "\"summary\":\"Blue Chip India Limited approved reduction of its paid-up equity share capital under Section 66 of the Companies Act, 2013 and applicable NCLT rules. The board authorized filings, petitions, applications, and related actions required for implementing the capital reduction.\""
        "}\n"

        "Example 2:\n"
        "Input document: Scheme of reduction of share capital between Blue Chip India Limited and shareholders.\n"
        "Output JSON:\n"
        "{"
        "\"heading\":\"Scheme of Reduction of Share Capital between Blue Chip India Limited and its Shareholders\","
        "\"summary\":\"The scheme provides for reduction of share capital of Blue Chip India Limited under Section 66 of the Companies Act, 2013 and NCLT rules. It sets out the definitions, company details, reduction process, listing treatment, and related terms applicable to shareholders.\""
        "}\n"

        "Example 3:\n"
        "Input document: Board resolution of IVIS International Private Limited approving merger with Magellanic Cloud Limited.\n"
        "Output JSON:\n"
        "{"
        "\"heading\":\"IVIS International Private Limited - Board Resolution to Approve Scheme of Merger by Absorption with Magellanic Cloud Limited\","
        "\"summary\":\"IVIS International Private Limited approved the scheme of merger by absorption with Magellanic Cloud Limited, with IVIS as transferor company and Magellanic Cloud as transferee company. The board authorized necessary NCLT filings, regulatory submissions, shareholder or creditor meeting actions, and representation before authorities.\""
        "}\n"

        "Example 4:\n"
        "Input document: Scheme of merger by absorption of IVIS International Private Limited with Magellanic Cloud Limited.\n"
        "Output JSON:\n"
        "{"
        "\"heading\":\"Scheme of Merger by Absorption of IVIS International Private Limited with Magellanic Cloud Limited\","
        "\"summary\":\"The scheme provides for merger by absorption of IVIS International Private Limited into Magellanic Cloud Limited under Sections 230 to 232 of the Companies Act, 2013. Since IVIS is a wholly owned subsidiary of Magellanic Cloud, the scheme states that shares held by Magellanic in IVIS will stand cancelled without fresh consideration.\""
        "}\n"

        "Example 5:\n"
        "Input document: RBI direction with RBI letterhead and a specific amendment title.\n"
        "Output JSON:\n"
        "{"
        "\"heading\":\"Reserve Bank of India (Commercial Banks – Income Recognition, Asset Classification and Provisioning) Second Amendment Directions, 2026\","
        "\"summary\":\"The RBI amended the Commercial Banks Income Recognition, Asset Classification and Provisioning Directions to add income-recognition rules for acquisition of Specified Non-Financial Assets. The amendment states how unrealised interest, charges, income, and expenses related to such assets must be accounted for and comes into force from October 01, 2026.\""
        "}\n"

        "\n\nIMPORTANT NEGATIVE EXAMPLES:\n"
        "Do NOT write: 'NOC under Regulation 37 Updates published...'\n"
        "Do NOT write: 'The uploaded PDF should be reviewed...'\n"
        "Do NOT write: 'This scanned PDF could not be read clearly enough...'\n"
        "Do NOT write: 'RESERVE BANK OF INDIA' when the PDF has a more specific direction, circular, notification, or amendment title.\n"
        "Do NOT write only the company address, CIN, registered office, or contact details.\n"
        "Do NOT simply repeat Annexure labels.\n"

        f"\n\nFilename: {filename}"
    )


def _clean_heading(value: str) -> str:
    cleaned = " ".join((value or "").split()).strip()

    # Remove long separator lines like _____ or ----- that often come from letterheads.
    cleaned = re.sub(r"[_\-]{3,}", " ", cleaned)

    cleaned = " ".join(cleaned.split()).strip(" -:;,.|_")
    return cleaned[:260]


# Abbreviations whose trailing period must not be read as a sentence end. Regulatory
# orders are full of "Rs. 25 lakh", "ITA No. 414", "Gudari Ltd. vs ITO" — splitting on
# those truncated summaries mid-clause.
_ABBREVIATIONS = (
    "Mr", "Mrs", "Ms", "Dr", "Shri", "Smt", "Sri", "Hon",
    "Ltd", "Pvt", "Co", "Corp", "Inc", "Pte",
    "Rs", "No", "Nos", "DIN", "CIN", "PAN",
    "Sec", "Art", "Reg", "Cl", "Sch", "Ch", "Pt", "para", "Para",
    "Anr", "Ors", "vs", "v", "Sr", "Jt", "Asst", "Addl", "Dy",
    "Govt", "Dept", "Adv", "AR", "DR",
    "etc", "approx",
)

_ABBREVIATION_RE = re.compile(
    r"\b(" + "|".join(re.escape(abbr) for abbr in _ABBREVIATIONS) + r")\.",
)


def _clean_summary(value: str) -> str:
    cleaned = " ".join((value or "").split()).strip()

    if not cleaned:
        return ""

    # Protect abbreviations so sentence splitting does not cut at "Rs.", "Mr.", "No.", etc.
    # Matched on word boundaries: a plain replace would mangle "Rev." into "Re" + "v.".
    protected = _ABBREVIATION_RE.sub(lambda match: f"{match.group(1)}<dot>", cleaned)

    sentences = re.split(r"(?<=[.!?])\s+", protected)
    summary = " ".join(sentences[:2]).strip()

    return summary.replace("<dot>", ".")


def _bad_ai_output(heading: str, summary: str) -> bool:
    combined = f"{heading} {summary}".casefold()

    blocked_phrases = (
        "uploaded pdf",
        "pdf should be reviewed",
        "source scan",
        "could not be read",
        "could not be generated",
        "no extractable text",
        "drive folder",
        "published",
        "ocr",
        "image is unclear",
        "scan was not clear",
    )

    if any(phrase in combined for phrase in blocked_phrases):
        return True

    if not heading.strip():
        return True

    if summary and len(summary.split()) < 8:
        return True

    heading_lower = heading.casefold()

    if heading_lower.startswith(("annexure", "page ", "date:", "regd. office", "registered office")):
        return True

    if "cin:" in heading_lower or "website:" in heading_lower or "phone:" in heading_lower:
        return True

    # Block separator/letterhead headings like "________ RESERVE BANK OF INDIA ________".
    if "_" in heading or re.search(r"[_\-]{5,}", heading):
        return True

    normalized_heading = re.sub(r"[^a-z0-9 ]", " ", heading_lower)
    normalized_heading = " ".join(normalized_heading.split())

    generic_letterheads = (
        "reserve bank of india",
        "rbi",
        "securities and exchange board of india",
        "sebi",
        "government of india",
        "bombay stock exchange",
        "bse",
        "national stock exchange",
        "nse",
        "department of regulation",
        "central office",
    )

    if normalized_heading in generic_letterheads:
        return True

    return False


def _sample_pdf_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text

    window = max_chars // 3
    head = text[:window].rstrip()

    middle_start = max((len(text) // 2) - (window // 2), 0)
    middle = text[middle_start : middle_start + window].strip()

    tail = text[-window:].lstrip()

    return (
        f"{head}\n\n[... middle of document ...]\n\n"
        f"{middle}\n\n[... end of document ...]\n\n{tail}"
    )


def _render_pdf_pages(content: bytes, max_pages: int) -> list[str]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        print("ERROR: pypdfium2 is not installed. Run: python -m pip install pypdfium2 pillow")
        return []

    image_urls: list[str] = []

    try:
        document = pdfium.PdfDocument(content)
    except Exception as exc:
        print("ERROR: PDF render failed with pypdfium2:", repr(exc))
        return image_urls

    total_pages = min(max_pages, len(document))
    print(f"Rendering {total_pages} page(s) for Groq vision using pypdfium2.")

    for page_index in range(total_pages):
        try:
            page = document[page_index]

            bitmap = page.render(scale=2)
            pil_image = bitmap.to_pil()

            buffer = BytesIO()
            pil_image.save(buffer, format="PNG")

            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            image_urls.append(f"data:image/png;base64,{encoded}")

            page.close()

        except Exception as exc:
            print(f"ERROR: Failed to render page {page_index + 1}:", repr(exc))

    document.close()

    print(f"Rendered {len(image_urls)} image(s) for Groq.")
    return image_urls