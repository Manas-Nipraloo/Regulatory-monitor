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

    max_pages_for_summary = 6
    pages_text = [
        (page.extract_text() or "").strip()
        for page in reader.pages[:max_pages_for_summary]
    ]

    text = "\n\n".join(part for part in pages_text if part)

    print("======================================")
    print("PDF EXTRACTION STARTED:", filename)
    print("PDF PAGE COUNT:", len(reader.pages))
    print("EXTRACTED TEXT CHARS:", len(text))
    print("VISION FIRST:", _should_use_vision_first(text))
    print("======================================")

    if _should_use_vision_first(text):
        ai_result = extract_scanned_pdf_with_groq(filename, content)
        print("AI RESULT FROM GROQ VISION:", ai_result)

        if ai_result:
            heading, summary = ai_result
        elif _has_meaningful_text(text):
            heading = _extract_heading(text, filename)
            summary = _summarize(text)
        else:
            heading = filename.rsplit(".", 1)[0]
            summary = ""

    elif _has_meaningful_text(text):
        ai_result = extract_text_pdf_with_groq(filename, text)
        print("AI RESULT FROM GROQ TEXT:", ai_result)

        if ai_result:
            heading, summary = ai_result
        else:
            heading = _extract_heading(text, filename)
            summary = _summarize(text)

    else:
        ai_result = extract_scanned_pdf_with_groq(filename, content)
        print("AI RESULT FROM GROQ FALLBACK VISION:", ai_result)

        if ai_result:
            heading, summary = ai_result
        else:
            heading = filename.rsplit(".", 1)[0]
            summary = ""

    print("FINAL PDF HEADING:", heading)
    print("FINAL PDF SUMMARY:", summary)
    print("======================================")

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
    scored_candidates: list[tuple[int, int, str]] = []

    for index, cleaned in enumerate(candidates):
        normalized = _normalize_heading_candidate(cleaned)

        if not normalized:
            continue

        score = _score_heading_candidate(normalized, index)

        if score > 0:
            scored_candidates.append((score, index, normalized))

    if scored_candidates:
        scored_candidates.sort(key=lambda item: (-item[0], item[1], len(item[2])))
        best_score, _, best_heading = scored_candidates[0]

        if best_score >= 5:
            return best_heading[:240]

    for cleaned in candidates:
        lowered = cleaned.casefold()

        if _looks_like_heading(cleaned):
            return _normalize_heading_candidate(cleaned)[:240]

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
            return _normalize_heading_candidate(cleaned)[:240]

    for cleaned in candidates:
        normalized = _normalize_heading_candidate(cleaned)

        if len(normalized) >= 12:
            return normalized[:240]

    return filename.rsplit(".", 1)[0]


def _summarize(text: str) -> str:
    sentences = _meaningful_sentences(text)

    if not sentences:
        return ""

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

    return shortened or ""


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

        if _score_heading_candidate(cleaned, 0) >= 8:
            continue

        if any(
            token in cleaned.casefold()
            for token in (
                "regd. office",
                "registered office",
                "corporate relationship dept",
                "phone :",
                "website :",
                "cin:",
            )
        ):
            continue

        sentences.append(cleaned)

    if sentences:
        return sentences

    for line in _meaningful_lines(text):
        if len(line) >= 35 and _score_heading_candidate(line, 0) < 8:
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


def _normalize_heading_candidate(line: str) -> str:
    cleaned = _strip_heading_prefix(" ".join(line.split())).strip(" -\t:;,.")
    cleaned = re.sub(r"\s{2,}", " ", cleaned)

    return cleaned


