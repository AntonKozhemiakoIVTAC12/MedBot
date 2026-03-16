from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from app.db import ReportType
from app.parsers import PdfParser


class PdfParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = PdfParser()

    def test_extracts_patient_name_and_numeric_date(self) -> None:
        parsed = self.parser.parse_text(
            extracted_text=(
                "Пациент: ИВАНОВ ИВАН ИВАНОВИЧ\n"
                "Дата исследования: 14.03.2026\n"
                "Общий анализ крови"
            ),
            source_path=Path("report.pdf"),
        )

        self.assertEqual(parsed.patient_name, "Иванов Иван Иванович")
        self.assertEqual(parsed.report_date, datetime(2026, 3, 14))
        self.assertEqual(parsed.report_type, ReportType.BLOOD)
        self.assertFalse(parsed.needs_manual_review)

    def test_prefers_biochemistry_over_generic_blood_keywords(self) -> None:
        parsed = self.parser.parse_text(
            extracted_text=(
                "Биохимический анализ крови\n"
                "Показатели: АЛТ, АСТ, билирубин, креатинин"
            ),
            source_path=Path("bio.pdf"),
        )

        self.assertEqual(parsed.report_type, ReportType.BIOCHEMISTRY)

    def test_supports_textual_date_and_hormone_classification(self) -> None:
        parsed = self.parser.parse_text(
            extracted_text=(
                "ФИО пациента: ПЕТРОВА МАРИЯ ИВАНОВНА\n"
                "Дата анализа: 5 января 2026\n"
                "Гормональное исследование: ТТГ, Т4 свободный"
            ),
            source_path=Path("hormones.pdf"),
        )

        self.assertEqual(parsed.patient_name, "Петрова Мария Ивановна")
        self.assertEqual(parsed.report_date, datetime(2026, 1, 5))
        self.assertEqual(parsed.report_type, ReportType.HORMONES)

    def test_extracts_invitro_title_and_sample_date(self) -> None:
        parsed = self.parser.parse_text(
            extracted_text=(
                "Перейти на исходный\n"
                "документ результатов\n"
                "лабораторного тестирования\n"
                "ЦЫГАНКОВА ТАТЬЯНА ИВАНОВНА\n"
                "Дата взятия образца: 03.12.2025 09:55\n"
                "Дата печати результата: 06.12.2025\n"
                "Клинический анализ крови\n"
                "Гемоглобин 11.2 г/дл\n"
                "Лейкоциты 4.62 тыс/мкл\n"
            ),
            source_path=Path("872488625_344581031_0_ЦЫГАНКОВА.pdf"),
        )

        self.assertEqual(parsed.patient_name, "Цыганкова Татьяна Ивановна")
        self.assertEqual(parsed.report_title, "Клинический анализ крови")
        self.assertEqual(parsed.report_date, datetime(2025, 12, 3))
        self.assertEqual(parsed.report_type, ReportType.BLOOD)

    def test_does_not_treat_medical_terms_as_patient_name(self) -> None:
        parsed = self.parser.parse_text(
            extracted_text=(
                "ЛПОНП групп холестерин\n"
                "Холестерин общий 5.1\n"
                "Дата анализа: 14.03.2026\n"
            ),
            source_path=Path("lipids.pdf"),
        )

        self.assertIsNone(parsed.patient_name)
        self.assertEqual(parsed.report_type, ReportType.BIOCHEMISTRY)

    def test_marks_empty_text_for_manual_review(self) -> None:
        parsed = self.parser.parse_text("", source_path=Path("scan.pdf"))

        self.assertTrue(parsed.needs_manual_review)
        self.assertEqual(parsed.report_type, ReportType.UNKNOWN)
        self.assertIsNone(parsed.patient_name)
        self.assertIsNone(parsed.report_date)


if __name__ == "__main__":
    unittest.main()
