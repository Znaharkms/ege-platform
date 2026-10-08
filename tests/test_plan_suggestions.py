from app.repositories.catalog import CatalogRepository


async def test_plan_suggestions_search_titles_only_and_escape_wildcards():
    class Result:
        def scalar_one(self):
            return "рынок %_"

        def mappings(self):
            return []

    class Session:
        def __init__(self):
            self.calls = []

        async def execute(self, statement, parameters):
            self.calls.append((str(statement), parameters))
            return Result()

    session = Session()
    assert await CatalogRepository(session).suggest_plans("рынок %_", 8) == []
    sql, params = session.calls[-1]
    assert "subj.code = 'society'" in sql
    assert "ci.content_type_code = 'plan'" in sql
    assert "ci.status = 'published'" in sql
    assert "sd.body_text" not in sql
    assert "WHEN app.normalize_search_text(ci.title) = :exact THEN 0" in sql
    assert params["token_1"] == r"%\%\_%"
    assert params["limit"] == 8


async def test_global_suggestions_include_all_reference_types_without_fuzzy_noise():
    class Result:
        def scalar_one(self):
            return "петр i"

        def mappings(self):
            return []

    class Session:
        def __init__(self):
            self.calls = []

        async def execute(self, statement, parameters):
            self.calls.append((str(statement), parameters))
            return Result()

    session = Session()
    assert await CatalogRepository(session).suggest_content("Пётр I", 8) == []
    sql, params = session.calls[-1]
    assert params["allow_fuzzy"] is False
    assert {params[f"content_type_{i}"] for i in range(3)} == {"date", "term", "plan"}
    assert "subject" not in params
    assert "sd.normalized_text ~ :ruler_pattern" in sql
    assert params["ruler_pattern"].endswith(r"i\M")
    assert "LIKE :title_prefix" in sql


async def test_suggestions_scope_subject_and_terms_do_not_search_definitions():
    class Result:
        def scalar_one(self):
            return "политика"

        def mappings(self):
            return []

    class Session:
        def __init__(self):
            self.calls = []

        async def execute(self, statement, parameters):
            self.calls.append((str(statement), parameters))
            return Result()

    session = Session()
    await CatalogRepository(session).suggest_content("политика", subject="society")
    sql, params = session.calls[-1]
    assert params["subject"] == "society"
    assert "ci.content_type_code = 'term' AND" in sql
    term_branch = sql.split("ci.content_type_code = 'term' AND", 1)[1].split(
        "ci.content_type_code <> 'term'", 1
    )[0]
    assert "app.normalize_search_text(ci.title) LIKE :contains_query" in term_branch
    assert "to_tsvector('pg_catalog.russian', ci.title)" in term_branch
    assert "sd.normalized_text" not in term_branch
    assert "sd.search_vector" not in term_branch
