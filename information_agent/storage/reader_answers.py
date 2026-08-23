from __future__ import annotations

import sqlite3

from ..contracts import project_now
from .common import _format_datetime, _optional_text
from .models import ReaderArticle, ReaderArticleAnswer, ReaderArticleAnswerClaim


class ReaderAnswerPersistenceMixin:
    """持久化文章助手的成功问答和进行中的请求占位。"""

    def claim_article_answer(
        self,
        article: ReaderArticle,
        *,
        request_id: str,
        question: str,
    ) -> ReaderArticleAnswerClaim:
        normalized_request_id = request_id.strip()
        normalized_question = question.strip()
        snapshot_id = article.snapshot_id
        content_hash = article.content_hash
        if not normalized_request_id:
            raise ValueError("request_id 不能为空")
        if not normalized_question:
            raise ValueError("question 不能为空")
        if snapshot_id is None or content_hash is None:
            raise ValueError("文章缺少正文快照标识")

        now = _format_datetime(project_now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM article_answer_requests WHERE request_id = ?",
                (normalized_request_id,),
            ).fetchone()
            if row is not None:
                if (
                    str(row["article_id"]) != article.article.article_id
                    or str(row["snapshot_id"]) != snapshot_id
                    or str(row["content_hash"]) != content_hash
                    or str(row["question"]) != normalized_question
                ):
                    raise ValueError("request_id 已对应其他文章问题")
                return ReaderArticleAnswerClaim(
                    record=_reader_article_answer_from_row(row),
                    owner=False,
                )

            connection.execute(
                """
                INSERT INTO article_answer_requests (
                    request_id, article_id, snapshot_id, content_hash,
                    question, answer, status, created_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, NULL, 'running', ?, NULL)
                """,
                (
                    normalized_request_id,
                    article.article.article_id,
                    snapshot_id,
                    content_hash,
                    normalized_question,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM article_answer_requests WHERE request_id = ?",
                (normalized_request_id,),
            ).fetchone()
        assert row is not None
        return ReaderArticleAnswerClaim(record=_reader_article_answer_from_row(row), owner=True)

    def complete_article_answer(self, request_id: str, answer: str) -> ReaderArticleAnswer:
        normalized_answer = answer.strip()
        if not normalized_answer:
            raise ValueError("answer 不能为空")
        finished_at = _format_datetime(project_now())
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE article_answer_requests
                SET answer = ?, status = 'completed', finished_at = ?
                WHERE request_id = ? AND status = 'running'
                """,
                (normalized_answer, finished_at, request_id.strip()),
            )
            if updated.rowcount != 1:
                row = connection.execute(
                    "SELECT * FROM article_answer_requests WHERE request_id = ?",
                    (request_id.strip(),),
                ).fetchone()
                if row is None:
                    raise ValueError("不存在的文章问答请求")
                if row["status"] == "completed":
                    return _reader_article_answer_from_row(row)
                raise ValueError("文章问答请求不在可完成状态")
            row = connection.execute(
                "SELECT * FROM article_answer_requests WHERE request_id = ?",
                (request_id.strip(),),
            ).fetchone()
        assert row is not None
        return _reader_article_answer_from_row(row)

    def fail_article_answer(self, request_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM article_answer_requests
                WHERE request_id = ? AND status = 'running'
                """,
                (request_id.strip(),),
            )

    def get_article_answer(self, request_id: str) -> ReaderArticleAnswer | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM article_answer_requests WHERE request_id = ?",
                (request_id.strip(),),
            ).fetchone()
        return _reader_article_answer_from_row(row) if row is not None else None

    def get_latest_running_article_answer(
        self,
        article_id: str,
        snapshot_id: str,
    ) -> ReaderArticleAnswer | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM article_answer_requests
                WHERE article_id = ? AND snapshot_id = ? AND status = 'running'
                ORDER BY created_at DESC, request_id DESC
                LIMIT 1
                """,
                (article_id, snapshot_id),
            ).fetchone()
        return _reader_article_answer_from_row(row) if row is not None else None

    def list_article_answers(
        self,
        article_id: str,
        snapshot_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ReaderArticleAnswer], bool]:
        _validate_pagination(limit, offset)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM article_answer_requests
                WHERE article_id = ? AND snapshot_id = ? AND status = 'completed'
                ORDER BY created_at DESC, request_id DESC
                LIMIT ? OFFSET ?
                """,
                (article_id, snapshot_id, limit + 1, offset),
            ).fetchall()
        has_more = len(rows) > limit
        return [_reader_article_answer_from_row(row) for row in rows[:limit]], has_more

    def clear_article_answers(self, article_id: str, *, snapshot_id: str | None = None) -> int:
        with self._connect() as connection:
            if snapshot_id is None:
                deleted = connection.execute(
                    "DELETE FROM article_answer_requests WHERE article_id = ?",
                    (article_id,),
                )
            else:
                deleted = connection.execute(
                    """
                    DELETE FROM article_answer_requests
                    WHERE article_id = ? AND snapshot_id = ?
                    """,
                    (article_id, snapshot_id),
                )
        return deleted.rowcount


def _reader_article_answer_from_row(row: sqlite3.Row) -> ReaderArticleAnswer:
    return ReaderArticleAnswer(
        request_id=str(row["request_id"]),
        article_id=str(row["article_id"]),
        snapshot_id=str(row["snapshot_id"]),
        content_hash=str(row["content_hash"]),
        question=str(row["question"]),
        answer=_optional_text(row["answer"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        finished_at=_optional_text(row["finished_at"]),
    )


def _validate_pagination(limit: int, offset: int) -> None:
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    if offset < 0:
        raise ValueError("offset must be non-negative")
