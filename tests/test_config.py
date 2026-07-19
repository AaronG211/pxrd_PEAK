from pathlib import Path

from pxrd_fetcher.config import Settings


def test_settings_load_from_dotenv(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "CHATGPT_API_KEY=test-key\nCHATGPT_MODEL=gpt-5.4-2026-03-05\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHATGPT_API_KEY", raising=False)
    monkeypatch.delenv("CHATGPT_MODEL", raising=False)

    settings = Settings()

    assert settings.chatgpt_api_key.get_secret_value() == "test-key"
    assert settings.chatgpt_model == "gpt-5.4-2026-03-05"


def test_settings_accepts_openai_alias_keys(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_API_KEY=alias-key\nOPENAI_MODEL=gpt-4.1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    settings = Settings()

    assert settings.chatgpt_api_key.get_secret_value() == "alias-key"
    assert settings.chatgpt_model == "gpt-4.1"


def test_prepare_directories_creates_expected_paths(tmp_path):
    settings = Settings(
        CHATGPT_API_KEY="x",
        CHATGPT_MODEL="gpt-5.4-2026-03-05",
        output_dir=Path("out"),
        data_dir=Path("data"),
        database_path=Path("data/db.sqlite"),
    )

    settings.prepare_directories(tmp_path)

    assert (tmp_path / "out").exists()
    assert (tmp_path / "data").exists()
    assert (tmp_path / "data" / "db.sqlite").parent.exists()