def _score_heading_candidate(line: str, index: int) -> int:
    cleaned = _normalize_heading_candidate(line)
    lowered = cleaned.casefold()
    words = cleaned.split()

    if len(cleaned) < 12 or len(cleaned) > 260:
        return -10

    if len(words) < 3 or len(words) > 34:
        return -8

    if _is_boilerplate_line(cleaned) or _is_index_like_line(cleaned):
        return -10

    score = max(0, 8 - index)

    if _looks_like_heading(cleaned):
        score += 7

    if _looks_like_main_heading(cleaned):
        score += 8

    uppercase_letters = sum(1 for char in cleaned if char.isalpha() and char.isupper())
    alpha_letters = sum(1 for char in cleaned if char.isalpha())
    uppercase_ratio = uppercase_letters / alpha_letters if alpha_letters else 0

    if alpha_letters >= 18 and uppercase_ratio >= 0.72:
        score += 4

    if 4 <= len(words) <= 22:
        score += 2

    if 8 <= len(words) <= 18:
        score += 2

    keyword_hits = sum(
        1
        for token in (
            "scheme",
            "merger",
            "amalgamation",
            "turnover",
            "data",
            "circular",
            "press release",
            "draft scheme",
            "complaint report",
            "certificate",
            "report on",
            "observation letter",
            "no objection",
            "transferor company",
            "transferee company",
            "board of directors",
            "consider and approve",
        )
        if token in lowered
    )

    score += min(keyword_hits, 4) * 2

    if re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",
        lowered,
    ):
        score += 2

    if re.search(r"\b\d{4}\b", cleaned):
        score += 1

    if ":" in cleaned and len(words) >= 4:
        score += 1

    if "(" in cleaned and ")" in cleaned:
        score += 1

    digit_ratio = sum(1 for char in cleaned if char.isdigit()) / max(len(cleaned), 1)

    if digit_ratio > 0.2:
        score -= 3

    if cleaned.endswith(":"):
        score -= 2

    if cleaned.count(",") >= 5:
        score -= 2

    if re.match(r"^(annexure|schedule|table|index)\b", lowered):
        score -= 8

    return score


def _looks_like_main_heading(line: str) -> bool:
    lowered = line.casefold()
    words = line.split()

    if len(words) < 4 or len(words) > 34:
        return False

    if _is_boilerplate_line(line):
        return False

    if re.match(
        r"^(to consider|foreign exchange turnover data|draft scheme|scheme of|complaint report|report on|certificate of|application for|reserve bank of india .* directions|rbi .* directions)\b",
        lowered,
    ):
        return True

    return any(
        token in lowered
        for token in (
            "scheme of merger",
            "scheme of amalgamation",
            "foreign exchange turnover data",
            "complaint report",
            "draft scheme",
            "to consider and approve",
            "observation letter",
            "no objection",
            "amendment directions",
            "master direction",
            "directions, 2026",
            "directions, 2025",
            "circular",
            "notification",
            "income recognition",
            "asset classification",
            "provisioning",
        )
    )


def _strip_heading_prefix(line: str) -> str:
    return re.sub(r"^(subject|sub|re|ref)\s*[:.-]\s*", "", line, flags=re.IGNORECASE)


def _is_boilerplate_line(line: str) -> bool:
    lowered = " ".join(line.casefold().split())
    compact = re.sub(r"[^a-z0-9]", "", lowered)

    if lowered.startswith(("annexure ", "page ", "date:", "dear sir", "dear madam", "dear sir/madam")):
        return True

    # Block separator-only lines like _________ or ---------
    if re.fullmatch(r"[_\-\s|.]{5,}", line):
        return True

    # Block generic letterhead-only headings.
    generic_letterheads = {
        "reservebankofindia",
        "rbi",
        "bharatiyarizvarbank",
        "securitiesandexchangeboardofindia",
        "sebi",
        "governmentofindia",
        "nationalstockexchangeofindia",
        "bombaystockexchange",
        "bse",
        "nse",
        "departmentofregulation",
        "centraloffice",
    }

    if compact in generic_letterheads:
        return True

    return any(
        token in lowered
        for token in (
            "www.rbi.org.in",
            "regd. office",
            "registered office",
            "department of regulation",
            "central office",
            "central office building",
            "shahid bhagat singh marg",
            "website :",
            "website:",
            "phone :",
            "phone:",
            "tel no",
            "fax",
            "cin:",
            "email:",
            "corporate relationship dept",
            "listing dept",
            "stock exchange",
            "nse symbol",
            "scrip code",
            "kind attn",
            "हिंदी आसान",
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