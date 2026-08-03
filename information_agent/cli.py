from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from .common.call_log import get_log_directory
from .common.llm import DEFAULT_LLM_TIMEOUT_SECONDS
from .serialization import (
    agent_report_to_payload,
    collection_report_to_payload,
    planning_report_to_payload,
    report_to_payload,
    search_answer_to_payload,
    search_report_to_payload,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RSS 信息搜集与分析 MVP")
    commands = parser.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect", help="采集、规范化并用 LLM 语义筛选")
    _add_common_arguments(
        collect_parser,
        limit_help="最多输出的文章数",
        default_timeout=DEFAULT_LLM_TIMEOUT_SECONDS,
    )
    ingest_parser = commands.add_parser(
        "ingest",
        help="采集、规范化、用 LLM 语义筛选并写入数据库",
    )
    _add_common_arguments(
        ingest_parser,
        limit_help="最多输出的文章数",
        default_timeout=DEFAULT_LLM_TIMEOUT_SECONDS,
    )
    analyze_parser = commands.add_parser("analyze", help="采集后继续调用 LLM 分析")
    _add_common_arguments(
        analyze_parser,
        limit_help="最多送入模型的证据数",
        default_timeout=DEFAULT_LLM_TIMEOUT_SECONDS,
    )
    plan_parser = commands.add_parser("plan", help="从筛选后的文章生成搜索计划")
    _add_common_arguments(
        plan_parser,
        limit_help="最多检查的文章数（上限 5）",
        default_limit=5,
        default_timeout=DEFAULT_LLM_TIMEOUT_SECONDS,
    )
    plan_run_parser = commands.add_parser(
        "plan-run",
        help="从数据库已选证据生成并保存搜索计划",
    )
    plan_run_parser.add_argument("run_id", help="ingest 命令返回的研究运行 ID")
    plan_run_parser.add_argument(
        "--timeout", type=float, default=DEFAULT_LLM_TIMEOUT_SECONDS, help="规划时限（秒）"
    )
    agent_run_parser = commands.add_parser(
        "agent-run",
        help="从数据库证据运行受限搜索 Agent",
    )
    agent_run_parser.add_argument("run_id", help="ingest 命令返回的研究运行 ID")
    agent_run_parser.add_argument(
        "--timeout", type=float, default=DEFAULT_LLM_TIMEOUT_SECONDS, help="Agent 总时限（秒）"
    )
    agent_run_parser.add_argument(
        "--max-steps",
        type=int,
        default=3,
        help="最大决策步骤数",
    )
    agent_run_parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="模型或搜索工具单步最大尝试次数",
    )
    search_parser = commands.add_parser("search", help="采集、生成问题并联网回答")
    _add_common_arguments(
        search_parser,
        limit_help="最多检查的文章数（上限 5）",
        default_limit=5,
        default_timeout=DEFAULT_LLM_TIMEOUT_SECONDS,
    )
    verification_parser = commands.add_parser(
        "verify-search",
        help="验证联网搜索配置、请求和来源返回",
    )
    verification_parser.add_argument(
        "--timeout", type=float, default=DEFAULT_LLM_TIMEOUT_SECONDS, help="验证时限（秒）"
    )
    for command_parser in (
        collect_parser,
        ingest_parser,
        analyze_parser,
        plan_parser,
        plan_run_parser,
        agent_run_parser,
        search_parser,
        verification_parser,
    ):
        command_parser.add_argument(
            "--output",
            type=Path,
            help="将 UTF-8 JSON 写入文件；裸文件名会写入 log/，同名文件会被覆盖",
        )
    return parser


def _add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    limit_help: str,
    default_limit: int = 20,
    default_timeout: float = 60,
) -> None:
    parser.add_argument("topic", help="研究主题，例如：AI Agent")
    parser.add_argument("feeds", nargs="+", help="一个或多个 RSS/Atom 地址")
    parser.add_argument("--timeout", type=float, default=default_timeout, help="总时限（秒）")
    parser.add_argument("--limit", type=int, default=default_limit, help=limit_help)


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "collect":
        from .orchestration import collect

        load_dotenv()
        report = collect(args.topic, args.feeds, timeout_seconds=args.timeout, limit=args.limit)
        payload = collection_report_to_payload(report)
    elif args.command == "ingest":
        from .orchestration import ingest
        from .serialization import persisted_collection_to_payload

        load_dotenv()
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
    elif args.command == "agent-run":
        from .orchestration import agent_run

        load_dotenv()
        report = agent_run(
            args.run_id,
            timeout_seconds=args.timeout,
            max_steps=args.max_steps,
            max_attempts=args.max_attempts,
        )
        payload = agent_report_to_payload(report)
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
    _write_json_output(payload, args.output)


def _write_json_output(payload: dict[str, object], output_path: Path | None = None) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output_path is not None:
        target_path = _resolve_output_path(output_path)
        target_path.write_text(serialized, encoding="utf-8", newline="\n")
        return
    if hasattr(sys.stdout, "reconfigure") and (sys.stdout.encoding or "").lower() not in {
        "utf-8",
        "utf8",
    }:
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(serialized)


def _resolve_output_path(output_path: Path) -> Path:
    if output_path.is_absolute() or output_path.parent != Path("."):
        return output_path
    log_directory = get_log_directory()
    log_directory.mkdir(parents=True, exist_ok=True)
    return log_directory / output_path.name


if __name__ == "__main__":
    main()
