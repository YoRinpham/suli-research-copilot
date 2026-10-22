import os


if os.getenv("RUN_DB_INTEGRATION") != "1":
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://test:test@localhost:5432/research_copilot_test",
    )
