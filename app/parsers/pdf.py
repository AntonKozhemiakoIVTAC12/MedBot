from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from app.db import ReportType

_NAME_PART = r"(?:[А-ЯЁ][а-яё]+|[А-ЯЁ]{2,})(?:-[А-ЯЁ][а-яё]+)?"
_FULL_NAME_PATTERN = re.compile(
    rf"\b({_NAME_PART}\s+{_NAME_PART}\s+{_NAME_PART})\b",
)
_WHITESPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"\b(?P<day>\d{1,2})[./-](?P<month>\d{1,2})[./-](?P<year>\d{2,4})\b",
)
_TEXTUAL_DATE_RE = re.compile(
    r"\b(?P<day>\d{1,2})\s+"
    r"(?P<month>января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
    r"\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)
_DATE_LABEL_PATTERNS = (
    re.compile(
        r"(?:дата\s+(?:исследования|анализа|забора|взятия|выполнения|взятия\s+образца|поступления\s+образца|печати\s+результата)|исследование\s+от|анализ\s+от)"
        r"\s*[:\-]?\s*"
        r"(?P<value>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:дата\s+(?:исследования|анализа|забора|взятия|выполнения|взятия\s+образца|поступления\s+образца|печати\s+результата)|исследование\s+от|анализ\s+от)"
        r"\s*[:\-]?\s*"
        r"(?P<value>\d{1,2}\s+"
        r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
        r"\s+\d{4})",
        re.IGNORECASE,
    ),
)
_PATIENT_PATTERNS = (
    re.compile(
        rf"(?:фио(?:\s+пациента)?|пациент(?:ка)?|patient)\s*[:\-]?\s*(?P<name>{_NAME_PART}(?:\s+{_NAME_PART}){{2}})",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:ф\.?\s*и\.?\s*о\.?)\s*[:\-]?\s*(?P<name>{_NAME_PART}(?:\s+{_NAME_PART}){{2}})",
        re.IGNORECASE,
    ),
)
_PERSON_STOP_WORDS = {
    "анализ",
    "анализа",
    "биохимия",
    "биохимический",
    "билирубин",
    "гемоглобин",
    "глюкоза",
    "гормоны",
    "групп",
    "группа",
    "группы",
    "исследование",
    "исследования",
    "клинический",
    "крови",
    "лейкоциты",
    "липидов",
    "липопротеинов",
    "мочи",
    "общий",
    "ов",
    "пролактин",
    "результат",
    "результаты",
    "соэ",
    "тест",
    "тестостерон",
    "тромбоциты",
    "холестерин",
}
_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_REPORT_TYPE_KEYWORDS: tuple[tuple[ReportType, tuple[str, ...]], ...] = (
    (
        ReportType.HORMONES,
        (
            "гормон",
            "ттг",
            "т3",
            "т4",
            "тиреотроп",
            "тироксин",
            "пролактин",
            "эстрадиол",
            "прогестерон",
            "тестостерон",
            "кортизол",
            "инсулин",
            "лг",
            "фсг",
        ),
    ),
    (
        ReportType.BIOCHEMISTRY,
        (
            "биохим",
            "биохими",
            "алт",
            "аст",
            "билирубин",
            "креатинин",
            "мочевина",
            "глюкоза",
            "холестерин",
            "общий белок",
            "альбумин",
            "щелочная фосфатаза",
        ),
    ),
    (
        ReportType.URINE,
        (
            "анализ моч",
            "моча",
            "удельный вес",
            "прозрачность мочи",
            "цвет мочи",
            "лейкоциты в моче",
            "эритроциты в моче",
            "белок в моче",
        ),
    ),
    (
        ReportType.BLOOD,
        (
            "анализ крови",
            "кровь",
            "оак",
            "гемоглобин",
            "лейкоцит",
            "эритроцит",
            "тромбоцит",
            "соэ",
            "гематокрит",
        ),
    ),
)


@dataclass(slots=True)
class ParsedPdf:
    source_path: Path
    extracted_text: str
    patient_name: str | None = None
    report_title: str | None = None
    report_date: datetime | None = None
    report_type: ReportType = ReportType.UNKNOWN
    needs_manual_review: bool = False


