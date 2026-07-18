from __future__ import annotations

import argparse
import json

from dotenv import load_dotenv

from .serialization import collection_report_to_payload, report_to_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RSS 信息搜集与分析 MVP")
    commands = parser.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect", help="只采集、规范化和筛选，不调用 LLM")
    _add_common_arguments(collect_parser, limit_help="最多输出的文章数")
    analyze_parser = commands.add_parser("analyze", help="采集后继续调用 LLM 分析")
    _add_common_arguments(analyze_parser, limit_help="最多送入模型的证据数")
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser, *, limit_help: str) -> None:
    parser.add_argument("topic", help="研究主题，例如：AI Agent")
    parser.add_argument("feeds", nargs="+", help="一个或多个 RSS/Atom 地址")
    parser.add_argument("--timeout", type=float, default=60, help="总时限（秒）")
    parser.add_argument("--limit", type=int, default=20, help=limit_help)


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "collect":
        from .orchestration.collection import collect

        report = collect(args.topic, args.feeds, timeout_seconds=args.timeout, limit=args.limit)
        payload = collection_report_to_payload(report)
    else:
        from .orchestration.workflow import run

        load_dotenv()
        report = run(args.topic, args.feeds, timeout_seconds=args.timeout, limit=args.limit)
        payload = report_to_payload(report)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
