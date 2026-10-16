"""Google Cloud & Gemini API key, service-account credentials, and HTTP retry helpers."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from typing import Any

from fastapi import HTTPException
from loguru import logger

from app.config import IntegrationConfig, RuntimeConfig
from app.state import PROVIDER_RETRY_STATUSES


def _secret(integration: IntegrationConfig | None, field: str = "api_key") -> str:
    if not integration:
        return ""
    return str(getattr(integration, field, "") or "").strip()


def _require_integration(
    integration: IntegrationConfig | None,
    role: str,
    fields: tuple[str, ...] = ("api_key",),
) -> IntegrationConfig:
    if not integration:
        raise RuntimeError(f"{role} integration is not selected")
    if not integration.enabled:
        raise RuntimeError(f"{integration.name} is disabled")
    if fields and not any(_secret(integration, field) for field in fields):
        readable = " or ".join(fields)
        raise RuntimeError(f"{integration.name} is missing {readable}")
    return integration


def _integration_api_key(
    integration: IntegrationConfig,
    role: str,
    fallback: str = "",
) -> str:
    api_key = (integration.api_key or fallback or "").strip()
    if not api_key:
        raise RuntimeError(f"{integration.name} is missing api_key for {role}")
    return api_key


def _integration_api_key_or_400(
    integration: IntegrationConfig,
    role: str,
    fallback: str = "",
) -> str:
    try:
        return _integration_api_key(integration, role, fallback)
    except RuntimeError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


def _google_image_api_key(config: RuntimeConfig, integration: IntegrationConfig) -> str:
    candidates = [
        integration.api_key,
        (config.integration("gemini-cloud").api_key if config.integration("gemini-cloud") else ""),
        (config.integration("gemini").api_key if config.integration("gemini") else ""),
        os.getenv("GOOGLE_API_KEY", ""),
    ]
    api_key = next((candidate.strip() for candidate in candidates if candidate and candidate.strip()), "")
    if not api_key:
        raise HTTPException(status_code=400, detail=f"{integration.name} is missing api_key for image generation")
    return api_key


def _google_cloud_credentials(integration: IntegrationConfig):
    from google.auth import default as google_auth_default
    from google.oauth2 import service_account

    if integration.credentials_json:
        return service_account.Credentials.from_service_account_info(
            json.loads(integration.credentials_json)
        )
    if integration.credentials_path:
        return service_account.Credentials.from_service_account_file(integration.credentials_path)
    credentials, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return credentials


def _provider_status(err: Exception) -> int | None:
    response = getattr(err, "response", None)
    status = getattr(response, "status_code", None) or getattr(err, "status_code", None)
    try:
        return int(status) if status else None
    except (TypeError, ValueError):
        return None


def _provider_body(err: Exception) -> Any:
    body = getattr(err, "body", None)
    if body:
        return body
    response = getattr(err, "response", None)
    if response is None:
        return None
    with suppress(Exception):
        return response.json()
    with suppress(Exception):
        return response.text
    return None


def _provider_message_from_body(body: Any) -> str:
    if isinstance(body, list):
        return "; ".join(filter(None, (_provider_message_from_body(item) for item in body)))
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("status") or error.get("code") or "").strip()
        if isinstance(error, str):
            return error.strip()
        return str(body.get("message") or body.get("detail") or "").strip()
    if isinstance(body, str):
        return body.strip()
    return ""


def _provider_safe_detail(label: str, err: Exception) -> str:
    status = _provider_status(err)
    message = _provider_message_from_body(_provider_body(err)) or str(err).split(" for url ")[0]
    message = message.replace("\n", " ").strip()
    if status:
        return f"{label} failed with HTTP {status}: {message}"
    return f"{label} failed: {message}"


def _provider_http_exception(label: str, err: Exception) -> HTTPException:
    if isinstance(err, HTTPException):
        return err
    status = _provider_status(err)
    http_status = status if status in {400, 401, 403, 404, 408, 409, 422, 429, 500, 502, 503, 504} else 502
    if http_status >= 500:
        http_status = 503
    return HTTPException(status_code=http_status, detail=_provider_safe_detail(label, err))


def _provider_retryable(err: Exception) -> bool:
    status = _provider_status(err)
    return bool(status in PROVIDER_RETRY_STATUSES)


async def _provider_call(label: str, request, attempts: int = 3):
    for attempt in range(1, attempts + 1):
        try:
            return await request()
        except HTTPException:
            raise
        except Exception as err:
            if attempt >= attempts or not _provider_retryable(err):
                raise _provider_http_exception(label, err) from err
            logger.warning(
                "{} attempt {}/{} failed, retrying: {}",
                label,
                attempt,
                attempts,
                _provider_safe_detail(label, err),
            )
            await asyncio.sleep(min(2.0, 0.35 * (2 ** (attempt - 1))))

    raise HTTPException(status_code=503, detail=f"{label} failed after retries")
