"""Environment and runtime diagnostics."""

from __future__ import annotations

import os
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import List

from .config import Settings
from .openai_service import OpenAIService, OpenAIServiceError
from .schemas import CheckStatus, DoctorCheck, DoctorReport
from .utils import command_exists


def _status(condition: bool, *, warn: bool = False) -> CheckStatus:
    if condition:
        return CheckStatus.PASS
    return CheckStatus.WARN if warn else CheckStatus.FAIL


def build_doctor_report(settings: Settings, *, probe_openai: bool = True) -> DoctorReport:
    """Assemble the doctor report."""

    checks: List[DoctorCheck] = []
    checks.append(check_python())
    checks.extend(check_native_dependencies())
    checks.extend(check_env(settings))
    checks.extend(check_paths(settings))

    if probe_openai and settings.chatgpt_api_key.get_secret_value():
        checks.append(check_openai(settings))
    else:
        checks.append(
            DoctorCheck(
                name="openai",
                status=CheckStatus.WARN,
                detail="Skipped OpenAI probe.",
            )
        )

    return DoctorReport(checks=checks)


def check_python() -> DoctorCheck:
    ok = sys.version_info >= (3, 11)
    version = ".".join(str(part) for part in sys.version_info[:3])
    detail = "Python {version} detected; v1 targets Python 3.11+.".format(version=version)
    return DoctorCheck(name="python", status=_status(ok), detail=detail)


def check_native_dependencies() -> List[DoctorCheck]:
    checks = []
    required_tools = {
        "pdftoppm": "Provided by poppler; required by the doctor contract even though PyMuPDF does page rendering.",
    }
    for command, explanation in required_tools.items():
        checks.append(
            DoctorCheck(
                name=command,
                status=_status(command_exists(command)),
                detail=explanation,
            )
        )
    checks.append(
        DoctorCheck(
            name="rapidocr",
            status=_status(find_spec("rapidocr_onnxruntime") is not None),
            detail="Preferred OCR backend (PP-OCR models via ONNX Runtime).",
        )
    )
    checks.append(
        DoctorCheck(
            name="paddleocr",
            status=_status(find_spec("paddleocr") is not None, warn=True),
            detail="Optional OCR backend (unstable on macOS arm64).",
        )
    )
    checks.append(
        DoctorCheck(
            name="paddle",
            status=_status(find_spec("paddle") is not None, warn=True),
            detail="Runtime required by PaddleOCR (optional).",
        )
    )
    checks.append(
        DoctorCheck(
            name="tesseract",
            status=_status(command_exists("tesseract"), warn=True),
            detail="Optional fallback OCR backend.",
        )
    )
    return checks


def check_env(settings: Settings) -> List[DoctorCheck]:
    checks = []
    key_present = bool(settings.chatgpt_api_key.get_secret_value())
    model_present = bool(settings.chatgpt_model.strip())
    checks.append(
        DoctorCheck(
            name="CHATGPT_API_KEY",
            status=_status(key_present),
            detail="Configured via environment or .env.",
        )
    )
    checks.append(
        DoctorCheck(
            name="CHATGPT_MODEL",
            status=_status(model_present),
            detail="Configured model: {model}".format(model=settings.chatgpt_model or "<missing>"),
        )
    )
    return checks


def check_paths(settings: Settings) -> List[DoctorCheck]:
    checks = []
    cwd = Path.cwd()
    settings.prepare_directories(cwd)
    for name, path in {
        "output_dir": settings.resolve_output_dir(cwd),
        "data_dir": settings.resolve_data_dir(cwd),
        "database_path": settings.resolve_database_path(cwd),
    }.items():
        writable = os.access(path if path.is_dir() else path.parent, os.W_OK)
        checks.append(
            DoctorCheck(
                name=name,
                status=_status(writable),
                detail=str(path),
            )
        )
    return checks


def check_openai(settings: Settings) -> DoctorCheck:
    try:
        detail = OpenAIService(settings).probe_model()
        return DoctorCheck(name="openai", status=CheckStatus.PASS, detail=detail)
    except OpenAIServiceError as exc:
        return DoctorCheck(name="openai", status=CheckStatus.FAIL, detail=str(exc))
