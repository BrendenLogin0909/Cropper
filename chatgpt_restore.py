"""OpenAI image edits staged for human review in the browser Cropper."""

import base64
import io
import os
import time
import unicodedata
from pathlib import Path

from PIL import Image, ImageOps

from app_settings import image_model, image_quality, resolved_api_key
from image_api_errors import is_retryable_rate_limit, retry_after_seconds

PROMPT = (
    "Conservatively restore this old family photograph. Preserve the exact people, facial features, "
    "expressions, poses, clothing, veils and occlusions, objects, text, composition and period character. "
    "Correct faded colour, dust and minor damage only where visible evidence supports it. Do not invent "
    "detail, remove a veil, change faces, change the scene, or colourise a genuinely monochrome print. "
    "Leave uncertain detail soft."
)
COLORIZATION_PROMPT = (
    "Colourise this already restored black-and-white or sepia family photograph, and ONLY colourise it. "
    "Treat the input as fixed photographic evidence: preserve pixel-aligned composition, exact faces, "
    "identity, age, facial proportions, eyes, mouths, teeth, expression, gaze, pose, hands, body shape, "
    "hair, clothing, veils, occlusions, objects, lettering, grain, lighting and shadows. No retouching, "
    "beautification, sharpening, repair, redrawing, cropping, resizing, added detail or removal. "
    "Add restrained, realistic, period-appropriate colour to skin, hair, clothing and surroundings. "
    "Preserve all brightness, texture and fine detail; change chrominance only. Do not invent markings "
    "or modify shapes to suit a colour. Colours not established by evidence are plausible estimates, "
    "not historical facts. Keep uncertain colours subtle rather than oversaturated. "
    "Preserve any already-coloured areas. Return precisely the same framing and aspect ratio."
)

# Kept as an alias for older imports; the live model name comes from Settings.
MODEL = "gpt-image-2.5-sunburst"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def openai_base_url() -> str:
    return os.environ.get("OPENAI_BASE_URL", "").strip() or DEFAULT_OPENAI_BASE_URL

GUIDANCE_LIMIT = 500
# Snapshot-tested engineering text; per-photo notes win over batch context.
OVERRIDE_SENTENCE = "If this note conflicts with the batch context, follow this note."
PREVIOUS_IMAGE_PARAGRAPH = (
    "Image 1 is the ORIGINAL and authoritative photographic evidence. Image 2 is a previous "
    "AI attempt provided only to explain the correction. Do not copy its mistakes or use it "
    "to override the original's identity, expression, pose, framing or occlusions."
)


def strip_guidance_controls(text: str) -> str:
    """Keep newlines; strip other Unicode control characters."""
    return "".join(ch for ch in str(text) if ch == "\n" or unicodedata.category(ch) != "Cc")


def guidance_length(text) -> int:
    """Unicode code-point count after the same trim/control strip as sanitize_guidance."""
    if not isinstance(text, str):
        return 0
    return len(strip_guidance_controls(text).strip())


def sanitize_guidance(text) -> str:
    """Trim and strip controls except newlines. Reject more than 500 Unicode code points."""
    if not isinstance(text, str):
        return ""
    cleaned = strip_guidance_controls(text).strip()
    if len(cleaned) > GUIDANCE_LIMIT:
        raise ValueError("Guidance is limited to 500 characters.")
    return cleaned


def guidance_was_truncated(text) -> bool:
    return guidance_length(text) > GUIDANCE_LIMIT


def build_edit_prompt(operation="restore", comments=None, batch_context=None, photo_note=None,
                      previous=False) -> str:
    """Preservation rules, then batch context, then per-photo note, then the previous-image paragraph, then corrections last."""
    prompt = COLORIZATION_PROMPT if operation == "colorize" else PROMPT
    batch = sanitize_guidance(batch_context)
    note = sanitize_guidance(photo_note)
    if batch:
        prompt += f"\nBatch context:\n{batch}"
    if note:
        prompt += f"\nPer-photo note:\n{note}\n{OVERRIDE_SENTENCE}"
    if previous:
        prompt += f"\n{PREVIOUS_IMAGE_PARAGRAPH}"
    if comments:
        prompt += ("\nUser-supplied corrections, in chronological order (later corrections override earlier ones). "
                   "Apply these within the preservation rules above; change only what is necessary:\n" +
                   "\n".join(f"{index + 1}. {comment}" for index, comment in enumerate(comments)))
    return prompt


