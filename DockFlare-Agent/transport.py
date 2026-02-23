"""
Transport layer for Agent-to-Master HTTP requests.

Centralizes authentication headers: Bearer API key for master-agent auth,
and optional Cloudflare Access Service Token headers when the Master is
protected by Access policies.
"""

import os
import logging

# Logged once when Service Token is first used.
_service_token_logged = False


def get_master_headers(include_json: bool = True) -> dict:
    """
    Returns headers for HTTP requests to the DockFlare Master.

    Always includes Authorization Bearer (DOCKFLARE_API_KEY).
    If CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET are set, adds
    CF-Access-Client-Id and CF-Access-Client-Secret for Cloudflare Access.

    Args:
        include_json: If True, adds Content-Type: application/json.

    Returns:
        Dict of headers suitable for requests.get/post.
    """
    global _service_token_logged
    api_key = os.getenv("DOCKFLARE_API_KEY", "")
    headers = {"Authorization": f"Bearer {api_key}"}
    if include_json:
        headers["Content-Type"] = "application/json"

    # Cloudflare Access Service Token (optional). Read at call time so
    # load_dotenv() has run. See: https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/
    cf_client_id = os.getenv("CF_ACCESS_CLIENT_ID", "").strip()
    cf_client_secret = os.getenv("CF_ACCESS_CLIENT_SECRET", "").strip()
    if cf_client_id and cf_client_secret:
        headers["CF-Access-Client-Id"] = cf_client_id
        headers["CF-Access-Client-Secret"] = cf_client_secret
        if not _service_token_logged:
            logging.getLogger(__name__).info(
                "Cloudflare Access Service Token auth enabled for Master requests"
            )
            _service_token_logged = True

    return headers
