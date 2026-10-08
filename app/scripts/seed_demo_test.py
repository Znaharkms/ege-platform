from __future__ import annotations

import argparse
import json
from uuid import uuid4

from sqlalchemy import text

from app.core.config import get_settings
from app.importing.sqlite_content import create_sync_engine

QUESTIONS = [
    {
        "type": "single_choice",
        "prompt": {
            "text": "Какой фактор производства обозначает предпринимательские способности?",
            "options": [
                {"id": "a", "text": "Земля"},
                {"id": "b", "text": "Предпринимательство"},
                {"id": "c", "text": "Капитал"},
            ],
        },
        "answer": {"correctOptionId": "b"},
        "explanation": {"text": "Предпринимательство выделяется как особый фактор производства."},
    },
    {
        "type": "multiple_choice",
        "prompt": {
            "text": "Выберите признаки рыночной экономики.",
            "options": [
                {"id": "a", "text": "Свобода предпринимательства"},
                {"id": "b", "text": "Централизованное планирование"},
                {"id": "c", "text": "Конкуренция"},
            ],
        },
        "answer": {"correctOptionIds": ["a", "c"]},
        "explanation": {"text": "Рынок основан на свободе предпринимательства и конкуренции."},
    },
    {
        "type": "text",
        "prompt": {"text": "Как называется устойчивый рост общего уровня цен?"},
        "answer": {"acceptedAnswers": ["инфляция"]},
        "explanation": {"text": "Устойчивый рост общего уровня цен называется инфляцией."},
    },
]


def seed(admin_email: str) -> None:
    engine = create_sync_engine(get_settings().sync_database_url)
    try:
        with engine.begin() as connection:
            admin_id = connection.execute(
                text(
                    """
                    SELECT id FROM app.users
                    WHERE lower(email) = lower(:email)
                      AND role = 'admin' AND status = 'active'
                    """
                ),
                {"email": admin_email},
            ).scalar_one_or_none()
            if admin_id is None:
                raise SystemExit(f"Администратор {admin_email!r} не найден")
            existing = connection.execute(
                text(
                    """
                    SELECT test.id
                    FROM app.tests AS test
                    JOIN app.subjects AS subject ON subject.id = test.subject_id
                    WHERE subject.code = 'society' AND test.slug = 'demo-economy'
                    """
                )
            ).scalar_one_or_none()
            if existing:
                print(f"Демонстрационный тест уже существует: {existing}")
                return
            subject_id = connection.execute(
                text("SELECT id FROM app.subjects WHERE code = 'society'")
            ).scalar_one()
            section_id = connection.execute(
                text(
                    """
                    INSERT INTO app.sections (subject_id, code, title, sort_order)
                    VALUES (:subject_id, 'economy', 'Экономика', 30)
                    ON CONFLICT (subject_id, code) DO UPDATE SET is_active = true
                    RETURNING id
                    """
                ),
                {"subject_id": subject_id},
            ).scalar_one()

            question_ids = []
            for item in QUESTIONS:
                question_id = uuid4()
                question_ids.append(question_id)
                connection.execute(
                    text(
                        """
                        INSERT INTO app.questions (
                            id, subject_id, section_id, question_type_code,
                            status, created_by, updated_by, published_at
                        )
                        VALUES (
                            :id, :subject_id, :section_id, :type,
                            'published', :admin_id, :admin_id, now()
                        )
                        """
                    ),
                    {
                        "id": question_id,
                        "subject_id": subject_id,
                        "section_id": section_id,
                        "type": item["type"],
                        "admin_id": admin_id,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO app.question_versions (
                            question_id, version, prompt, answer_spec,
                            explanation, default_points, created_by
                        )
                        VALUES (
                            :question_id, 1, CAST(:prompt AS jsonb),
                            CAST(:answer AS jsonb), CAST(:explanation AS jsonb),
                            1, :admin_id
                        )
                        """
                    ),
                    {
                        "question_id": question_id,
                        "prompt": json.dumps(item["prompt"], ensure_ascii=False),
                        "answer": json.dumps(item["answer"], ensure_ascii=False),
                        "explanation": json.dumps(item["explanation"], ensure_ascii=False),
                        "admin_id": admin_id,
                    },
                )

            test_id = connection.execute(
                text(
                    """
                    INSERT INTO app.tests (
                        subject_id, section_id, title, slug, description,
                        status, created_by, updated_by, published_at
                    )
                    VALUES (
                        :subject_id, :section_id, 'Демонстрационный тест: Экономика',
                        'demo-economy', 'Три вопроса для проверки механизма тестирования',
                        'published', :admin_id, :admin_id, now()
                    )
                    RETURNING id
                    """
                ),
                {
                    "subject_id": subject_id,
                    "section_id": section_id,
                    "admin_id": admin_id,
                },
            ).scalar_one()
            for position, question_id in enumerate(question_ids, start=1):
                connection.execute(
                    text(
                        """
                        INSERT INTO app.test_questions (test_id, question_id, position)
                        VALUES (:test_id, :question_id, :position)
                        """
                    ),
                    {"test_id": test_id, "question_id": question_id, "position": position},
                )
            print(f"Создан демонстрационный тест: {test_id}")
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Создать демонстрационный тест")
    parser.add_argument("--admin-email", required=True)
    args = parser.parse_args()
    seed(args.admin_email)


if __name__ == "__main__":
    main()
