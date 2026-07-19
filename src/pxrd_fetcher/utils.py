"""Shared utility helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


DOI_RE = re.compile(r"(10\.\d{4,9}[_./][^/\s]+)", re.IGNORECASE)


def now_utc() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_doi_like_name(path: Path) -> Optional[str]:
    match = DOI_RE.search(path.stem)
    if not match:
        return None
    return match.group(1).replace("_", "/")


def derive_paper_id(path: Path) -> str:
    doi = detect_doi_like_name(path)
    if doi:
        return doi.replace("/", "_")
    return file_sha256(path)[:16]


def slugify(value: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or fallback


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, indent=2, default=str)


def load_json(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    return json.loads(text)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def encode_image_to_data_url(path: Path) -> str:
    mime = "image/png"
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return "data:{mime};base64,{payload}".format(mime=mime, payload=payload)


def encode_image_object_to_data_url(image: object, *, format: str = "PNG") -> str:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError("Pillow is required for in-memory image encoding.") from exc

    if isinstance(image, Image.Image):
        pil_image = image.convert("RGB")
    else:
        pil_image = Image.fromarray(image).convert("RGB")

    buffer = BytesIO()
    pil_image.save(buffer, format=format)
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    mime = "image/png" if format.upper() == "PNG" else "image/jpeg"
    return "data:{mime};base64,{payload}".format(mime=mime, payload=payload)


def relative_to(base: Path, target: Path) -> str:
    try:
        return str(target.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(target.resolve())


def mean_or_none(values: Iterable[float]) -> Optional[float]:
    data = list(values)
    if not data:
        return None
    return float(sum(data) / len(data))
