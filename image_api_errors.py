"""Actionable Image API failures without exposing credentials or raw responses."""

import re

QUOTA_CODES = {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active", "usage_limit_reached"}
QUOTA_MESSAGE = "your OpenAI account is out of credit"


def _identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,100}", value) else None


def error_body(exc):
    body = getattr(exc, "body", None)
    body = body if isinstance(body, dict) else {}
    if isinstance(body.get("error"), dict):
        body = body["error"]
    return body


def error_tags(exc):
    body = error_body(exc)
    code = _identifier(getattr(exc, "code", None) or body.get("code"))
    kind = _identifier(getattr(exc, "type", None) or body.get("type"))
    return {code, kind}, code, kind


def is_quota_error(exc) -> bool:
    tags, _, _ = error_tags(exc)
    return bool(tags & QUOTA_CODES)


def is_retryable_rate_limit(exc) -> bool:
    return getattr(exc, "status_code", None) == 429 and not is_quota_error(exc)


def retry_after_seconds(exc, default: float = 1.0, cap: float = 30.0) -> float:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    raw = headers.get("retry-after", default)
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = default
    if seconds < 0:
        seconds = default
    return min(cap, max(default, seconds))


def image_api_error(exc):
    status = getattr(exc, "status_code", None)
    body = error_body(exc)
    tags, code, kind = error_tags(exc)
    param = _identifier(getattr(exc, "param", None) or body.get("param"))
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    request_id = _identifier(getattr(exc, "request_id", None) or headers.get("x-request-id"))
    # Inspect upstream text only to classify it; never return or log raw text.
    message = str(body.get("message", "")).lower()
    name = type(exc).__name__
    if is_quota_error(exc):
        payload = {"error": QUOTA_MESSAGE, "code": code, "type": kind, "request_id": request_id}
        return 429, payload
    if status == 401 or "invalid_api_key" in tags:
        reason = "Image API authentication failed. Check Cropper's API key in Settings."
    elif "model_not_found" in tags:
        reason = "The configured image model is unavailable to this API project. Check model access before retrying."
    elif status == 403:
        reason = "Image API access was denied. Check project permissions and whether organization verification is required."
    elif status == 429:
        reason = "Image API rate limit reached. Cropper retried once; wait and retry this photo if it still fails."
    elif tags & {"moderation_blocked", "content_policy_violation", "safety_violations"} or (
        status == 400 and any(word in message for word in ("safety", "moderation", "content policy"))
    ):
        reason = "The image service rejected this photo or instruction during its safety review. Review the photo and instructions before retrying."
    elif status == 413 or tags & {"image_too_large", "payload_too_large"} or (
        status in {400, 422} and any(word in message for word in ("too large", "maximum size", "file size"))
    ):
        reason = "The image upload is too large. Try a smaller copy of this photo."
    elif tags & {"invalid_image", "invalid_image_format", "image_parse_error", "invalid_image_url"}:
        reason = "The image service could not read this photo. Try exporting a fresh PNG or JPEG copy."
    elif status in {400, 404, 422}:
        reason = "The image service rejected the request."
        if param:
            reason += f" Check the {param} setting or input."
        else:
            reason += " Check the photo format and image request settings."
    elif status == 408 or name == "APITimeoutError":
        reason = "The image request timed out. Its completion is unknown; check pending results before manually retrying."
    elif name == "APIConnectionError":
        reason = "Cropper could not connect to the image service. Check the internet connection, proxy or firewall before retrying."
    elif status is not None and 500 <= status <= 599:
        reason = "The image service had a temporary failure. Retry this photo manually later."
    elif isinstance(exc, ValueError) and str(exc) == "The image API returned no result.":
        reason = "The image service returned no image. Retry this photo manually."
    elif isinstance(exc, ValueError) and (
        "API key" in str(exc) or str(exc).startswith("OPENAI_API_KEY")
    ):
        reason = "Add your OpenAI API key in Settings, or set the OPENAI_API_KEY environment variable."
    elif isinstance(exc, ValueError) and str(exc).startswith("Install the openai package"):
        reason = "Cropper's OpenAI dependency is missing. Install its requirements and restart Cropper."
    elif isinstance(exc, OSError):
        reason = "Cropper could not read the source photo or the returned image. Check the file and try a fresh PNG or JPEG copy."
    else:
        reason = "The image request failed unexpectedly. Check API access and connection before manually retrying."
    diagnostics = []
    if isinstance(status, int):
        diagnostics.append(f"HTTP {status}")
    if code:
        diagnostics.append(code)
    elif kind:
        diagnostics.append(kind)
    if request_id:
        diagnostics.append(f"request {request_id}")
    if diagnostics:
        reason += " (" + "; ".join(diagnostics) + ")"
    payload = {"error": reason, "code": code, "type": kind, "request_id": request_id}
    if is_retryable_rate_limit(exc):
        payload["retry_after"] = headers.get("retry-after", "5")
    return status if status in {400, 401, 403, 404, 408, 413, 422, 429} else 502, payload
