from io import BytesIO

from pathlib import Path
import re
from io import BytesIO

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
    candidates = _meaningful_lines(text)
    for cleaned in candidates:
        lowered = cleaned.casefold()
        if _looks_like_heading(cleaned):
            return cleaned[:240]
        if any(
            token in lowered
            for token in (
                "subject:",
                "re:",
                "scheme of",
                "report on",
                "certificate",
                "declaration",
                "undertaking",
                "observation letter",
                "no-objection letter",
            )
        ):
            return _strip_heading_prefix(cleaned)[:240]
    for cleaned in candidates:
        if len(cleaned) >= 12:
            return cleaned[:240]
    return filename.rsplit(".", 1)[0]


def _summarize(text: str) -> str:
    sentences = _meaningful_sentences(text)
    if not sentences:
        return NO_EXTRACTABLE_TEXT

    max_words = min(get_settings().summary_max_words, 55)
    chosen: list[str] = []
    total_words = 0
    for sentence in sentences:
        words = sentence.split()
        if not words:
            continue
        if chosen and total_words + len(words) > max_words:
            break
        chosen.append(sentence)
        total_words += len(words)
        if len(chosen) >= 2:
            break

    summary = " ".join(chosen).strip()
    if summary:
        return summary[:420]

    words = " ".join(text.split()).split()
    shortened = " ".join(words[:max_words]).rstrip(" .,;:")
    return shortened or NO_EXTRACTABLE_TEXT


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


def _meaningful_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        cleaned = " ".join(raw_line.split()).strip(" -\t")
        if not cleaned or len(cleaned) < 4:
            continue
        if _is_boilerplate_line(cleaned) or _is_index_like_line(cleaned):
            continue
        lines.append(cleaned)
    return lines


def _meaningful_sentences(text: str) -> list[str]:
    compact = " ".join(text.split())
    raw_sentences = re.split(r"(?<=[.!?])\s+", compact)
    sentences: list[str] = []
    for sentence in raw_sentences:
        cleaned = sentence.strip(" -\t")
        if len(cleaned) < 35:
            continue
        if _is_boilerplate_line(cleaned) or _is_index_like_line(cleaned):
            continue
        if any(token in cleaned.casefold() for token in ("regd. office", "registered office", "corporate relationship dept", "phone :", "website :", "cin:")):
            continue
        sentences.append(cleaned)
    if sentences:
        return sentences

    for line in _meaningful_lines(text):
        if len(line) >= 35:
            sentences.append(line)
    return sentences


def _looks_like_heading(line: str) -> bool:
    lowered = line.casefold()
    if _is_boilerplate_line(line):
        return False
    return any(
        token in lowered
        for token in (
            "subject:",
            "scheme of",
            "report on",
            "certificate",
            "declaration",
            "undertaking",
            "observation letter",
            "no-objection letter",
            "application for",
        )
    )


def _strip_heading_prefix(line: str) -> str:
    return re.sub(r"^(subject|sub|re|ref)\s*[:.-]\s*", "", line, flags=re.IGNORECASE)


def _is_boilerplate_line(line: str) -> bool:
    lowered = line.casefold()
    if lowered.startswith(("annexure ", "page ", "date:", "dear sir", "dear madam", "dear sir/madam")):
        return True
    return any(
        token in lowered
        for token in (
            "regd. office",
            "registered office",
            "website :",
            "website:",
            "phone :",
            "phone:",
            "fax",
            "cin:",
            "email:",
            "corporate relationship dept",
            "listing dept",
            "stock exchange",
            "nse symbol",
            "scrip code",
            "kind attn",
        )
    )


def _is_index_like_line(line: str) -> bool:
    compact = " ".join(line.split())
    lowered = compact.casefold()

    if re.match(r"^\d+(?:\.\d+){1,}\.?\s+", compact):
        return True

    if re.match(r"^\d+\.\s+", compact):
        return any(
            token in lowered
            for token in (
                "annexure",
                "report",
                "opinion",
                "certificate",
                "financials",
                "undertaking",
                "complaint",
                "compliance",
                "board resolution",
                "scheme of amalgamation",
                "fairness opinion",
                "share entitlement ratio",
                "audit committee",
                "id committee",
            )
        )

    return False
