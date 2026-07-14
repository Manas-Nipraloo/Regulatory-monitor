from io import BytesIO

from pathlib import Path
import re

from pypdf import PdfReader

from app.config import get_settings
from app.schemas import PdfExtractionResponse
from app.services.groq_extractor import extract_scanned_pdf_with_groq, extract_text_pdf_with_groq


NO_EXTRACTABLE_TEXT = "No extractable text was found in this PDF."


def extract_pdf_metadata(filename: str, content: bytes) -> PdfExtractionResponse:
    reader = PdfReader(BytesIO(content))
    pages_text = [(page.extract_text() or "").strip() for page in reader.pages]
    text = "\n\n".join(part for part in pages_text if part)

    if _should_use_vision_first(text):
        ai_result = extract_scanned_pdf_with_groq(filename, content)
        if ai_result and _looks_noisy(*ai_result):
            ai_result = None
        if ai_result:
            heading, summary = ai_result
        elif _has_meaningful_text(text):
            heading = _extract_heading(text, filename)
            summary = _summarize(text)
        else:
            heading = filename.rsplit(".", 1)[0]
            summary = NO_EXTRACTABLE_TEXT
    elif _has_meaningful_text(text):
        ai_result = extract_text_pdf_with_groq(filename, text)
        if ai_result and _looks_noisy(*ai_result):
            vision_result = extract_scanned_pdf_with_groq(filename, content)
            if vision_result and not _looks_noisy(*vision_result):
                ai_result = vision_result
            else:
                ai_result = _unreadable_result(filename)
        if ai_result:
            heading, summary = ai_result
        else:
            heading = _extract_heading(text, filename)
            summary = _summarize(text)
    else:
        ai_result = extract_scanned_pdf_with_groq(filename, content)
        if ai_result and _looks_noisy(*ai_result):
            ai_result = _unreadable_result(filename)
        if ai_result:
            heading, summary = ai_result
        else:
            heading = filename.rsplit(".", 1)[0]
            summary = NO_EXTRACTABLE_TEXT

    if _looks_noisy(heading, summary):
        heading, summary = _unreadable_result(filename)

    return PdfExtractionResponse(
        filename=filename,
        heading=heading,
        summary=summary,
        page_count=len(reader.pages),
        extracted_text_chars=len(text),
    )


def _has_meaningful_text(text: str) -> bool:
    words = [word for word in text.split() if any(char.isalpha() for char in word)]
    return len(words) >= 20


def _should_use_vision_first(text: str) -> bool:
    if not _has_meaningful_text(text):
        return True
    compact = " ".join(text.split())
    if "�" in compact or "\\n" in compact:
        return True
    if any(fragment in compact for fragment in ("On B }", "IEIb", "F:Jranb", "ggTgTg")):
        return True
    alpha = sum(1 for char in compact if char.isalpha())
    symbols = sum(1 for char in compact if not char.isalnum() and not char.isspace())
    if alpha and symbols / alpha > 0.45:
        return True
    short_words = [word for word in compact.split()[:120] if len(word) <= 2]
    return len(short_words) > 45


def _extract_heading(text: str, filename: str) -> str:
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if len(cleaned) >= 8:
            return cleaned[:240]
    return filename.rsplit(".", 1)[0]


def _summarize(text: str) -> str:
    max_words = min(get_settings().summary_max_words, 55)
    words = " ".join(text.split()).split()
    if not words:
        return NO_EXTRACTABLE_TEXT
    if len(words) <= max_words:
        return " ".join(words)
    shortened = " ".join(words[:max_words]).rstrip(" .,;:")
    sentence_match = re.search(r"^(.+[.!?])(?:\s|$)", shortened)
    if sentence_match:
        return sentence_match.group(1).strip()
    return shortened


def _looks_noisy(heading: str, summary: str) -> bool:
    text = f"{heading} {summary}"
    heading_symbols = [char for char in heading if not char.isalnum() and not char.isspace()]
    if len(heading.strip()) < 16 and heading_symbols:
        return True
    if "ieib" in heading.casefold():
        return True
    if summary.strip().startswith(("On B }", "]_&", "i)")):
        return True
    if "�" in text or "\\n" in text:
        return True
    meaningful = [char for char in text if char.isalnum() or char.isspace()]
    if not text:
        return True
    return len(meaningful) / len(text) < 0.7


def _unreadable_result(filename: str) -> tuple[str, str]:
    heading = Path(filename).stem or "Scanned annexure"
    summary = (
        "This scanned PDF could not be read clearly enough to generate a reliable AI summary. "
        "Please review the uploaded document in the Drive folder for details."
    )
    return heading, summary
