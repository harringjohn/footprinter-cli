"""Footprinter HTTP API — FastAPI routers calling the service layer."""


def _load_api_max_limit() -> int:
    try:
        from footprinter.source_registry import get_config

        return get_config().get("limits", {}).get("api_max_limit", 200)
    except Exception:
        return 200


MAX_LIMIT = _load_api_max_limit()
"""Upper bound for `limit` query params on HTTP list/search endpoints."""
