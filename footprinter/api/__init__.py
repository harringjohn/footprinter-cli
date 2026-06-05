"""Footprinter HTTP API — FastAPI routers calling the service layer."""

import logging

_logger = logging.getLogger(__name__)


def _get_api_max_limit() -> int:
    try:
        from footprinter.source_registry import get_config

        return get_config().get("limits", {}).get("api_max_limit", 200)
    except Exception:
        _logger.debug("Config unavailable for api_max_limit, using default 200")
        return 200


MAX_LIMIT = _get_api_max_limit()
"""Upper bound for `limit` query params on HTTP list/search endpoints."""
