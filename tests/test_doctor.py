from types import SimpleNamespace

from pxrd_fetcher import doctor
from pxrd_fetcher.openai_service import OpenAIServiceError
from pxrd_fetcher.schemas import CheckStatus


def test_check_native_dependencies(monkeypatch):
    monkeypatch.setattr(doctor, "command_exists", lambda name: name == "tesseract")
    monkeypatch.setattr(
        doctor,
        "find_spec",
        lambda name: object() if name in {"rapidocr_onnxruntime", "paddleocr"} else None,
    )

    checks = doctor.check_native_dependencies()

    assert len(checks) == 5
    assert checks[0].status == CheckStatus.FAIL
    assert checks[1].status == CheckStatus.PASS
    assert checks[2].status == CheckStatus.PASS
    assert checks[3].status == CheckStatus.WARN
    assert checks[4].status == CheckStatus.PASS


def test_check_openai_success(monkeypatch):
    class FakeService:
        def __init__(self, settings):
            self.settings = settings

        def probe_model(self):
            return "ok"

    monkeypatch.setattr(doctor, "OpenAIService", FakeService)
    settings = SimpleNamespace(
        chatgpt_api_key=SimpleNamespace(get_secret_value=lambda: "x"),
        chatgpt_model="gpt-5.4-2026-03-05",
        prepare_directories=lambda cwd=None: None,
        resolve_output_dir=lambda cwd=None: cwd,
        resolve_data_dir=lambda cwd=None: cwd,
        resolve_database_path=lambda cwd=None: cwd / "db.sqlite",
    )

    check = doctor.check_openai(settings)

    assert check.status == CheckStatus.PASS


def test_check_openai_failure(monkeypatch):
    class FakeService:
        def __init__(self, settings):
            self.settings = settings

        def probe_model(self):
            raise OpenAIServiceError("boom")

    monkeypatch.setattr(doctor, "OpenAIService", FakeService)
    settings = SimpleNamespace(
        chatgpt_api_key=SimpleNamespace(get_secret_value=lambda: "x"),
        chatgpt_model="gpt-5.4-2026-03-05",
        prepare_directories=lambda cwd=None: None,
        resolve_output_dir=lambda cwd=None: cwd,
        resolve_data_dir=lambda cwd=None: cwd,
        resolve_database_path=lambda cwd=None: cwd / "db.sqlite",
    )

    check = doctor.check_openai(settings)

    assert check.status == CheckStatus.FAIL
    assert "boom" in check.detail
