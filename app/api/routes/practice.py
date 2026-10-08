from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.api.dependencies import OptionalPrincipal
from app.api.routes.catalog import Session, _offset, _page
from app.core.problems import ProblemException
from app.core.security import generate_refresh_token, hash_secret
from app.repositories.practice import PracticeRepository
from app.services.admin_notifications import deliver_admin_notifications, queue_admin_notification
from app.services.grading import grade_response
from app.services.student_progress import MISTAKES_QUERY

router = APIRouter(tags=["Tests"])
SubjectCode = Literal["history", "society"]


class StartAttemptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    test_id: UUID = Field(alias="testId")


class SubmitAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    attempt_question_id: UUID = Field(alias="attemptQuestionId")
    response: Any


class HistoryTrainerStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_numbers: list[int] = Field(default_factory=list, alias="taskNumbers")
    periods: list[str] = Field(default_factory=list)
    categories: list[Literal["maps", "culture", "wwii", "tables"]] = Field(
        default_factory=list
    )
    question_count: int | None = Field(default=None, alias="questionCount", ge=1, le=5000)
    mistakes_only: bool = Field(default=False, alias="mistakesOnly")
    review_only: bool = Field(default=False, alias="reviewOnly")


class HistoryTrainerCountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mistakes_only: bool = Field(default=False, alias="mistakesOnly")
    review_only: bool = Field(default=False, alias="reviewOnly")
    task_numbers: list[int] = Field(default_factory=list, alias="taskNumbers")
    periods: list[str] = Field(default_factory=list)
    categories: list[Literal["maps", "culture", "wwii", "tables"]] = Field(
        default_factory=list
    )


class QuestionErrorReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    attempt_question_id: UUID = Field(alias="attemptQuestionId")
    message: str = Field(min_length=5, max_length=2000)


def _history_trainer_filters(
    task_numbers: list[int],
    periods: list[str],
    categories: list[str],
) -> tuple[list[str], dict[str, Any]]:
    conditions = ["question.status = 'published'"]
    parameters: dict[str, Any] = {}
    if task_numbers:
        conditions.append("metadata.task_number = ANY(CAST(:task_numbers AS integer[]))")
        parameters["task_numbers"] = task_numbers
    if periods:
        conditions.append("metadata.period = ANY(CAST(:periods AS text[]))")
        parameters["periods"] = periods
    if categories:
        category_conditions: list[str] = []
        metadata_categories = [item for item in categories if item != "maps"]
        if "maps" in categories:
            category_conditions.append("metadata.task_number BETWEEN 9 AND 12")
        if metadata_categories:
            category_conditions.append(
                "metadata.categories ?| CAST(:categories AS text[])"
            )
            parameters["categories"] = metadata_categories
        conditions.append(f"({' OR '.join(category_conditions)})")
    return conditions, parameters


async def _require_attempt_access(
    *,
    attempt_id: UUID,
    principal: Any,
    anonymous_token: str | None,
    session: Session,
) -> dict[str, Any]:
    owner = await PracticeRepository(session).get_attempt_owner(attempt_id)
    if owner is None:
        raise ProblemException(404, "attempt_not_found", "Attempt not found")
    if owner["user_id"] is not None:
        if principal is None:
            raise ProblemException(401, "authentication_required", "Authentication required")
        if owner["user_id"] != principal.user_id:
            raise ProblemException(403, "attempt_forbidden", "Attempt belongs to another user")
    else:
        if not anonymous_token:
            raise ProblemException(
                401, "anonymous_session_required", "Anonymous session token is required"
            )
        result = await session.execute(
            text(
                """
                SELECT 1
                FROM app.anonymous_sessions
                WHERE id = :session_id
                  AND token_hash = :token_hash
                  AND expires_at > now()
                """
            ),
            {
                "session_id": owner["anonymous_session_id"],
                "token_hash": hash_secret(anonymous_token),
            },
        )
        if result.scalar_one_or_none() is None:
            raise ProblemException(403, "anonymous_session_invalid", "Invalid anonymous session")
    return dict(owner)


async def _anonymous_session_id(
    session: Session,
    anonymous_token: str | None,
) -> UUID | None:
    if not anonymous_token:
        return None
    result = await session.execute(
        text(
            """
            SELECT id
            FROM app.anonymous_sessions
            WHERE token_hash = :token_hash
              AND expires_at > now()
            """
        ),
        {"token_hash": hash_secret(anonymous_token)},
    )
    return result.scalar_one_or_none()


