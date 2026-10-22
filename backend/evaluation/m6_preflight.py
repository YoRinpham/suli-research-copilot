"""Run from backend: python evaluation/m6_preflight.py [--network] [--provider-smoke]."""

import argparse
import json

from app.config.settings import get_settings
from app.services.m6_preflight import run_preflight


def main() -> None:
    parser = argparse.ArgumentParser(description="Sanitized M6 demo preflight")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--http", action="store_true", help="check local frontend and backend health URLs")
    parser.add_argument("--provider-smoke", action="store_true", help="one tiny real Qwen call")
    args = parser.parse_args()
    try:
        settings = get_settings()
    except Exception:
        print(json.dumps({"status": "BLOCKED", "failure_categories": ["configuration_invalid"]}))
        return
    print(json.dumps(run_preflight(settings, provider_smoke=args.provider_smoke,
        network_check=args.network, http_check=args.http), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
