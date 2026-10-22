# ModelScope Docker deployment

The root Dockerfile builds the existing Next.js frontend and FastAPI backend. Nginx exposes port 7860 and forwards `/api/` to the backend; the browser uses the same origin. No database is bundled. PostgreSQL with pgvector is supplied through `DATABASE_URL` in Studio secrets, alongside `QWEN_API_KEY`.

Each container startup runs `python -m alembic upgrade head` from `backend`, then verifies database `research_copilot`, pgvector, and revision `0010_m6_report_delivery`. Migration or verification failure stops startup. Successful initialization logs `DATABASE_READY` with the database-observed client IP, SSL state and migration revision; no connection password is logged. This client IP can be checked against the RDS whitelist. It is observed at runtime and is not a claim of a permanently fixed Studio egress address.

Keep `STORAGE_ROOT=/mnt/workspace/suli/pdfs` and `M5_STORAGE_ROOT=/mnt/workspace/suli/m5` in plaintext variables. Studio hardware and visibility are managed in the existing platform settings and are not changed by these files. Database migration must run at container startup, never while building the image.

Run the bootstrap unit checks with `python -m unittest discover -s deploy -p test_bootstrap.py -v`. The real platform build and `DATABASE_READY` runtime entry are required to confirm successful deployment.
