"""Initialize the supplied RDS database, then supervise the Studio services."""

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REVISION = "0010_m6_report_delivery"


def redact(message: str) -> str:
    url = os.environ.get("DATABASE_URL", "")
    password = urlsplit(url).password if url else None
    secrets = [url, os.environ.get("QWEN_API_KEY", ""), password or "", unquote(password or "")]
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        message = message.replace(secret, "[REDACTED]")
    return re.sub(r"postgres(?:ql)?(?:\+psycopg)?://[^\s]+", "[REDACTED_DATABASE_URL]", message)


def database_status() -> dict:
    import psycopg

    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url, connect_timeout=10, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user, inet_client_addr()::text, "
                           "(SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid())")
            database, user, client_ip, ssl = cursor.fetchone()
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
            vector = cursor.fetchone()
            cursor.execute("SELECT version_num FROM alembic_version")
            revision = cursor.fetchone()
            cursor.execute("SELECT count(*) FROM information_schema.tables "
                           "WHERE table_schema='public' AND table_type='BASE TABLE'")
            return {"database": database, "user": user, "client_ip": client_ip, "ssl": ssl,
                    "vector_version": vector[0] if vector else None,
                    "revision": revision[0] if revision else None,
                    "public_table_count": cursor.fetchone()[0]}


def initialize(root: Path) -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql+psycopg://") or unquote(urlsplit(url).path) != "/research_copilot":
        raise ValueError("DATABASE_URL must target the research_copilot PostgreSQL database")
    for key, suffix in [("STORAGE_ROOT", "pdfs"), ("M5_STORAGE_ROOT", "m5")]:
        Path(os.environ.get(key, f"/mnt/workspace/suli/{suffix}")).mkdir(parents=True, exist_ok=True)
    print("[SULI] Running database migrations in deployment environment", flush=True)
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                            cwd=root / "backend", capture_output=True, text=True, timeout=180)
    output = redact((result.stdout or "") + (result.stderr or ""))
    if output:
        print(output, flush=True)
    if result.returncode:
        raise RuntimeError("Database migration failed; application services were not started")
    status = database_status()
    if status["database"] != "research_copilot" or not status.get("vector_version") or status.get("revision") != EXPECTED_REVISION:
        raise RuntimeError("Database schema or vector verification failed")
    print("[SULI] DATABASE_READY " + json.dumps(status), flush=True)


def serve(root: Path) -> int:
    children = []
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    frontend_environment = dict(os.environ, HOSTNAME="127.0.0.1", PORT="3000")
    try:
        children.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"], cwd=root / "backend"))
        children.append(subprocess.Popen(["node", "server.js"], cwd=root / "frontend", env=frontend_environment))
        children.append(subprocess.Popen(["nginx", "-c", str(root / "deploy" / "nginx.conf"), "-g", "daemon off;"]))
        while not stopping:
            for child in children:
                if child.poll() is not None:
                    print("[SULI] A service exited; stopping the container", flush=True)
                    return child.returncode or 1
            time.sleep(0.5)
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main(root: Path = ROOT) -> int:
    try:
        initialize(root)
        return serve(root)
    except Exception as error:
        print("[SULI] STARTUP_FAILED " + redact(str(error)), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
