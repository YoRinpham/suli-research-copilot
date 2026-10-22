from functools import lru_cache
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.config.settings import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )


def check_database_connection(*, require_vector: bool = False) -> None:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
        if require_vector:
            present = connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            )
            if not present:
                raise RuntimeError("pgvector extension is not installed")


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
