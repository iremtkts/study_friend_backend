from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings


class N8NConfigurationError(Exception):
    pass


class N8NBadResponseError(Exception):
    pass


def _base_url() -> str:
    settings = get_settings()
    base_url = settings.n8n_base_url.strip().rstrip("/")
    secret = settings.n8n_webhook_secret.get_secret_value()
    if not base_url or not secret:
        raise N8NConfigurationError("n8n settings are incomplete.")
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise N8NConfigurationError("N8N_BASE_URL is invalid.") from exc
    if not hostname or parsed.scheme not in {"http", "https"}:
        raise N8NConfigurationError("N8N_BASE_URL is invalid.")
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if hostname.lower() not in local_hosts and parsed.scheme != "https":
        raise N8NConfigurationError("N8N_BASE_URL must use HTTPS except on localhost.")
    return base_url


def is_configured() -> bool:
    try:
        _base_url()
        return True
    except N8NConfigurationError:
        return False


def post_webhook(path: str, payload: dict, *, transport: httpx.BaseTransport | None = None) -> dict:
    settings = get_settings()
    base_url = _base_url()
    secret = settings.n8n_webhook_secret.get_secret_value()
    url = f"{base_url}/webhook/{path.lstrip('/')}"
    with httpx.Client(
        timeout=settings.n8n_timeout_seconds,
        follow_redirects=False,
        verify=True,
        transport=transport,
    ) as client:
        response = client.post(url, json=payload, headers={"X-Webhook-Secret": secret})
    response.raise_for_status()
    try:
        result = response.json()
    except (ValueError, TypeError) as exc:
        raise N8NBadResponseError from exc
    if not isinstance(result, dict):
        raise N8NBadResponseError
    return result
