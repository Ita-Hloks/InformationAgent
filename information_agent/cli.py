from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from dotenv import load_dotenv

from .orchestration import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RSS 信息搜集与分析 MVP")
    parser.add_argument("topic", help="研究主题，例如：AI Agent")
    parser.add_argument("feeds", nargs="+", help="一个或多个 RSS/Atom 地址")
    parser.add_argument("--timeout", type=float, default=60, help="总时限（秒）")
    parser.add_argument("--limit", type=int, default=20, help="最多送入模型的证据数")
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    report = run(args.topic, args.feeds, timeout_seconds=args.timeout, limit=args.limit)
    payload = asdict(report)
    payload["status"] = report.status.value
    for item in payload["evidence"]:
        item["collected_at"] = item["collected_at"].isoformat()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
