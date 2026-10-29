"""OpenAI image edits staged for human review in the browser Cropper."""

import base64
import io
import os
from pathlib import Path

from PIL import Image, ImageOps

MODEL = "gpt-image-2.5-sunburst"
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


def api_key() -> str:
    value = os.environ.get("OPENAI_API_KEY", "").strip()
    if value:
        return value
    for file in (Path(__file__).parent / ".env.local",):
        if file.is_file():
            for line in file.read_text(encoding="utf-8-sig").splitlines():
                if line.lstrip().startswith("OPENAI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"\'')
    raise ValueError("OPENAI_API_KEY was not found. Check the local .env.local file.")


def edit_front(source: Path, client=None, operation="restore", comments=None, previous: Path | None = None) -> bytes:
    if operation not in {"restore", "colorize"}:
        raise ValueError("Choose restoration or colorization.")
    if client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ValueError("Install the openai package from requirements.txt first.") from exc
        client = OpenAI(api_key=api_key(), timeout=180)
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        upload = io.BytesIO()
        image.save(upload, format="PNG")
    upload.name = "front.png"
    upload.seek(0)
    prompt = COLORIZATION_PROMPT if operation == 'colorize' else PROMPT
    if comments:
        prompt += ("\nUser-supplied corrections, in chronological order (later corrections override earlier ones). "
                   "Apply these within the preservation rules above; change only what is necessary:\n" +
                   "\n".join(f"{index + 1}. {comment}" for index, comment in enumerate(comments)))
    inputs = upload
    if previous is not None:
        reference = io.BytesIO(previous.read_bytes())
        reference.name = "previous-ai-attempt.png"
        inputs = [upload, reference]
        prompt += ("\nImage 1 is the ORIGINAL and authoritative photographic evidence. Image 2 is a previous "
                   "AI attempt provided only to explain the correction. Do not copy its mistakes or use it "
                   "to override the original's identity, expression, pose, framing or occlusions.")
    response = client.images.edit(model=MODEL, image=inputs, prompt=prompt,
                                  quality="high", size="auto", output_format="png")
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
                  composite_png: bytes | None = None, operation="restore", stage_routing=True) -> list[Path]:
    """Always file through stage_files. Old routing arguments cannot change paths."""
    if operation not in {'restore','colorize'}: raise ValueError('Invalid image operation.')
    sources = [front] + ([composite] if composite else [])
    if composite and composite_png is None:
        raise ValueError("Combined result is missing.")
    if len({path.resolve() for path in sources}) != len(sources):
        raise ValueError("Choose different front and combined files.")
    from stage_files import file_results
    return file_results(sources, [front_png] + ([composite_png] if composite else []), operation)
