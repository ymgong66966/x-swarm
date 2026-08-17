from xswarm.db import engine_url


def test_bare_postgres_urls_get_the_psycopg3_driver():
    assert engine_url("postgresql://u:p@host:5432/db") == "postgresql+psycopg://u:p@host:5432/db"
    assert engine_url("postgres://u:p@host/db") == "postgresql+psycopg://u:p@host/db"


def test_explicit_driver_and_sqlite_are_left_alone():
    assert engine_url("postgresql+psycopg://u@host/db") == "postgresql+psycopg://u@host/db"
    assert engine_url("sqlite:///xswarm.db") == "sqlite:///xswarm.db"
