from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _section(row: Mapping[str, Any]) -> dict[str, Any] | None:
    if row.get("section_id") is None:
        return None
    return {
        "id": row["section_id"],
        "subject": row["subject"],
        "parentId": row.get("section_parent_id"),
        "code": row["section_code"],
        "title": row["section_title"],
        "description": row.get("section_description"),
        "sortOrder": row["section_sort_order"],
    }


def test_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "subject": row["subject"],
        "section": _section(row),
        "title": row["title"],
        "slug": row["slug"],
        "description": row["description"],
        "mode": row["mode"],
        "questionCount": row["question_count"],
        "timeLimitSeconds": row["time_limit_seconds"],
    }


def attempt_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "testId": row["test_id"],
        "subject": row["subject"],
        "sectionId": row["section_id"],
        "kind": row["kind"],
        "status": row["status"],
        "title": row["title"],
        "score": _number(row["score"]),
        "maxScore": _number(row["max_score"]),
        "correctCount": row["correct_count"],
        "questionCount": row["question_count"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
    }


TEST_SELECT = """
    SELECT test.id, subject.code AS subject, test.title, test.slug,
           test.description, test.mode::text AS mode, test.time_limit_seconds,
           section.id AS section_id, section.parent_id AS section_parent_id,
           section.code AS section_code, section.title AS section_title,
           section.description AS section_description,
           section.sort_order AS section_sort_order,
           CASE
             WHEN test.mode = 'generated' AND test.slug = 'history-trainer' THEN (
               SELECT count(*)::integer
               FROM app.history_question_metadata AS metadata
               JOIN app.questions AS trainer_question
                 ON trainer_question.id = metadata.question_id
               WHERE trainer_question.status = 'published'
             )
             ELSE count(tq.question_id)::integer
           END AS question_count,
           COALESCE(sum(COALESCE(tq.points_override, qv.default_points)), 0) AS total_points
    FROM app.tests AS test
    JOIN app.subjects AS subject ON subject.id = test.subject_id
    LEFT JOIN app.sections AS section ON section.id = test.section_id
    LEFT JOIN app.test_questions AS tq ON tq.test_id = test.id
    LEFT JOIN app.questions AS question ON question.id = tq.question_id
    LEFT JOIN app.question_versions AS qv
        ON qv.question_id = question.id AND qv.version = question.current_version
"""


ATTEMPT_SELECT = """
    SELECT attempt.id, attempt.test_id, subject.code AS subject,
           attempt.section_id, attempt.kind::text AS kind,
           attempt.status::text AS status, attempt.test_title_snapshot AS title,
           attempt.score, attempt.max_score, attempt.correct_count,
           attempt.question_count, attempt.started_at, attempt.completed_at,
           attempt.user_id, attempt.anonymous_session_id
    FROM app.test_attempts AS attempt
    JOIN app.subjects AS subject ON subject.id = attempt.subject_id
"""


class PracticeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_tests(
        self,
        *,
        subject: str | None,
        section_id: UUID | None,
        mode: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        conditions = ["test.status = 'published'", "subject.is_active = true"]
        parameters: dict[str, Any] = {"offset": offset, "fetch_limit": limit + 1}
        if subject:
            conditions.append("subject.code = :subject")
            parameters["subject"] = subject
        if section_id:
            conditions.append("test.section_id = :section_id")
            parameters["section_id"] = section_id
        if mode:
            conditions.append("test.mode = CAST(:mode AS app.test_mode)")
            parameters["mode"] = mode
        result = await self.session.execute(
            text(
                f"""
                {TEST_SELECT}
                WHERE {' AND '.join(conditions)}
                GROUP BY test.id, subject.code, section.id
                ORDER BY test.title, test.id
                OFFSET :offset
                LIMIT :fetch_limit
                """
            ),
            parameters,
        )
        rows = list(result.mappings())
        return [test_summary(row) for row in rows[:limit]], len(rows) > limit

    async def get_test(self, test_id: UUID) -> dict[str, Any] | None:
        result = await self.session.execute(
            text(
                f"""
                {TEST_SELECT}
                WHERE test.id = :test_id
                  AND test.status = 'published'
                  AND subject.is_active = true
                GROUP BY test.id, subject.code, section.id
                """
            ),
            {"test_id": test_id},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        item = test_summary(row)
        item["totalPoints"] = float(row["total_points"])
        return item

    async def get_attempt(self, attempt_id: UUID) -> dict[str, Any] | None:
        result = await self.session.execute(
            text(f"{ATTEMPT_SELECT} WHERE attempt.id = :attempt_id"),
            {"attempt_id": attempt_id},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        attempt = attempt_summary(row)
        questions = await self.session.execute(
            text(
                """
                SELECT aq.id, question.question_type_code, aq.position,
                       version.prompt, aq.points_possible,
                       answer.response, answer.is_correct, answer.points_awarded,
                       answer.feedback, version.explanation
                FROM app.attempt_questions AS aq
                JOIN app.questions AS question ON question.id = aq.question_id
                JOIN app.question_versions AS version
                  ON version.question_id = aq.question_id
                 AND version.version = aq.question_version
                LEFT JOIN app.attempt_answers AS answer
                  ON answer.attempt_question_id = aq.id
                WHERE aq.attempt_id = :attempt_id
                ORDER BY aq.position
                """
            ),
            {"attempt_id": attempt_id},
        )
        attempt["questions"] = [self._public_question(item) for item in questions.mappings()]
        return attempt

    @staticmethod
    def _public_question(row: Mapping[str, Any]) -> dict[str, Any]:
        answered = row["response"] is not None
        result = None
        if answered:
            result = {
                "attemptQuestionId": row["id"],
                "isCorrect": row["is_correct"],
                "pointsAwarded": _number(row["points_awarded"]),
                "pointsPossible": float(row["points_possible"]),
                "correctAnswer": row["feedback"].get("correctAnswer"),
                "explanation": row["explanation"],
                "relatedContent": [],
            }
        return {
            "attemptQuestionId": row["id"],
            "type": row["question_type_code"],
            "position": row["position"],
            "prompt": row["prompt"],
            "pointsPossible": float(row["points_possible"]),
            "answered": answered,
            "submittedResponse": row["response"] if answered else None,
            "result": result,
        }

    async def get_attempt_owner(self, attempt_id: UUID) -> Mapping[str, Any] | None:
        result = await self.session.execute(
            text(
                """
                SELECT user_id, anonymous_session_id, status::text AS status
                FROM app.test_attempts
                WHERE id = :attempt_id
                """
            ),
            {"attempt_id": attempt_id},
        )
        return result.mappings().one_or_none()
