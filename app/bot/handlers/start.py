from __future__ import annotations

from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.bot.keyboards.main_menu import (
    MAIN_MENU_FAMILY_TEXT,
    MAIN_MENU_RECENT_TEXT,
    MAIN_MENU_STATUS_TEXT,
    build_categories_keyboard,
    build_family_members_keyboard,
    build_main_menu,
    build_recent_reports_keyboard,
    build_report_card_keyboard,
    build_reports_keyboard,
)
from app.services.recommendations import (
    RecommendationGenerationError,
    RecommendationService,
)
from app.services.reports import FamilyMemberItem, ReportCard, ReportService


def create_router(
    report_service: ReportService,
    recommendation_service: RecommendationService,
) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def handle_start(message: Message) -> None:
        await message.answer(
            "Выберите члена семьи, чтобы открыть категории анализов и список отчетов.",
            reply_markup=build_main_menu(),
        )
        await _show_family_members(message=message, report_service=report_service)

    @router.message(F.text == MAIN_MENU_FAMILY_TEXT)
    async def handle_family_menu(message: Message) -> None:
        await _show_family_members(message=message, report_service=report_service)

    @router.message(F.text == MAIN_MENU_RECENT_TEXT)
    async def handle_recent_reports(message: Message) -> None:
        await _show_recent_reports(message=message, report_service=report_service)

    @router.message(F.text == MAIN_MENU_STATUS_TEXT)
    async def handle_service_status(message: Message) -> None:
        overview = await report_service.get_overview()
        await message.answer(
            "Сервис доступен.\n"
            f"Членов семьи: {overview.family_members}\n"
            f"Распознанных отчетов: {overview.reports}",
            reply_markup=build_main_menu(),
        )

    @router.callback_query(F.data == "menu:family")
    async def handle_family_menu_callback(callback: CallbackQuery) -> None:
        await _show_family_members(callback=callback, report_service=report_service)

    @router.callback_query(F.data == "menu:recent")
    async def handle_recent_reports_callback(callback: CallbackQuery) -> None:
        await _show_recent_reports(callback=callback, report_service=report_service)

    @router.callback_query(F.data.startswith("member:"))
    async def handle_member_selected(callback: CallbackQuery) -> None:
        member_id = _parse_int_token(callback.data, 1)
        if member_id is None:
            await callback.answer("Не удалось определить члена семьи.", show_alert=True)
            return

        member = await report_service.get_family_member(member_id)
        if member is None:
            await callback.answer("Член семьи не найден.", show_alert=True)
            return

        categories = await report_service.list_categories_for_member(member_id)
        await _edit_callback_message(
            callback,
            text=_render_categories_text(member, categories),
            reply_markup=build_categories_keyboard(member_id, categories),
        )

    @router.callback_query(F.data.startswith("category:"))
    async def handle_category_selected(callback: CallbackQuery) -> None:
        if callback.data is None:
            await callback.answer("Категория недоступна.", show_alert=True)
            return

        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Категория недоступна.", show_alert=True)
            return

        member_id = _parse_int_token(callback.data, 1)
        category_key = parts[2]
        if member_id is None:
            await callback.answer("Не удалось определить члена семьи.", show_alert=True)
            return

        member = await report_service.get_family_member(member_id)
        if member is None:
            await callback.answer("Член семьи не найден.", show_alert=True)
            return

        reports = await report_service.list_reports_for_member(member_id, category_key)
        await _edit_callback_message(
            callback,
            text=_render_reports_text(member, category_key, reports),
            reply_markup=build_reports_keyboard(member_id, category_key, reports),
        )

    @router.callback_query(F.data.startswith("report:"))
    async def handle_report_selected(callback: CallbackQuery) -> None:
        report_id = _parse_int_token(callback.data, 3)
        if report_id is None:
            await callback.answer("Отчет недоступен.", show_alert=True)
            return

        report_card = await report_service.get_report_card(report_id)
        if report_card is None:
            await callback.answer("Отчет не найден.", show_alert=True)
            return

        await _edit_callback_message(
            callback,
            text=_render_report_card_text(report_card),
            reply_markup=build_report_card_keyboard(
                report_card.family_member_id,
                report_card.category_key,
                report_card.report_id,
                has_pdf=bool(report_card.pdf_path),
            ),
        )

    @router.callback_query(F.data.startswith("recent:"))
    async def handle_recent_report_selected(callback: CallbackQuery) -> None:
        report_id = _parse_int_token(callback.data, 1)
        if report_id is None:
            await callback.answer("Отчет недоступен.", show_alert=True)
            return

        report_card = await report_service.get_report_card(report_id)
        if report_card is None:
            await callback.answer("Отчет не найден.", show_alert=True)
            return

        await _edit_callback_message(
            callback,
            text=_render_report_card_text(report_card),
            reply_markup=build_report_card_keyboard(
                report_card.family_member_id,
                report_card.category_key,
                report_card.report_id,
                has_pdf=bool(report_card.pdf_path),
            ),
        )

    @router.callback_query(F.data.startswith("pdf:"))
    async def handle_open_pdf(callback: CallbackQuery) -> None:
        report_id = _parse_int_token(callback.data, 1)
        if report_id is None:
            await callback.answer("PDF недоступен.", show_alert=True)
            return

        report_card = await report_service.get_report_card(report_id)
        if report_card is None or not report_card.pdf_path:
            await callback.answer("Файл не найден.", show_alert=True)
            return

        pdf_path = Path(report_card.pdf_path)
        if not pdf_path.exists():
            await callback.answer("PDF отсутствует на диске.", show_alert=True)
            return

        await _safe_callback_answer(callback)

        if callback.message is not None:
            await callback.message.answer_document(
                FSInputFile(path=pdf_path, filename=report_card.pdf_filename),
                caption=f"{report_card.title}\n{report_card.report_date_text}",
            )

    @router.callback_query(F.data.startswith("advice:"))
    async def handle_generate_advice(callback: CallbackQuery) -> None:
        report_id = _parse_int_token(callback.data, 1)
        if report_id is None:
            await callback.answer("Совет ИИ недоступен.", show_alert=True)
            return

        await _safe_callback_answer(callback, "Готовлю совет ИИ...")

        try:
            await recommendation_service.generate_for_report(report_id)
        except RecommendationGenerationError as error:
            if callback.message is not None:
                await callback.message.answer(str(error))
            return

        report_card = await report_service.get_report_card(report_id)
        if report_card is None:
            await callback.answer("Отчет не найден.", show_alert=True)
            return

        if callback.message is None:
            return

        try:
            await callback.message.edit_text(
                _render_report_card_text(report_card),
                reply_markup=build_report_card_keyboard(
                    report_card.family_member_id,
                    report_card.category_key,
                    report_card.report_id,
                    has_pdf=bool(report_card.pdf_path),
                ),
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise

    return router


async def _show_family_members(
    *,
    report_service: ReportService,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
) -> None:
    members = await report_service.list_family_members()
    text = _render_family_members_text(members)
    keyboard = build_family_members_keyboard(members) if members else None

    if callback is not None:
        await _edit_callback_message(callback, text=text, reply_markup=keyboard)
        return

    if message is not None:
        await message.answer(text, reply_markup=keyboard)


async def _show_recent_reports(
    *,
    report_service: ReportService,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
) -> None:
    reports = await report_service.list_recent_reports()
    text = _render_recent_reports_text(reports)
    keyboard = build_recent_reports_keyboard(reports)

    if callback is not None:
        await _edit_callback_message(callback, text=text, reply_markup=keyboard)
        return

    if message is not None:
        await message.answer(text, reply_markup=keyboard)


async def _edit_callback_message(
    callback: CallbackQuery,
    *,
    text: str,
    reply_markup,
) -> None:
    if callback.message is None:
        await _safe_callback_answer(callback)
        return

    await _safe_callback_answer(callback)

    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise


async def _safe_callback_answer(
    callback: CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as error:
        error_text = str(error).lower()
        if "query is too old" in error_text or "query id is invalid" in error_text:
            return
        raise


def _parse_int_token(data: str | None, index: int) -> int | None:
    if not data:
        return None

    parts = data.split(":")
    if len(parts) <= index:
        return None

    try:
        return int(parts[index])
    except ValueError:
        return None


def _render_family_members_text(members: list[FamilyMemberItem]) -> str:
    if not members:
        return (
            "Список членов семьи пока пуст.\n"
            "После загрузки и распознавания анализов здесь появятся персональные разделы."
        )

    lines = ["Члены семьи:", ""]
    for member in members:
        lines.append(f"• {member.display_name}")
    lines.append("")
    lines.append("Нажмите на нужного человека, чтобы открыть категории анализов.")
    return "\n".join(lines)


def _render_categories_text(member: FamilyMemberItem, categories) -> str:
    lines = [f"{member.display_name}", "", "Категории анализов:"]
    for category in categories:
        lines.append(f"• {category.label}: {category.report_count}")
    lines.append("")
    lines.append("Откройте категорию, чтобы увидеть список отчетов.")
    return "\n".join(lines)


def _render_reports_text(
    member: FamilyMemberItem,
    category_key: str,
    reports,
) -> str:
    category_title = ReportService.get_category_title(category_key)
    if not reports:
        return (
            f"{member.display_name}\n"
            f"{category_title}\n\n"
            "В этой категории пока нет распознанных отчетов."
        )

    lines = [f"{member.display_name}", category_title, "", "Доступные отчеты:"]
    for report in reports:
        lines.append(f"• {report.subtitle} - {report.title}")
    lines.append("")
    lines.append("Выберите отчет, чтобы открыть карточку анализа.")
    return "\n".join(lines)


def _render_recent_reports_text(reports) -> str:
    if not reports:
        return "Последние анализы пока отсутствуют."

    lines = ["Последние анализы:", ""]
    for report in reports:
        lines.append(f"• {report.subtitle} - {report.title}")
    lines.append("")
    lines.append("Выберите отчет для просмотра карточки анализа.")
    return "\n".join(lines)


def _render_report_card_text(report_card: ReportCard) -> str:
    lines = [
        report_card.title,
        "",
        f"Член семьи: {report_card.family_member_name}",
        f"Категория: {report_card.category_label}",
        f"Дата: {report_card.report_date_text}",
        f"Распознанное ФИО: {report_card.patient_name}",
        "",
        "Краткая выжимка:",
        report_card.summary,
    ]

    if report_card.ai_recommendation:
        lines.extend(
            [
                "",
                "Последний совет ИИ:",
                report_card.ai_recommendation,
            ]
        )

    if report_card.pdf_filename:
        lines.extend(["", f"PDF: {report_card.pdf_filename}"])

    return "\n".join(lines)