def api_key() -> str:
    value = resolved_api_key()
    if value:
        return value
    raise ValueError("Add your OpenAI API key in Settings, or set the OPENAI_API_KEY environment variable.")


def test_key(key: str) -> dict:
    """One cheap models.list call. Never returns or logs provider text or the key."""
    from image_api_errors import QUOTA_MESSAGE, is_quota_error
    secret = (key or "").strip()
    if not secret:
        raise ValueError("Paste an API key first.")
    client = openai_client(api_key_value=secret, timeout=30)
    try:
        client.models.list()
        return {"status": "valid", "message": "This API key is valid."}
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        name = type(exc).__name__
        if is_quota_error(exc):
            return {"status": "no_quota", "message": QUOTA_MESSAGE}
        if status == 401 or getattr(exc, "code", None) == "invalid_api_key":
            return {"status": "invalid", "message": "This API key is invalid."}
        if name in {"APIConnectionError", "APITimeoutError"} or status in {408, 502, 503, 504}:
            return {"status": "network", "message": "Network error: Cropper could not reach OpenAI."}
        return {"status": "error", "message": "The test call failed. Check API access and try again."}


def openai_client(api_key_value=None, timeout=180):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ValueError("Install the openai package from requirements.txt first.") from exc
    return OpenAI(
        api_key=api_key_value if api_key_value is not None else api_key(),
        timeout=timeout,
        max_retries=0,
        base_url=openai_base_url(),
    )


def call_images_edit(client, **kwargs):
    """One provider call, plus exactly one retry with backoff on rate-limit 429 only."""
    try:
        return client.images.edit(**kwargs)
    except Exception as exc:
        if not is_retryable_rate_limit(exc):
            raise
        time.sleep(retry_after_seconds(exc))
        return client.images.edit(**kwargs)


def edit_front(source: Path, client=None, operation="restore", comments=None, previous: Path | None = None,
               quality=None, model=None, preview=False, batch_context=None, photo_note=None) -> bytes:
    if operation not in {"restore", "colorize"}:
        raise ValueError("Choose restoration or colorization.")
    if client is None:
        client = openai_client()
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        upload = io.BytesIO()
        image.save(upload, format="PNG")
    upload.name = "front.png"
    upload.seek(0)
    prompt = build_edit_prompt(operation, comments=comments, batch_context=batch_context,
                              photo_note=photo_note, previous=previous is not None)
    inputs = upload
    if previous is not None:
        reference = io.BytesIO(previous.read_bytes())
        reference.name = "previous-ai-attempt.png"
        inputs = [upload, reference]
    chosen_quality = quality or image_quality(preview=preview)
    chosen_model = model or image_model()
    response = call_images_edit(client, model=chosen_model, image=inputs, prompt=prompt,
                                quality=chosen_quality, size="auto", output_format="png")
    if not response.data or not response.data[0].b64_json:
        raise ValueError("The image API returned no result.")
    result = base64.b64decode(response.data[0].b64_json)
    with Image.open(io.BytesIO(result)) as check:
        check.verify()
    if operation == 'colorize':
        from colorize import colour_only
        return colour_only(source, result)
    return result


def reassemble(front_png: bytes, composite: Path, split: int, layout: str) -> bytes:
    if not 10 <= split <= 90 or layout not in {"right", "below"}:
        raise ValueError("Choose a valid front/back split.")
    with Image.open(composite) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
    with Image.open(io.BytesIO(front_png)) as restored:
        front = restored.convert("RGB")
    if layout == "right":
        edge = round(image.width * split / 100)
        image.paste(front.resize((edge, image.height), Image.Resampling.LANCZOS), (0, 0))
    else:
        edge = round(image.height * split / 100)
        image.paste(front.resize((image.width, edge), Image.Resampling.LANCZOS), (0, 0))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def file_accepted(front: Path, front_png: bytes, composite: Path | None = None,
                  composite_png: bytes | None = None, operation="restore", stage_routing=True,
                  output_paths=None) -> list[Path]:
    """Always file through stage_files. Old routing arguments cannot change paths."""
    if operation not in {'restore','colorize'}: raise ValueError('Invalid image operation.')
    sources = [front] + ([composite] if composite else [])
    if composite and composite_png is None:
        raise ValueError("Combined result is missing.")
    if len({path.resolve() for path in sources}) != len(sources):
        raise ValueError("Choose different front and combined files.")
    from stage_files import file_results
    return file_results(sources, [front_png] + ([composite_png] if composite else []), operation,
                        output_paths=output_paths)
