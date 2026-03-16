from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.reports import FamilyMemberItem, ReportCategoryItem, ReportListItem

MAIN_MENU_FAMILY_TEXT = "Члены семьи"
MAIN_MENU_RECENT_TEXT = "Последние анализы"
MAIN_MENU_STATUS_TEXT = "Состояние сервиса"


def build_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MAIN_MENU_FAMILY_TEXT)],
            [KeyboardButton(text=MAIN_MENU_RECENT_TEXT), KeyboardButton(text=MAIN_MENU_STATUS_TEXT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел",
    )


def build_family_members_keyboard(members: list[FamilyMemberItem]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for member in members:
        builder.button(text=member.display_name, callback_data=f"member:{member.member_id}")
    builder.adjust(1)
    return builder.as_markup()


def build_categories_keyboard(
    member_id: int,
    categories: list[ReportCategoryItem],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(
            text=f"{category.label} ({category.report_count})",
            callback_data=f"category:{member_id}:{category.key}",
        )
    builder.button(text="Назад к семье", callback_data="menu:family")
    builder.adjust(1)
    return builder.as_markup()


def build_reports_keyboard(
    member_id: int,
    category_key: str,
    reports: list[ReportListItem],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for report in reports:
        builder.button(
            text=f"{report.subtitle} | {report.title}",
            callback_data=f"report:{member_id}:{category_key}:{report.report_id}",
        )

    builder.row(
        InlineKeyboardButton(
            text="Назад к категориям",
            callback_data=f"member:{member_id}",
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def build_recent_reports_keyboard(reports: list[ReportListItem]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for report in reports:
        builder.button(
            text=f"{report.subtitle} | {report.title}",
            callback_data=f"recent:{report.report_id}",
        )
    builder.button(text="Члены семьи", callback_data="menu:family")
    builder.adjust(1)
    return builder.as_markup()


def build_report_card_keyboard(
    member_id: int | None,
    category_key: str,
    report_id: int,
    *,
    has_pdf: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Совет ИИ",
        callback_data=f"advice:{report_id}",
    )
    if has_pdf:
        builder.button(
            text="Открыть PDF",
            callback_data=f"pdf:{report_id}",
        )
    if member_id is not None:
        builder.button(
            text="К списку отчетов",
            callback_data=f"category:{member_id}:{category_key}",
        )
    else:
        builder.button(text="К последним анализам", callback_data="menu:recent")
    builder.button(text="Члены семьи", callback_data="menu:family")
    builder.adjust(1)
    return builder.as_markup()