def _exclude_answered_questions(
    conditions: list[str],
    parameters: dict[str, Any],
    *,
    test_id: UUID,
    user_id: UUID | None,
    anonymous_session_id: UUID | None,
) -> None:
    if user_id is None and anonymous_session_id is None:
        return
    owner_condition = (
        "attempt.user_id = :history_user_id"
        if user_id is not None
        else "attempt.anonymous_session_id = :history_anonymous_session_id"
    )
    conditions.append(
        f"""
        NOT EXISTS (
            SELECT 1
            FROM app.attempt_questions AS previous_question
            JOIN app.test_attempts AS attempt
              ON attempt.id = previous_question.attempt_id
            JOIN app.attempt_answers AS previous_answer
              ON previous_answer.attempt_question_id = previous_question.id
            WHERE previous_question.question_id = question.id
              AND attempt.test_id = :history_test_id
              AND {owner_condition}
        )
        """
    )
    parameters["history_test_id"] = test_id
    if user_id is not None:
        parameters["history_user_id"] = user_id
    else:
        parameters["history_anonymous_session_id"] = anonymous_session_id


async def _create_attempt(
    *,
    session: Session,
    principal: Any,
    test_row: Any,
    questions: list[Any],
    kind: Literal["standard", "generated"],
    existing_anonymous_token: str | None = None,
    existing_anonymous_session_id: UUID | None = None,
) -> dict[str, object]:
    anonymous_token = existing_anonymous_token
    user_id = principal.user_id if principal else None
    anonymous_session_id = existing_anonymous_session_id
    if principal is None and anonymous_session_id is None:
        anonymous_token = generate_refresh_token()
        anonymous_result = await session.execute(
            text(
                """
                INSERT INTO app.anonymous_sessions (token_hash, expires_at)
                VALUES (:token_hash, :expires_at)
                RETURNING id
                """
            ),
            {
                "token_hash": hash_secret(anonymous_token),
                "expires_at": datetime.now(UTC) + timedelta(days=30),
            },
        )
        anonymous_session_id = anonymous_result.scalar_one()

    seconds = test_row["time_limit_seconds"]
    expires_at = datetime.now(UTC) + timedelta(seconds=seconds) if seconds else None
    max_score = sum(
        float(question["points"])
        for question in questions
        if question["question_type_code"] != "self_check"
    )
    attempt_result = await session.execute(
        text(
            """
            INSERT INTO app.test_attempts (
                user_id, anonymous_session_id, test_id, subject_id, section_id,
                kind, test_title_snapshot, max_score, question_count, expires_at
            )
            VALUES (
                :user_id, :anonymous_session_id, :test_id, :subject_id, :section_id,
                CAST(:kind AS app.attempt_kind), :title, :max_score,
                :question_count, :expires_at
            )
            RETURNING id
            """
        ),
        {
            "user_id": user_id,
            "anonymous_session_id": anonymous_session_id,
            "test_id": test_row["id"],
            "subject_id": test_row["subject_id"],
            "section_id": test_row["section_id"],
            "kind": kind,
            "title": test_row["title"],
            "max_score": max_score,
            "question_count": len(questions),
            "expires_at": expires_at,
        },
    )
    attempt_id = attempt_result.scalar_one()
    attempt_questions = [
        {
            "attempt_id": attempt_id,
            "question_id": question["question_id"],
            "version": question["current_version"],
            "position": position,
            "points": question["points"],
        }
        for position, question in enumerate(questions, 1)
    ]
    if attempt_questions:
        await session.execute(
            text(
                """
                INSERT INTO app.attempt_questions (
                    attempt_id, question_id, question_version, position, points_possible
                )
                VALUES (:attempt_id, :question_id, :version, :position, :points)
                """
            ),
            attempt_questions,
        )
    attempt = await PracticeRepository(session).get_attempt(attempt_id)
    await session.commit()
    return {"anonymousSessionToken": anonymous_token, "attempt": attempt}


