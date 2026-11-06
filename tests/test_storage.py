from pathlib import Path

from sqlalchemy import inspect, text

from video_publisher.storage import create_db_engine, create_session_factory, upgrade_database
from video_publisher.storage.migrate import downgrade_database
from video_publisher.storage.models import SettingRow


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'm.db').as_posix()}"


def test_upgrade_creates_tables(tmp_path: Path) -> None:
    url = _url(tmp_path)
    upgrade_database(url)
    engine = create_db_engine(url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert {"settings", "alembic_version"} <= tables


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    url = _url(tmp_path)
    upgrade_database(url)
    upgrade_database(url)  # second start of the app must not fail
    engine = create_db_engine(url)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()
    assert version == "0001"


def test_upgrade_keeps_existing_data(tmp_path: Path) -> None:
    url = _url(tmp_path)
    upgrade_database(url)
    engine = create_db_engine(url)
    with create_session_factory(engine).begin() as session:
        session.add(SettingRow(key="language", value="en"))
    upgrade_database(url)
    with create_session_factory(engine)() as session:
        assert session.get(SettingRow, "language") is not None
    engine.dispose()


def test_downgrade_removes_tables(tmp_path: Path) -> None:
    url = _url(tmp_path)
    upgrade_database(url)
    downgrade_database(url, "base")
    engine = create_db_engine(url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert "settings" not in tables


def test_sqlite_pragmas_enabled(tmp_path: Path) -> None:
    engine = create_db_engine(_url(tmp_path))
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert conn.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
    engine.dispose()
