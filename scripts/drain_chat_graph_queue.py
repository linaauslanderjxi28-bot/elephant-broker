#!/usr/bin/env python3
"""Drain one bounded batch from the asynchronous trade-chat graph extraction queue."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.chat_graph_worker import run_one  # noqa: E402
from elephantbroker.schemas.config import ElephantBrokerConfig  # noqa: E402
import asyncpg  # noqa: E402


async def main_async(args: argparse.Namespace) -> int:
    config = ElephantBrokerConfig.load(args.config_path)
    conn = await asyncpg.connect(args.postgres_dsn or config.postgres_dsn)
    completed: list[dict] = []
    try:
        for _ in range(max(1, args.max_jobs)):
            result = await run_one(
                conn,
                config,
                retry_failed=args.retry_failed,
                max_attempts=max(1, args.max_attempts),
            )
            if result["status"] == "idle":
                break
            completed.append(result)
    finally:
        await conn.close()
    print(json.dumps({"status": "ok", "processed": len(completed), "results": completed}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", default="/etc/elephantbroker/default.yaml")
    parser.add_argument("--postgres-dsn", default="")
    parser.add_argument("--max-jobs", type=int, default=10)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-failed", action="store_true")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
