"""User-facing AI features (enterprise): Glow chat, postmortems, daily summary.

They build on the core AI plumbing — provider abstraction
(``services.ai_client``), opt-in/config (``services.ai_config``) and redaction
(``services.ai_redaction``) — and honour its opt-in: with AI switched off in
Settings > AI nothing is sent anywhere, in every edition.
"""
from extensions import Registry


def register(registry: Registry) -> None:
    from nodeglow_ee.ai import daily_summary, glow, postmortem_api

    registry.add_router(glow.router)
    registry.enable_feature("ai_assistant")

    registry.add_router(postmortem_api.router)
    registry.on_incident_resolved(postmortem_api.on_incident_resolved)
    registry.enable_feature("ai_postmortem")

    registry.add_router(daily_summary.router)
    registry.add_scheduler_hook(daily_summary.schedule)
    registry.enable_feature("ai_daily_summary")