class PdfParser:
    def parse(self, source_path: Path) -> ParsedPdf:
        extracted_text = self._extract_text(source_path)
        return self.parse_text(extracted_text=extracted_text, source_path=source_path)

    def parse_text(self, extracted_text: str, source_path: Path | None = None) -> ParsedPdf:
        normalized_text = self._normalize_text(extracted_text)
        review_required = len(normalized_text) < 20
        return ParsedPdf(
            source_path=source_path or Path("<memory>"),
            extracted_text=normalized_text,
            patient_name=self._extract_patient_name(normalized_text),
            report_title=self._extract_report_title(normalized_text),
            report_date=self._extract_report_date(normalized_text),
            report_type=self._classify_report_type(
                text=normalized_text,
                source_path=source_path,
            ),
            needs_manual_review=review_required,
        )

    def _extract_text(self, source_path: Path) -> str:
        reader = PdfReader(str(source_path))
        pages: list[str] = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages.append(page_text)
        return "\n".join(pages)

    def _extract_patient_name(self, text: str) -> str | None:
        if not text:
            return None

        for pattern in _PATIENT_PATTERNS:
            match = pattern.search(text)
            if match:
                candidate = self._normalize_person_name(match.group("name"))
                if self._is_plausible_person_name(candidate, strict=False):
                    return candidate

        for line in text.splitlines():
            stripped = line.strip()
            if len(stripped) > 120:
                continue

            match = _FULL_NAME_PATTERN.search(stripped)
            if match:
                candidate = self._normalize_person_name(match.group(1))
                if self._is_plausible_person_name(candidate, strict=True):
                    return candidate

        return None

    def _extract_report_date(self, text: str) -> datetime | None:
        if not text:
            return None

        for pattern in _DATE_LABEL_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue

            parsed = self._parse_date_value(match.group("value"))
            if parsed is not None:
                return parsed

        for pattern in (_DATE_RE, _TEXTUAL_DATE_RE):
            match = pattern.search(text)
            if not match:
                continue

            parsed = self._parse_date_match(match)
            if parsed is not None:
                return parsed

        return None

    def _extract_report_title(self, text: str) -> str | None:
        if not text:
            return None

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or len(stripped) > 120:
                continue

            normalized = self._normalize_for_matching(stripped)
            if normalized.startswith("дата "):
                continue
            if normalized.startswith("перейти на исходный"):
                continue
            if normalized.startswith("документ результатов"):
                continue
            if normalized.startswith("лабораторного тестирования"):
                continue
            if "исполнитель" in normalized:
                continue
            if "результаты исследований не являются диагнозом" in normalized:
                continue
            if "анализ" in normalized or "исследование" in normalized:
                return self._clean_title(stripped)

        return None

    def _classify_report_type(self, text: str, source_path: Path | None) -> ReportType:
        if not text.strip():
            return ReportType.UNKNOWN

        haystack = self._normalize_for_matching(text)
        if source_path is not None:
            haystack = f"{haystack} {self._normalize_for_matching(source_path.name)}"

        best_type = ReportType.OTHER
        best_score = 0
        for report_type, keywords in _REPORT_TYPE_KEYWORDS:
            score = sum(1 for keyword in keywords if keyword in haystack)
            if score > best_score:
                best_type = report_type
                best_score = score

        return best_type if best_score > 0 else ReportType.OTHER

    @staticmethod
    def _normalize_text(text: str) -> str:
        lines = [PdfParser._strip_line(line) for line in text.splitlines()]
        meaningful_lines = [line for line in lines if line]
        return "\n".join(meaningful_lines)

    @staticmethod
    def _strip_line(line: str) -> str:
        return _WHITESPACE_RE.sub(" ", line).strip()

    @staticmethod
    def _normalize_for_matching(text: str) -> str:
        return PdfParser._strip_line(text).lower().replace("ё", "е")

    @staticmethod
    def _normalize_person_name(name: str) -> str:
        parts = re.split(r"\s+", name.strip())
        normalized_parts: list[str] = []
        for part in parts:
            subparts = part.split("-")
            normalized_subparts = [subpart[:1].upper() + subpart[1:].lower() for subpart in subparts]
            normalized_parts.append("-".join(normalized_subparts))
        return " ".join(normalized_parts)

    @staticmethod
    def _clean_title(value: str) -> str:
        return " ".join(value.split())[:160].strip()

    @staticmethod
    def _is_plausible_person_name(value: str, *, strict: bool) -> bool:
        parts = [part for part in value.split() if part]
        if len(parts) != 3:
            return False

        normalized_parts = [part.lower().replace("ё", "е") for part in parts]
        if any(part in _PERSON_STOP_WORDS for part in normalized_parts):
            return False

        if strict and any(len(part) < 3 for part in normalized_parts):
            return False

        return True

    def _parse_date_value(self, value: str) -> datetime | None:
        value = value.strip()
        numeric_match = _DATE_RE.fullmatch(value)
        if numeric_match:
            return self._parse_date_match(numeric_match)

        textual_match = _TEXTUAL_DATE_RE.fullmatch(value)
        if textual_match:
            return self._parse_date_match(textual_match)

        return None

    @staticmethod
    def _parse_date_match(match: re.Match[str]) -> datetime | None:
        day = int(match.group("day"))
        year = int(match.group("year"))
        if year < 100:
            year += 2000

        try:
            if "month" in match.groupdict() and match.group("month").isdigit():
                month = int(match.group("month"))
            else:
                month = _MONTHS[match.group("month").lower()]
            return datetime(year=year, month=month, day=day)
        except (KeyError, ValueError):
            return None
