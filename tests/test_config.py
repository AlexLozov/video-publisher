from pathlib import Path

import pytest

from video_publisher.config import Settings, get_settings


def test_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no stray .env
    settings = get_settings()
    assert settings.app_name == "Video Publisher"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.ui_dist_dir is None


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VP_PORT", "9123")
    monkeypatch.setenv("VP_UI_DIST_DIR", str(tmp_path))
    settings = Settings()
    assert settings.port == 9123
    assert settings.ui_dist_dir == tmp_path


def test_dotenv_file_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("VP_APP_NAME=Custom\n", encoding="utf-8")
    assert Settings().app_name == "Custom"


def test_invalid_port_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VP_PORT", "not-a-number")
    with pytest.raises(ValueError):
        Settings()
