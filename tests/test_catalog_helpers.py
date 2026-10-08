from uuid import uuid4

from app.repositories.catalog import CatalogRepository, build_section_tree


def test_build_section_tree_nests_children() -> None:
    root_id = uuid4()
    child_id = uuid4()
    rows = [
        {
            "id": root_id,
            "subject": "history",
            "parent_id": None,
            "code": "xix",
            "title": "XIX век",
            "description": None,
            "sort_order": 1,
        },
        {
            "id": child_id,
            "subject": "history",
            "parent_id": root_id,
            "code": "reforms",
            "title": "Реформы",
            "description": None,
            "sort_order": 1,
        },
    ]

    result = build_section_tree(rows)

    assert [item["id"] for item in result] == [root_id]
    assert [item["id"] for item in result[0]["children"]] == [child_id]


def test_plan_tree_nests_children() -> None:
    root_id = uuid4()
    child_id = uuid4()
    rows = [
        {"id": root_id, "parent_id": None, "item_text": "Пункт", "sort_order": 1},
        {"id": child_id, "parent_id": root_id, "item_text": "Подпункт", "sort_order": 1},
    ]

    result = CatalogRepository._plan_tree(rows)

    assert result[0]["text"] == "Пункт"
    assert result[0]["children"][0]["text"] == "Подпункт"


def test_ruler_search_keeps_exact_roman_ordinal_and_accepts_cases() -> None:
    import re

    from app.repositories.catalog import ruler_search_pattern

    pattern = ruler_search_pattern("Петра I")
    assert pattern is not None
    python_pattern = pattern.replace(r"\m", r"\b").replace(r"\M", r"\b")
    for text in ("петр i", "реформы петра i", "при петре i", "петром i проведено"):
        assert re.search(python_pattern, text), text
    for text in ("петр ii", "петр iii", "александр i", "петр ii и иван i"):
        assert not re.search(python_pattern, text), text
    assert re.search(python_pattern, "петр ii продолжил реформы петра i")
    assert ruler_search_pattern("крещение руси") is None


def test_ruler_search_handles_other_rulers() -> None:
    import re

    from app.repositories.catalog import ruler_search_pattern

    for query, correct, wrong in (
        ("Екатерина II", "екатериной ii", "екатерина i"),
        ("Павел I", "павла i", "павел ii"),
        ("Александр III", "александра iii", "александр ii"),
    ):
        pattern = ruler_search_pattern(query).replace(r"\m", r"\b").replace(r"\M", r"\b")
        assert re.search(pattern, correct)
        assert not re.search(pattern, wrong)


def test_sources_and_required_plan_points_are_preserved():
    from app.repositories.catalog import content_sources

    assert content_sources({"legacy_sources": "*/ А 2024*"}) == ["*/ А 2024*"]
    assert content_sources({"feature_metadata": [{"source_ref": "А 2024"}]}) == ["А 2024"]
    root, child = uuid4(), uuid4()
    rows = [
        {"id": root, "parent_id": None, "item_text": "Пункт", "sort_order": 2},
        {"id": child, "parent_id": root, "item_text": "Подпункт", "sort_order": 1},
    ]
    tree = CatalogRepository._plan_tree(
        rows, [{"number": 2, "is_required": True, "source_ref": "А 2024"}]
    )
    assert tree[0]["required"] is True
    assert tree[0]["source"] == "А 2024"
    assert tree[0]["children"][0]["required"] is False
