import app


def test_local_qa_returns_different_answers_for_different_symptoms():
    yellow_answer = app.lookup_ai_crop_doctor_local_qa("Mere patte pile ho rahe hain")
    brown_answer = app.lookup_ai_crop_doctor_local_qa("Patte par bhure daag aa gaye hain")

    assert yellow_answer is not None
    assert brown_answer is not None
    assert yellow_answer != brown_answer
    assert "nitrogen" in yellow_answer.lower() or "micronutrient" in yellow_answer.lower()
    assert "fung" in brown_answer.lower() or "blight" in brown_answer.lower()


def test_local_qa_handles_synonym_based_symptom_queries():
    answer = app.lookup_ai_crop_doctor_local_qa("Jad kaali ho rahi hai aur plant murjha raha hai")

    assert answer is not None
    assert "root rot" in answer.lower() or "drainage" in answer.lower()


def test_local_qa_does_not_capture_project_faq_questions():
    answer = app.lookup_ai_crop_doctor_local_qa("What is climate adaptation in agriculture?")

    assert answer is None
