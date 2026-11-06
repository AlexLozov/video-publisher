from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from video_publisher.storage import (
    Base,
    create_db_engine,
    create_session_factory,
    upgrade_database,
)
from video_publisher.storage.migrate import alembic_config, downgrade_database
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
    assert version == _head()


def _head() -> str:
    head = ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head()
    assert head is not None
    return head


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


def test_upgrade_from_previous_version_keeps_settings(tmp_path: Path) -> None:
    """A user of v0001 (settings only) updates the app: settings survive, new tables appear."""
    url = _url(tmp_path)
    upgrade_database(url, "0001")
    engine = create_db_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO settings (key, value, updated_at) "
                "VALUES ('language', '\"en\"', '2026-11-06 00:00:00')"
            )
        )
    upgrade_database(url)
    with create_session_factory(engine)() as session:
        row = session.get(SettingRow, "language")
        assert row is not None and row.value == "en"
    assert "publications" in inspect(engine).get_table_names()
    engine.dispose()


def test_orm_models_match_migrations(tmp_path: Path) -> None:
    """Catches a model change that was made without a migration."""
    url = _url(tmp_path)
    upgrade_database(url)
    engine = create_db_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_downgrade_removes_tables(tmp_path: Path) -> None:
    url = _url(tmp_path)
    upgrade_database(url)
    downgrade_database(url, "base")
    engine = create_db_engine(url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert "settings" not in tables
    assert "publications" not in tables


def test_sqlite_pragmas_enabled(tmp_path: Path) -> None:
    engine = create_db_engine(_url(tmp_path))
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert conn.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
    engine.dispose()