@router.get("/tests", operation_id="listTests", include_in_schema=False)
async def list_tests(
    session: Session,
    subject: SubjectCode | None = None,
    section_id: Annotated[UUID | None, Query(alias="sectionId")] = None,
    mode: Literal["fixed", "generated"] | None = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    offset = _offset(cursor)
    items, has_more = await PracticeRepository(session).list_tests(
        subject=subject,
        section_id=section_id,
        mode=mode,
        offset=offset,
        limit=limit,
    )
    return {"items": items, "page": _page(offset, limit, has_more)}


@router.get("/tests/{test_id}", operation_id="getTest", include_in_schema=False)
async def get_test(test_id: UUID, session: Session) -> dict[str, object]:
    item = await PracticeRepository(session).get_test(test_id)
    if item is None:
        raise ProblemException(404, "test_not_found", "Test not found")
    return item


@router.post(
    "/attempts",
    status_code=status.HTTP_201_CREATED,
    operation_id="startAttempt",
    include_in_schema=False,
)
async def start_attempt(
    payload: StartAttemptRequest,
    session: Session,
    principal: OptionalPrincipal,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    del idempotency_key
    test_result = await session.execute(
        text(
            """
            SELECT test.id, test.title, test.mode::text AS mode,
                   test.subject_id, test.section_id, test.time_limit_seconds
            FROM app.tests AS test
            WHERE test.id = :test_id AND test.status = 'published'
            """
        ),
        {"test_id": payload.test_id},
    )
    test_row = test_result.mappings().one_or_none()
    if test_row is None:
        raise ProblemException(404, "test_not_found", "Test not found")
    if test_row["mode"] != "fixed":
        raise ProblemException(400, "generated_test_unsupported", "Generated tests are not enabled")
    questions_result = await session.execute(
        text(
            """
            SELECT question.id AS question_id, question.current_version,
                   question.question_type_code, tq.position,
                   COALESCE(tq.points_override, version.default_points) AS points
            FROM app.test_questions AS tq
            JOIN app.questions AS question ON question.id = tq.question_id
            JOIN app.question_versions AS version
              ON version.question_id = question.id
             AND version.version = question.current_version
            WHERE tq.test_id = :test_id
              AND question.status = 'published'
            ORDER BY tq.position
            """
        ),
        {"test_id": payload.test_id},
    )
    questions = list(questions_result.mappings())
    if not questions:
        raise ProblemException(422, "test_has_no_questions", "Test has no published questions")

    return await _create_attempt(
        session=session,
        principal=principal,
        test_row=test_row,
        questions=questions,
        kind="standard",
    )


@router.get(
    "/history-trainer/config",
    operation_id="getHistoryTrainerConfig",
    include_in_schema=False,
)
async def get_history_trainer_config(session: Session) -> dict[str, object]:
    test_result = await session.execute(
        text(
            """
            SELECT test.id, test.title
            FROM app.tests AS test
            WHERE test.slug = 'history-trainer'
              AND test.mode = 'generated'
              AND test.status = 'published'
            """
        )
    )
    test_row = test_result.mappings().one_or_none()
    if test_row is None:
        raise ProblemException(404, "history_trainer_not_found", "History trainer not found")
    task_result = await session.execute(
        text(
            """
            SELECT metadata.task_number, count(*)::integer AS question_count
            FROM app.history_question_metadata AS metadata
            JOIN app.questions AS question ON question.id = metadata.question_id
            WHERE question.status = 'published'
            GROUP BY metadata.task_number
            ORDER BY metadata.task_number
            """
        )
    )
    period_result = await session.execute(
        text(
            """
            SELECT metadata.period, count(*)::integer AS question_count
            FROM app.history_question_metadata AS metadata
            JOIN app.questions AS question ON question.id = metadata.question_id
            WHERE question.status = 'published'
              AND NULLIF(btrim(metadata.period), '') IS NOT NULL
              AND lower(btrim(metadata.period)) NOT LIKE 'вов%'
              AND lower(btrim(metadata.period)) NOT LIKE 'таблиц%'
            GROUP BY metadata.period
            ORDER BY metadata.period
            """
        )
    )
    category_result = await session.execute(
        text(
            """
            SELECT category.value AS code, count(*)::integer AS question_count
            FROM app.history_question_metadata AS metadata
            JOIN app.questions AS question ON question.id = metadata.question_id
            CROSS JOIN LATERAL jsonb_array_elements_text(metadata.categories) AS category(value)
            WHERE question.status = 'published'
            GROUP BY category.value
            ORDER BY category.value
            """
        )
    )
    tasks = [
        {"number": row["task_number"], "questionCount": row["question_count"]}
        for row in task_result.mappings()
    ]
    return {
        "testId": test_row["id"],
        "title": test_row["title"],
        "totalQuestionCount": sum(item["questionCount"] for item in tasks),
        "tasks": tasks,
        "periods": [
            {"name": row["period"], "questionCount": row["question_count"]}
            for row in period_result.mappings()
        ],
        "categories": [
            {"code": row["code"], "questionCount": row["question_count"]}
            for row in category_result.mappings()
        ],
    }


@router.post(
    "/history-trainer/count",
    operation_id="countHistoryTrainerQuestions",
    include_in_schema=False,
)
async def count_history_trainer_questions(
    payload: HistoryTrainerCountRequest,
    session: Session,
    principal: OptionalPrincipal,
    anonymous_token: Annotated[str | None, Header(alias="X-Anonymous-Session")] = None,
) -> dict[str, int]:
    test_result = await session.execute(
        text(
            """
            SELECT id
            FROM app.tests
            WHERE slug = 'history-trainer'
              AND mode = 'generated'
              AND status = 'published'
            """
        )
    )
    test_id = test_result.scalar_one_or_none()
    if test_id is None:
        raise ProblemException(404, "history_trainer_not_found", "History trainer not found")
    anonymous_session_id = (
        await _anonymous_session_id(session, anonymous_token) if principal is None else None
    )
    conditions, parameters = _history_trainer_filters(
        payload.task_numbers,
        payload.periods,
        payload.categories,
    )
    if payload.mistakes_only:
        if principal is None:
            raise ProblemException(
                401, "authentication_required", "Войдите для работы над ошибками"
            )
        conditions.append(f"question.id IN ({MISTAKES_QUERY})")
        parameters["user_id"] = principal.user_id
    elif payload.review_only:
        if principal is None:
            raise ProblemException(401,"authentication_required","Войдите для повторения темы")
        if not payload.task_numbers and not payload.periods:
            raise ProblemException(422,"review_topic_required","Выберите задание или период для повторения")
    else:
        _exclude_answered_questions(
            conditions,
            parameters,
            test_id=test_id,
            user_id=principal.user_id if principal else None,
            anonymous_session_id=anonymous_session_id,
        )
    result = await session.execute(
        text(
            f"""
            SELECT count(*)::integer
            FROM app.history_question_metadata AS metadata
            JOIN app.questions AS question ON question.id = metadata.question_id
            WHERE {' AND '.join(conditions)}
            """
        ),
        parameters,
    )
    return {"questionCount": result.scalar_one()}


@router.post(
    "/history-trainer/attempts",
    status_code=status.HTTP_201_CREATED,
    operation_id="startHistoryTrainerAttempt",
    include_in_schema=False,
)
async def start_history_trainer_attempt(
    payload: HistoryTrainerStartRequest,
    session: Session,
    principal: OptionalPrincipal,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    anonymous_token: Annotated[str | None, Header(alias="X-Anonymous-Session")] = None,
) -> dict[str, object]:
    del idempotency_key
    test_result = await session.execute(
        text(
            """
            SELECT test.id, test.title, test.subject_id, test.section_id,
                   test.time_limit_seconds
            FROM app.tests AS test
            WHERE test.slug = 'history-trainer'
              AND test.mode = 'generated'
              AND test.status = 'published'
            """
        )
    )
    test_row = test_result.mappings().one_or_none()
    if test_row is None:
        raise ProblemException(404, "history_trainer_not_found", "History trainer not found")

    anonymous_session_id = (
        await _anonymous_session_id(session, anonymous_token) if principal is None else None
    )

    conditions, parameters = _history_trainer_filters(
        payload.task_numbers,
        payload.periods,
        payload.categories,
    )
    if payload.mistakes_only:
        if principal is None:
            raise ProblemException(
                401, "authentication_required", "Войдите для работы над ошибками"
            )
        conditions.append(f"question.id IN ({MISTAKES_QUERY})")
        parameters["user_id"] = principal.user_id
    elif payload.review_only:
        if principal is None:
            raise ProblemException(401,"authentication_required","Войдите для повторения темы")
        if not payload.task_numbers and not payload.periods:
            raise ProblemException(422,"review_topic_required","Выберите задание или период для повторения")
    else:
        _exclude_answered_questions(
            conditions,
            parameters,
            test_id=test_row["id"],
            user_id=principal.user_id if principal else None,
            anonymous_session_id=anonymous_session_id,
        )
    result = await session.execute(
        text(
            f"""
            SELECT question.id AS question_id, question.current_version,
                   question.question_type_code, version.default_points AS points,
                   metadata.task_number
            FROM app.history_question_metadata AS metadata
            JOIN app.questions AS question ON question.id = metadata.question_id
            JOIN app.question_versions AS version
              ON version.question_id = question.id
             AND version.version = question.current_version
            WHERE {' AND '.join(conditions)}
            """
        ),
        parameters,
    )
    by_task: dict[int, list[Any]] = {}
    for row in result.mappings():
        by_task.setdefault(row["task_number"], []).append(row)
    if not by_task:
        raise ProblemException(
            422,
            "history_trainer_no_questions",
            "No questions match the selected filters",
        )
    randomizer = random.SystemRandom()
    for group in by_task.values():
        randomizer.shuffle(group)
    selected: list[Any] = []
    active_tasks = list(by_task)
    randomizer.shuffle(active_tasks)
    selection_limit = payload.question_count or sum(len(group) for group in by_task.values())
    while active_tasks and len(selected) < selection_limit:
        next_round: list[int] = []
        for task_number in active_tasks:
            group = by_task[task_number]
            if group and len(selected) < selection_limit:
                selected.append(group.pop())
            if group:
                next_round.append(task_number)
        active_tasks = next_round
    return await _create_attempt(
        session=session,
        principal=principal,
        test_row=test_row,
        questions=selected,
        kind="generated",
        existing_anonymous_token=anonymous_token if anonymous_session_id else None,
        existing_anonymous_session_id=anonymous_session_id,
    )


@router.get("/attempts/{attempt_id}", operation_id="getAttempt", include_in_schema=False)
async def get_attempt(
    attempt_id: UUID,
    session: Session,
    principal: OptionalPrincipal,
    anonymous_token: Annotated[str | None, Header(alias="X-Anonymous-Session")] = None,
) -> dict[str, object]:
    await _require_attempt_access(
        attempt_id=attempt_id,
        principal=principal,
        anonymous_token=anonymous_token,
        session=session,
    )
    attempt = await PracticeRepository(session).get_attempt(attempt_id)
    if attempt is None:
        raise ProblemException(404, "attempt_not_found", "Attempt not found")
    return attempt


@router.post(
    "/attempts/{attempt_id}/answers",
    operation_id="submitAttemptAnswer",
    include_in_schema=False,
)
async def submit_answer(
    attempt_id: UUID,
    payload: SubmitAnswerRequest,
    session: Session,
    principal: OptionalPrincipal,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    anonymous_token: Annotated[str | None, Header(alias="X-Anonymous-Session")] = None,
) -> dict[str, object]:
    del idempotency_key
    if payload.response is None:
        raise ProblemException(400, "empty_answer", "Answer cannot be null")
    owner = await _require_attempt_access(
        attempt_id=attempt_id,
        principal=principal,
        anonymous_token=anonymous_token,
        session=session,
    )
    if owner["status"] != "in_progress":
        raise ProblemException(409, "attempt_not_active", "Attempt is not active")
    result = await session.execute(
        text(
            """
            SELECT aq.id, aq.points_possible, question.question_type_code,
                   version.answer_spec, version.explanation,
                   attempt.expires_at,
                   answer.id AS answer_id
            FROM app.attempt_questions AS aq
            JOIN app.test_attempts AS attempt ON attempt.id = aq.attempt_id
            JOIN app.questions AS question ON question.id = aq.question_id
            JOIN app.question_versions AS version
              ON version.question_id = aq.question_id
             AND version.version = aq.question_version
            LEFT JOIN app.attempt_answers AS answer ON answer.attempt_question_id = aq.id
            WHERE aq.id = :attempt_question_id
              AND aq.attempt_id = :attempt_id
            FOR UPDATE OF attempt
            """
        ),
        {"attempt_question_id": payload.attempt_question_id, "attempt_id": attempt_id},
    )
    question = result.mappings().one_or_none()
    if question is None:
        raise ProblemException(404, "attempt_question_not_found", "Question not found")
    if question["answer_id"] is not None:
        raise ProblemException(409, "answer_already_submitted", "Answer already submitted")
    if question["expires_at"] and question["expires_at"] <= datetime.now(UTC):
        await session.execute(
            text("UPDATE app.test_attempts SET status = 'expired' WHERE id = :id"),
            {"id": attempt_id},
        )
        await session.commit()
        raise ProblemException(409, "attempt_expired", "Attempt has expired")

    grade = grade_response(
        question["question_type_code"], payload.response, question["answer_spec"]
    )
    points = (
        float(question["points_possible"]) * grade.ratio
        if grade.ratio is not None
        else None
    )
    feedback = {"correctAnswer": grade.correct_answer}
    await session.execute(
        text(
            """
            INSERT INTO app.attempt_answers (
                attempt_question_id, response, is_correct, points_awarded, feedback
            )
            VALUES (
                :question_id, CAST(:response AS jsonb), :is_correct,
                :points, CAST(:feedback AS jsonb)
            )
            """
        ),
        {
            "question_id": payload.attempt_question_id,
            "response": json.dumps(payload.response, ensure_ascii=False),
            "is_correct": grade.is_correct,
            "points": points,
            "feedback": json.dumps(feedback, ensure_ascii=False),
        },
    )
    await session.execute(
        text("UPDATE app.test_attempts SET last_activity_at = now() WHERE id = :id"),
        {"id": attempt_id},
    )
    await session.commit()
    return {
        "attemptQuestionId": payload.attempt_question_id,
        "isCorrect": grade.is_correct,
        "pointsAwarded": points,
        "pointsPossible": float(question["points_possible"]),
        "correctAnswer": grade.correct_answer,
        "explanation": question["explanation"],
        "relatedContent": [],
    }


@router.post(
    "/history-trainer/error-reports",
    operation_id="createHistoryTrainerErrorReport",
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create_history_trainer_error_report(
    payload: QuestionErrorReportCreate,
    background_tasks: BackgroundTasks,
    session: Session,
    principal: OptionalPrincipal,
    anonymous_token: Annotated[str | None, Header(alias="X-Anonymous-Session")] = None,
) -> dict[str, object]:
    message = payload.message.strip()
    if len(message) < 5:
        raise ProblemException(422, "report_message_too_short", "Опишите ошибку подробнее")
    question_result = await session.execute(
        text(
            """
            SELECT aq.attempt_id, aq.question_id, aq.question_version,
                   attempt.anonymous_session_id, answer.id AS answer_id
            FROM app.attempt_questions AS aq
            JOIN app.test_attempts AS attempt ON attempt.id=aq.attempt_id
            LEFT JOIN app.attempt_answers AS answer
              ON answer.attempt_question_id=aq.id
            WHERE aq.id=:attempt_question_id
            """
        ),
        {"attempt_question_id": payload.attempt_question_id},
    )
    question = question_result.mappings().one_or_none()
    if question is None:
        raise ProblemException(404, "attempt_question_not_found", "Question not found")
    await _require_attempt_access(
        attempt_id=question["attempt_id"],
        principal=principal,
        anonymous_token=anonymous_token,
        session=session,
    )
    if question["answer_id"] is None:
        raise ProblemException(
            409,
            "question_not_answered",
            "Сообщить об ошибке можно после отправки ответа",
        )
    report_result = await session.execute(
        text(
            """
            INSERT INTO app.question_error_reports (
                question_id,question_version,attempt_question_id,
                user_id,anonymous_session_id,message
            ) VALUES (
                :question_id,:question_version,:attempt_question_id,
                :user_id,:anonymous_session_id,:message
            )
            ON CONFLICT (attempt_question_id) DO NOTHING
            RETURNING id,status,created_at
            """
        ),
        {
            "question_id": question["question_id"],
            "question_version": question["question_version"],
            "attempt_question_id": payload.attempt_question_id,
            "user_id": principal.user_id if principal else None,
            "anonymous_session_id": (
                question["anonymous_session_id"] if principal is None else None
            ),
            "message": message,
        },
    )
    report = report_result.mappings().one_or_none()
    if report is None:
        raise ProblemException(
            409,
            "question_error_already_reported",
            "Вы уже сообщили об ошибке в этом вопросе",
        )
    await queue_admin_notification(session, report["id"])
    await session.commit()
    background_tasks.add_task(deliver_admin_notifications)
    return {
        "id": report["id"],
        "status": report["status"],
        "createdAt": report["created_at"],
    }


@router.get(
    "/history-trainer/error-reports/mine",
    operation_id="listMyHistoryTrainerErrorReports",
    include_in_schema=False,
)
async def list_my_history_trainer_error_reports(
    session: Session,
    principal: OptionalPrincipal,
    anonymous_token: Annotated[str | None, Header(alias="X-Anonymous-Session")] = None,
    mark_viewed: Annotated[bool, Query(alias="markViewed")] = True,
) -> dict[str, object]:
    parameters: dict[str, Any] = {}
    if principal is not None:
        owner_condition = "report.user_id=:user_id"
        parameters["user_id"] = principal.user_id
    elif anonymous_token:
        anonymous_result = await session.execute(
            text("SELECT id FROM app.anonymous_sessions WHERE token_hash=:token_hash"),
            {"token_hash": hash_secret(anonymous_token)},
        )
        anonymous_session_id = anonymous_result.scalar_one_or_none()
        if anonymous_session_id is None:
            return {"items": [], "unreadCount": 0}
        owner_condition = "report.anonymous_session_id=:anonymous_session_id"
        parameters["anonymous_session_id"] = anonymous_session_id
    else:
        return {"items": [], "unreadCount": 0}
    result = await session.execute(
        text(
            f"""
            SELECT report.id,report.message,report.status,report.admin_reply,
                   report.created_at,report.replied_at,report.student_viewed_at,report.workflow_status,
                   version.prompt,metadata.task_number
            FROM app.question_error_reports AS report
            LEFT JOIN app.question_versions AS version
              ON version.question_id=report.question_id
             AND version.version=report.question_version
            LEFT JOIN app.history_question_metadata AS metadata
              ON metadata.question_id=report.question_id
            WHERE {owner_condition}
            ORDER BY report.created_at DESC
            """
        ),
        parameters,
    )
    rows = list(result.mappings())
    unread_ids = [
        row["id"]
        for row in rows
        if row["admin_reply"] and row["student_viewed_at"] is None
    ]
    items = [
        {
            "id": row["id"],
            "taskNumber": row["task_number"],
            "questionPreview": (row["prompt"] or {}).get("text"),
            "message": row["message"],
            "status": row["status"],
            "adminReply": row["admin_reply"],
            "workflowStatus": row["workflow_status"],
            "createdAt": row["created_at"],
            "repliedAt": row["replied_at"],
            "unread": row["id"] in unread_ids,
        }
        for row in rows
    ]
    if unread_ids and mark_viewed:
        await session.execute(
            text(
                """
                UPDATE app.question_error_reports
                SET student_viewed_at=now(),updated_at=now()
                WHERE id = ANY(CAST(:ids AS uuid[]))
                """
            ),
            {"ids": unread_ids},
        )
        await session.commit()
    return {"items": items, "unreadCount": len(unread_ids)}


@router.post(
    "/attempts/{attempt_id}/complete",
    operation_id="completeAttempt",
    include_in_schema=False,
)
async def complete_attempt(
    attempt_id: UUID,
    session: Session,
    principal: OptionalPrincipal,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    anonymous_token: Annotated[str | None, Header(alias="X-Anonymous-Session")] = None,
) -> dict[str, object]:
    del idempotency_key
    owner = await _require_attempt_access(
        attempt_id=attempt_id,
        principal=principal,
        anonymous_token=anonymous_token,
        session=session,
    )
    if owner["status"] == "completed":
        return await _attempt_result(session, attempt_id)
    if owner["status"] != "in_progress":
        raise ProblemException(409, "attempt_not_active", "Attempt is not active")
    totals = await session.execute(
        text(
            """
            SELECT COALESCE(sum(answer.points_awarded), 0) AS score,
                   COALESCE(
                       sum(aq.points_possible)
                           FILTER (WHERE question.question_type_code <> 'self_check'),
                       0
                   ) AS max_score,
                   count(*) FILTER (WHERE answer.is_correct)::integer AS correct_count
            FROM app.attempt_questions AS aq
            JOIN app.questions AS question ON question.id = aq.question_id
            LEFT JOIN app.attempt_answers AS answer ON answer.attempt_question_id = aq.id
            WHERE aq.attempt_id = :attempt_id
            """
        ),
        {"attempt_id": attempt_id},
    )
    total = totals.mappings().one()
    await session.execute(
        text(
            """
            UPDATE app.test_attempts
            SET status = 'completed', score = :score, max_score = :max_score,
                correct_count = :correct_count, completed_at = now(),
                last_activity_at = now()
            WHERE id = :attempt_id
            """
        ),
        {"attempt_id": attempt_id, **dict(total)},
    )
    if principal is not None and owner["user_id"] == principal.user_id:
        await _save_progress(session, attempt_id, principal.user_id)
    await session.commit()
    return await _attempt_result(session, attempt_id)


async def _attempt_result(session: Session, attempt_id: UUID) -> dict[str, object]:
    attempt = await PracticeRepository(session).get_attempt(attempt_id)
    if attempt is None:
        raise ProblemException(404, "attempt_not_found", "Attempt not found")
    answers = [question["result"] for question in attempt.pop("questions") if question["result"]]
    max_score = attempt["maxScore"] or 0
    percentage = round((attempt["score"] or 0) * 100 / max_score, 2) if max_score else 0
    owner = await PracticeRepository(session).get_attempt_owner(attempt_id)
    return {
        **attempt,
        "percentage": percentage,
        "answers": answers,
        "savedToProfile": bool(owner and owner["user_id"]),
    }


async def _save_progress(session: Session, attempt_id: UUID, user_id: UUID) -> None:
    await session.execute(
        text(
            """
            INSERT INTO app.user_question_stats (
                user_id, question_id, attempt_count, correct_count, wrong_count,
                mastery_score, first_answered_at, last_answered_at, last_was_correct
            )
            SELECT :user_id, aq.question_id, 1,
                   CASE WHEN answer.is_correct THEN 1 ELSE 0 END,
                   CASE WHEN answer.is_correct THEN 0 ELSE 1 END,
                   CASE WHEN answer.is_correct THEN 1 ELSE 0 END,
                   now(), now(), answer.is_correct
            FROM app.attempt_questions AS aq
            JOIN app.attempt_answers AS answer ON answer.attempt_question_id = aq.id
            WHERE aq.attempt_id = :attempt_id AND answer.is_correct IS NOT NULL
            ON CONFLICT (user_id, question_id) DO UPDATE
            SET attempt_count = app.user_question_stats.attempt_count + 1,
                correct_count = app.user_question_stats.correct_count
                    + CASE WHEN EXCLUDED.last_was_correct THEN 1 ELSE 0 END,
                wrong_count = app.user_question_stats.wrong_count
                    + CASE WHEN EXCLUDED.last_was_correct THEN 0 ELSE 1 END,
                mastery_score = (
                    app.user_question_stats.correct_count
                    + CASE WHEN EXCLUDED.last_was_correct THEN 1 ELSE 0 END
                )::numeric / (app.user_question_stats.attempt_count + 1),
                last_answered_at = now(),
                last_was_correct = EXCLUDED.last_was_correct,
                updated_at = now()
            """
        ),
        {"attempt_id": attempt_id, "user_id": user_id},
    )
    await session.execute(
        text(
            """
            INSERT INTO app.review_queue (user_id, question_id, last_result_correct)
            SELECT :user_id, aq.question_id, false
            FROM app.attempt_questions AS aq
            JOIN app.attempt_answers AS answer ON answer.attempt_question_id = aq.id
            WHERE aq.attempt_id = :attempt_id AND answer.is_correct = false
            ON CONFLICT (user_id, question_id) DO UPDATE
            SET status = 'pending', error_count = app.review_queue.error_count + 1,
                next_review_at = now(), updated_at = now(), last_result_correct = false
            """
        ),
        {"attempt_id": attempt_id, "user_id": user_id},
    )
    await session.execute(
        text(
            """
            INSERT INTO app.user_subject_progress (
                user_id, subject_id, answered_count, correct_count,
                completed_tests, mastery_score, last_activity_at
            )
            SELECT :user_id, attempt.subject_id,
                   count(answer.id) FILTER (WHERE answer.is_correct IS NOT NULL)::integer,
                   count(*) FILTER (WHERE answer.is_correct)::integer,
                   1,
                   CASE WHEN count(answer.id)
                                  FILTER (WHERE answer.is_correct IS NOT NULL) = 0 THEN 0
                        ELSE count(*) FILTER (WHERE answer.is_correct)::numeric
                             / count(answer.id)
                                  FILTER (WHERE answer.is_correct IS NOT NULL) END,
                   now()
            FROM app.test_attempts AS attempt
            LEFT JOIN app.attempt_questions AS aq ON aq.attempt_id = attempt.id
            LEFT JOIN app.attempt_answers AS answer ON answer.attempt_question_id = aq.id
            WHERE attempt.id = :attempt_id
            GROUP BY attempt.subject_id
            ON CONFLICT (user_id, subject_id) DO UPDATE
            SET answered_count = app.user_subject_progress.answered_count
                    + EXCLUDED.answered_count,
                correct_count = app.user_subject_progress.correct_count
                    + EXCLUDED.correct_count,
                completed_tests = app.user_subject_progress.completed_tests + 1,
                mastery_score = (
                    app.user_subject_progress.correct_count + EXCLUDED.correct_count
                )::numeric / NULLIF(
                    app.user_subject_progress.answered_count + EXCLUDED.answered_count, 0
                ),
                last_activity_at = now(), updated_at = now()
            """
        ),
        {"attempt_id": attempt_id, "user_id": user_id},
    )
