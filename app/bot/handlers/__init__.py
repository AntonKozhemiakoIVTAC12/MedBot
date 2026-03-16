from aiogram import Dispatcher

from app.bot.handlers.start import create_router
from app.services.recommendations import RecommendationService
from app.services.reports import ReportService


def register_handlers(
    dispatcher: Dispatcher,
    report_service: ReportService,
    recommendation_service: RecommendationService,
) -> None:
    dispatcher.include_router(create_router(report_service, recommendation_service))
