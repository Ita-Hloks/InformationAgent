from __future__ import annotations

import argparse
import json
import sys

from dotenv import load_dotenv

from .serialization import (
    collection_report_to_payload,
    planning_report_to_payload,
    report_to_payload,
    search_answer_to_payload,
    search_report_to_payload,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RSS 信息搜集与分析 MVP")
    commands = parser.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect", help="只采集、规范化和筛选，不调用 LLM")
    _add_common_arguments(collect_parser, limit_help="最多输出的文章数")
    ingest_parser = commands.add_parser("ingest", help="采集、规范化、筛选并写入数据库，不调用 LLM")
    _add_common_arguments(ingest_parser, limit_help="最多输出的文章数")
    analyze_parser = commands.add_parser("analyze", help="采集后继续调用 LLM 分析")
    _add_common_arguments(analyze_parser, limit_help="最多送入模型的证据数")
    plan_parser = commands.add_parser("plan", help="从筛选后的文章生成搜索计划")
    _add_common_arguments(
        plan_parser,
        limit_help="最多检查的文章数（上限 5）",
        default_limit=5,
    )
    plan_run_parser = commands.add_parser(
        "plan-run",
        help="从数据库已选证据生成并保存搜索计划",
    )
    plan_run_parser.add_argument("run_id", help="ingest 命令返回的研究运行 ID")
    plan_run_parser.add_argument("--timeout", type=float, default=60, help="规划时限（秒）")
    search_parser = commands.add_parser("search", help="采集、生成问题并联网回答")
    _add_common_arguments(
        search_parser,
        limit_help="最多检查的文章数（上限 5）",
        default_limit=5,
    )
    verification_parser = commands.add_parser(
        "verify-search",
        help="验证联网搜索配置、请求和来源返回",
    )
    verification_parser.add_argument("--timeout", type=float, default=60, help="验证时限（秒）")
    return parser


def _add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    limit_help: str,
    default_limit: int = 20,
) -> None:
    parser.add_argument("topic", help="研究主题，例如：AI Agent")
    parser.add_argument("feeds", nargs="+", help="一个或多个 RSS/Atom 地址")
    parser.add_argument("--timeout", type=float, default=60, help="总时限（秒）")
    parser.add_argument("--limit", type=int, default=default_limit, help=limit_help)


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "collect":
        from .orchestration import collect

        report = collect(args.topic, args.feeds, timeout_seconds=args.timeout, limit=args.limit)
        payload = collection_report_to_payload(report)
    elif args.command == "ingest":
        from .orchestration import ingest
        from .serialization import persisted_collection_to_payload

        result = ingest(args.topic, args.feeds, timeout_seconds=args.timeout, limit=args.limit)
        payload = persisted_collection_to_payload(result)
    elif args.command == "analyze":
        from .orchestration import run

        load_dotenv()
        report = run(args.topic, args.feeds, timeout_seconds=args.timeout, limit=args.limit)
        payload = report_to_payload(report)
    elif args.command == "plan":
        from .orchestration import plan

        load_dotenv()
        report = plan(args.topic, args.feeds, timeout_seconds=args.timeout, limit=args.limit)
        payload = planning_report_to_payload(report)
    elif args.command == "plan-run":
        from .orchestration import plan_run
        from .serialization import persisted_planning_to_payload

        load_dotenv()
        result = plan_run(args.run_id, timeout_seconds=args.timeout)
        payload = persisted_planning_to_payload(result)
    elif args.command == "search":
        from .orchestration import search

        load_dotenv()
        report = search(args.topic, args.feeds, timeout_seconds=args.timeout, limit=args.limit)
        payload = search_report_to_payload(report)
    else:
        from .search import verify_connection

        load_dotenv()
        answer = verify_connection(args.timeout)
        payload = search_answer_to_payload(answer)
    _write_json_output(payload)


def _write_json_output(payload: dict[str, object]) -> None:
    if hasattr(sys.stdout, "reconfigure") and (sys.stdout.encoding or "").lower() not in {
        "utf-8",
        "utf8",
    }:
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
