"""OCR backend abstraction; RapidOCR is preferred (stable ONNX), PaddleOCR optional."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, List, Optional, Protocol, Sequence, Tuple

import numpy as np


class OCREngineError(RuntimeError):
    """Raised when no usable OCR backend is available."""


@dataclass(frozen=True)
class OCRToken:
    """One OCR detection with normalized confidence and geometry."""

    text: str
    confidence: float
    bbox: Tuple[int, int, int, int]
    polygon: Tuple[Tuple[int, int], ...]


class OCRBackend(Protocol):
    """Minimal OCR backend protocol used by preprocess and digitize."""

    name: str

    def detect_tokens(self, image: object, *, min_confidence: float = 0.0) -> List[OCRToken]:
        """Return OCR tokens detected in the supplied image."""

    def extract_text(self, image: object, *, min_confidence: float = 0.0) -> str:
        """Return OCR text for the supplied image."""


def _merge_lines(parts: Iterable[str]) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _to_rgb_array(image: object) -> np.ndarray:
    if isinstance(image, np.ndarray):
        if image.ndim == 2:
            return np.stack([image] * 3, axis=-1)
        return image

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - import guard
        raise OCREngineError("Pillow is required for OCR image conversion.") from exc

    if isinstance(image, Image.Image):
        return np.asarray(image.convert("RGB"))
    raise TypeError("Unsupported OCR image input type: {kind}".format(kind=type(image)))


class RapidOCRBackend:
    """RapidOCR (ONNX Runtime) backend — same PP-OCR models, no paddlepaddle."""

    name = "rapidocr"

    def detect_tokens(self, image: object, *, min_confidence: float = 0.0) -> List[OCRToken]:
        array = _to_rgb_array(image)
        result, _ = _load_rapid_ocr()(array)
        tokens: List[OCRToken] = []
        if not result:
            return tokens
        for entry in result:
            if not entry or len(entry) < 3:
                continue
            polygon_raw, text, score = entry[0], entry[1], entry[2]
            cleaned = str(text or "").strip()
            try:
                confidence = float(score or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            if not cleaned or confidence < min_confidence:
                continue
            poly = np.asarray(polygon_raw, dtype=float).reshape(-1, 2)
            x0 = int(np.floor(poly[:, 0].min()))
            y0 = int(np.floor(poly[:, 1].min()))
            x1 = int(np.ceil(poly[:, 0].max()))
            y1 = int(np.ceil(poly[:, 1].max()))
            tokens.append(
                OCRToken(
                    text=cleaned,
                    confidence=confidence,
                    bbox=(x0, y0, x1, y1),
                    polygon=tuple((int(round(x)), int(round(y))) for x, y in poly.tolist()),
                )
            )
        tokens.sort(key=lambda token: (token.bbox[1], token.bbox[0]))
        return tokens

    def extract_text(self, image: object, *, min_confidence: float = 0.0) -> str:
        return _merge_lines(token.text for token in self.detect_tokens(image, min_confidence=min_confidence))


class PaddleOCRBackend:
    """PaddleOCR-backed OCR detector/recognizer."""

    name = "paddleocr"

    def detect_tokens(self, image: object, *, min_confidence: float = 0.0) -> List[OCRToken]:
        array = _to_rgb_array(image)
        tokens: List[OCRToken] = []
        for result in _load_paddle_ocr().predict(array):
            texts: Sequence[str] = result.get("rec_texts", [])
            scores: Sequence[float] = result.get("rec_scores", [])
            polygons: Sequence[object] = result.get("rec_polys", [])
            for text, score, polygon in zip(texts, scores, polygons):
                cleaned = str(text or "").strip()
                confidence = float(score or 0.0)
                if not cleaned or confidence < min_confidence:
                    continue
                poly = np.asarray(polygon, dtype=float).reshape(-1, 2)
                x0 = int(np.floor(poly[:, 0].min()))
                y0 = int(np.floor(poly[:, 1].min()))
                x1 = int(np.ceil(poly[:, 0].max()))
                y1 = int(np.ceil(poly[:, 1].max()))
                tokens.append(
                    OCRToken(
                        text=cleaned,
                        confidence=confidence,
                        bbox=(x0, y0, x1, y1),
                        polygon=tuple((int(round(x)), int(round(y))) for x, y in poly.tolist()),
                    )
                )
        tokens.sort(key=lambda token: (token.bbox[1], token.bbox[0]))
        return tokens

    def extract_text(self, image: object, *, min_confidence: float = 0.0) -> str:
        return _merge_lines(token.text for token in self.detect_tokens(image, min_confidence=min_confidence))


class TesseractOCRBackend:
    """pytesseract-backed fallback OCR implementation."""

    name = "tesseract"

    def detect_tokens(self, image: object, *, min_confidence: float = 0.0) -> List[OCRToken]:
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - import guard
            raise OCREngineError("pytesseract is not installed.") from exc

        array = _to_rgb_array(image)
        pil_image = Image.fromarray(array)
        data = pytesseract.image_to_data(
            pil_image,
            output_type=pytesseract.Output.DICT,
            config="--psm 11",
        )

        tokens: List[OCRToken] = []
        for text, left, top, width, height, conf in zip(
            data.get("text", []),
            data.get("left", []),
            data.get("top", []),
            data.get("width", []),
            data.get("height", []),
            data.get("conf", []),
        ):
            cleaned = str(text or "").strip()
            try:
                confidence = max(0.0, min(1.0, float(conf) / 100.0))
            except (TypeError, ValueError):
                confidence = 0.0
            if not cleaned or confidence < min_confidence:
                continue
            x0 = int(left)
            y0 = int(top)
            x1 = int(left + width)
            y1 = int(top + height)
            polygon = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
            tokens.append(
                OCRToken(
                    text=cleaned,
                    confidence=confidence,
                    bbox=(x0, y0, x1, y1),
                    polygon=polygon,
                )
            )
        tokens.sort(key=lambda token: (token.bbox[1], token.bbox[0]))
        return tokens

    def extract_text(self, image: object, *, min_confidence: float = 0.0) -> str:
        return _merge_lines(token.text for token in self.detect_tokens(image, min_confidence=min_confidence))


_ALIASES = {
    "rapid": "rapidocr",
    "rapidocr": "rapidocr",
    "rapid_ocr": "rapidocr",
    "paddle": "paddle",
    "paddleocr": "paddle",
    "tesseract": "tesseract",
    "pytesseract": "tesseract",
}


def get_ocr_backend(preferred: str = "rapidocr", *, allow_fallback: bool = True) -> OCRBackend:
    """Return the preferred OCR backend, falling back through rapid → tesseract → paddle."""

    normalized = _ALIASES.get((preferred or "rapidocr").strip().lower(), "rapidocr")
    backends = [normalized]
    if allow_fallback:
        for candidate in ("rapidocr", "tesseract", "paddle"):
            if candidate not in backends:
                backends.append(candidate)

    errors: List[str] = []
    for backend_name in backends:
        try:
            if backend_name == "rapidocr":
                _validate_rapid_install()
                return RapidOCRBackend()
            if backend_name == "paddle":
                _validate_paddle_install()
                return PaddleOCRBackend()
            if backend_name == "tesseract":
                _validate_tesseract_install()
                return TesseractOCRBackend()
        except OCREngineError as exc:
            errors.append(str(exc))
            continue

    raise OCREngineError("; ".join(errors) or "No OCR backend is available.")


def _validate_rapid_install() -> None:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError as exc:
        raise OCREngineError("rapidocr-onnxruntime is not installed.") from exc


def _validate_paddle_install() -> None:
    try:
        import paddle  # noqa: F401
        import paddleocr  # noqa: F401
    except ImportError as exc:
        raise OCREngineError("PaddleOCR is not installed.") from exc


def _validate_tesseract_install() -> None:
    try:
        import pytesseract  # noqa: F401
    except ImportError as exc:
        raise OCREngineError("pytesseract is not installed.") from exc


@lru_cache(maxsize=1)
def _load_rapid_ocr():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:  # pragma: no cover - import guard
        raise OCREngineError("rapidocr-onnxruntime is not installed.") from exc
    return RapidOCR()


@lru_cache(maxsize=1)
def _load_paddle_ocr():
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:  # pragma: no cover - import guard
        raise OCREngineError("PaddleOCR is not installed.") from exc

    return PaddleOCR(
        lang="en",
        ocr_version="PP-OCRv5",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
