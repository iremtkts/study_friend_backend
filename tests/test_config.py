from pydantic import SecretStr

from app.core.config import Settings


def create_settings(database_url: str) -> Settings:
    return Settings(database_url=database_url, jwt_secret_key=SecretStr("x" * 32))


def test_postgresql_url_uses_psycopg_driver():
    settings = create_settings("postgresql://user:password@localhost:5432/study_friend")
    assert settings.database_url == (
        "postgresql+psycopg://user:password@localhost:5432/study_friend"
    )


def test_explicit_psycopg_and_sqlite_urls_remain_unchanged():
    postgres_url = "postgresql+psycopg://user:password@localhost:5432/study_friend"
    sqlite_url = "sqlite://"
    assert create_settings(postgres_url).database_url == postgres_url
    assert create_settings(sqlite_url).database_url == sqlite_url
