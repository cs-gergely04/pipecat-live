"""Google Imagen image generation and AI Task prompt/JSON helpers."""

from __future__ import annotations

import json
from contextlib import suppress
from io import BytesIO
from typing import Any

from fastapi import HTTPException
from pipecat.frames.frames import ErrorFrame, URLImageRawFrame

from app.config import DEFAULT_GOOGLE_IMAGEN_MODEL, IntegrationConfig, RuntimeConfig
from app.services.google_credentials import _google_image_api_key, _require_integration
from app.state import IMAGE_GENERATION_KINDS


def _json_from_model_text(text: str) -> Any:
    """Parse JSON from a model answer that should contain only JSON."""

    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.strip("`")
        if clean.lower().startswith("json"):
            clean = clean[4:].strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    start = clean.find("{")
    end = clean.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(clean[start : end + 1])
        except json.JSONDecodeError as err:
            raise ValueError("Model did not return valid JSON") from err
    start = clean.find("[")
    end = clean.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(clean[start : end + 1])
        except json.JSONDecodeError as err:
            raise ValueError("Model did not return valid JSON") from err
    raise ValueError("Model did not return valid JSON")


def _ai_task_prompt(payload: dict[str, Any]) -> str:
    task_name = str(payload.get("task_name") or "AI Task").strip()
    instructions = str(payload.get("instructions") or "").strip()
    fields = payload.get("structure_fields") if isinstance(payload.get("structure_fields"), list) else []
    if not fields:
        return f"Task: {task_name}\n\n{instructions}"

    field_lines = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        required = "required" if field.get("required") else "optional"
        description = str(field.get("description") or "").strip()
        name = str(field.get("name") or "").strip()
        selector = str(field.get("selector") or "").strip()
        detail = f"{name} ({required})"
        extras = ", ".join(part for part in (description, selector) if part)
        field_lines.append(f"- {detail}: {extras}" if extras else f"- {detail}")

    return (
        f"Task: {task_name}\n\n"
        f"{instructions}\n\n"
        "Return only a JSON object for Home Assistant AI Tasks. "
        "Do not include markdown, commentary, or code fences. Fields:\n"
        + "\n".join(field_lines)
    )


def _ai_image_task_prompt(payload: dict[str, Any]) -> str:
    task_name = str(payload.get("task_name") or "Image generation task").strip()
    instructions = str(payload.get("instructions") or "").strip()
    return f"{task_name}\n\n{instructions}".strip()


def _enabled_image_generation_integration(
    config: RuntimeConfig,
    payload: dict[str, Any],
) -> IntegrationConfig:
    provider_id = str(payload.get("provider_id") or config.ai_image_provider_id or "").strip()
    if provider_id:
        integration = config.integration(provider_id)
        if not integration:
            raise HTTPException(status_code=404, detail=f"Image generation integration {provider_id} not found")
        if integration.kind not in IMAGE_GENERATION_KINDS:
            raise HTTPException(status_code=400, detail=f"{integration.name} cannot generate images")
        try:
            return _require_integration(integration, "Image generation", fields=())
        except RuntimeError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

    for integration in config.integrations:
        if integration.kind not in IMAGE_GENERATION_KINDS or not integration.enabled:
            continue
        try:
            return _require_integration(integration, "Image generation", fields=())
        except RuntimeError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

    raise HTTPException(
        status_code=400,
        detail="No image generation integration is enabled. Enable Google Imagen.",
    )


def _image_frame_size(size: Any) -> tuple[int, int] | None:
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        return None
    try:
        width = int(size[0])
        height = int(size[1])
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _encode_image_frame(
    image_data: bytes,
    size: Any,
    image_format: str | None,
) -> tuple[bytes, str, int | None, int | None]:
    if not image_data:
        raise HTTPException(status_code=502, detail="Image provider returned an empty image")

    from PIL import Image

    with suppress(Exception):
        image = Image.open(BytesIO(image_data))
        image.load()
        mime = {
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
        }.get(str(image.format or "").upper(), "image/png")
        return image_data, mime, image.size[0], image.size[1]

    frame_size = _image_frame_size(size)
    if not frame_size:
        raise HTTPException(status_code=502, detail="Image provider returned raw image data without size")

    mode = str(image_format or "RGB").strip() or "RGB"
    try:
        image = Image.frombytes(mode, frame_size, image_data)
    except Exception as err:
        raise HTTPException(status_code=502, detail=f"Image provider returned unsupported image data: {err}") from err

    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "A" in image.mode else "RGB")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue(), "image/png", image.size[0], image.size[1]


async def _run_pipecat_image_generation(
    config: RuntimeConfig,
    integration: IntegrationConfig,
    prompt: str,
) -> dict[str, Any]:
    model = (integration.default_model or "").strip()
    if integration.kind == "google_imagen":
        from pipecat.services.google.image import GoogleImageGenService

        model = model or DEFAULT_GOOGLE_IMAGEN_MODEL
        service = GoogleImageGenService(
            api_key=_google_image_api_key(config, integration),
            settings=GoogleImageGenService.Settings(model=model, number_of_images=1),
        )
    else:
        raise HTTPException(status_code=400, detail=f"{integration.name} cannot generate images")

    session = getattr(service, "_aiohttp_session", None)
    try:
        async for frame in service.run_image_gen(prompt):
            if isinstance(frame, ErrorFrame):
                detail = getattr(frame, "error", None) or str(frame)
                raise HTTPException(status_code=502, detail=str(detail))
            if isinstance(frame, URLImageRawFrame) or hasattr(frame, "image"):
                encoded, mime_type, width, height = _encode_image_frame(
                    getattr(frame, "image", b""),
                    getattr(frame, "size", None),
                    getattr(frame, "format", None),
                )
                return {
                    "image": encoded,
                    "mime_type": mime_type,
                    "width": width,
                    "height": height,
                    "model": model,
                    "revised_prompt": prompt,
                    "source_url": getattr(frame, "url", None),
                }
    finally:
        if session:
            await session.close()

    raise HTTPException(status_code=502, detail=f"{integration.name} did not return an image")
