# A question leaves the mistake list after its latest graded answer is correct.
MISTAKES_QUERY = """
    SELECT latest.question_id FROM (
        SELECT DISTINCT ON (aq.question_id) aq.question_id, answer.is_correct
        FROM app.attempt_questions AS aq
        JOIN app.attempt_answers AS answer ON answer.attempt_question_id=aq.id
        JOIN app.test_attempts AS attempt ON attempt.id=aq.attempt_id
        JOIN app.tests AS test ON test.id=attempt.test_id
        WHERE attempt.user_id=:user_id AND test.slug='history-trainer'
          AND answer.is_correct IS NOT NULL
        ORDER BY aq.question_id,answer.answered_at DESC,answer.id DESC
    ) AS latest WHERE latest.is_correct=false
"""
