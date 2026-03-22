import numpy as np

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


def test_local_qa_answers_basic_weather_and_humidity_questions():
    weather_answer = app.lookup_ai_crop_doctor_local_qa("weather kiya hai")
    humidity_answer = app.lookup_ai_crop_doctor_local_qa("humidity kya hoti hai")

    assert weather_answer is not None
    assert humidity_answer is not None
    assert "mausam" in weather_answer.lower() or "weather" in weather_answer.lower()
    assert "humidity" in humidity_answer.lower() or "nami" in humidity_answer.lower()


def test_local_qa_answers_rain_report_and_pest_disease_difference():
    rain_answer = app.lookup_ai_crop_doctor_local_qa("rain report kya hota hai")
    difference_answer = app.lookup_ai_crop_doctor_local_qa("pest aur disease me difference kya hai")

    assert rain_answer is not None
    assert difference_answer is not None
    assert "rain" in rain_answer.lower() or "barish" in rain_answer.lower()
    assert "pest" in difference_answer.lower() or "disease" in difference_answer.lower()


def test_local_qa_answers_temperature_and_fungicide_basics():
    temperature_answer = app.lookup_ai_crop_doctor_local_qa("temperature kya hota hai farming me")
    fungicide_answer = app.lookup_ai_crop_doctor_local_qa("fungicide kya hota hai")

    assert temperature_answer is not None
    assert fungicide_answer is not None
    assert "temperature" in temperature_answer.lower() or "tapman" in temperature_answer.lower()
    assert "fungicide" in fungicide_answer.lower() or "fungal" in fungicide_answer.lower()


def test_semantic_reranker_prefers_better_meaning_match(monkeypatch):
    vector_map = {
        ("retrieval_query", "leaf disease spray treatment"): np.array([1.0, 0.0], dtype=np.float32),
        ("retrieval_document", "crop nutrition leaf growth booster"): np.array(
            [0.15, 0.98], dtype=np.float32
        ),
        ("retrieval_document", "fungal leaf infection fungicide spray control"): np.array(
            [0.98, 0.12], dtype=np.float32
        ),
    }

    def fake_embedding(text, task_type="retrieval_document", title=""):
        key = (task_type, str(text or "").strip())
        return vector_map.get(key)

    monkeypatch.setattr(app, "is_ai_crop_doctor_semantic_search_enabled", lambda: True)
    monkeypatch.setattr(app, "get_ai_crop_doctor_semantic_shortlist_size", lambda: 4)
    monkeypatch.setattr(app, "get_ai_crop_doctor_semantic_min_score", lambda: 0.6)
    monkeypatch.setattr(app, "get_ai_crop_doctor_semantic_weight", lambda: 6.0)
    monkeypatch.setattr(app, "get_ai_crop_doctor_semantic_embedding", fake_embedding)

    candidates = [
        {
            "entry": {"question": "Best leaf nutrition spray"},
            "score": 11.2,
            "semantic_text": "crop nutrition leaf growth booster",
            "semantic_title": "Best leaf nutrition spray",
        },
        {
            "entry": {"question": "Leaf disease treatment"},
            "score": 10.9,
            "semantic_text": "fungal leaf infection fungicide spray control",
            "semantic_title": "Leaf disease treatment",
        },
    ]

    best_candidate = app.rerank_ai_crop_doctor_semantic_candidates("leaf disease spray treatment", candidates)

    assert best_candidate is not None
    assert best_candidate["entry"]["question"] == "Leaf disease treatment"
    assert best_candidate["semantic_score"] > candidates[0]["semantic_score"]
    assert best_candidate["blended_score"] > candidates[0]["blended_score"]


def test_semantic_reranker_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(app, "is_ai_crop_doctor_semantic_search_enabled", lambda: False)

    candidates = [
        {
            "entry": {"question": "Leaf disease treatment"},
            "score": 9.0,
            "semantic_text": "fungal leaf infection fungicide spray control",
            "semantic_title": "Leaf disease treatment",
        }
    ]

    best_candidate = app.rerank_ai_crop_doctor_semantic_candidates("leaf disease spray treatment", candidates)

    assert best_candidate is None
    assert candidates[0]["blended_score"] == candidates[0]["score"]
    assert candidates[0]["semantic_score"] == 0.0


def test_merge_pgvector_candidates_adds_db_backed_match():
    entry = {
        "question": "Leaf disease treatment",
        "category": "symptom",
        "keywords": ["fungicide", "leaf infection"],
        "answer": "Use crop-labelled fungicide after confirming fungal infection.",
    }
    source_key = app.build_ai_crop_doctor_local_qa_source_key(entry)
    candidates = []

    app.merge_ai_crop_doctor_pgvector_candidates(
        candidates,
        [{"source_key": source_key, "semantic_score": 0.84}],
        {source_key: entry},
        threshold_floor=4.5,
    )

    assert len(candidates) == 1
    assert candidates[0]["entry"]["question"] == "Leaf disease treatment"
    assert candidates[0]["semantic_score"] == 0.84
    assert candidates[0]["blended_score"] > candidates[0]["score"]


def test_lookup_local_qa_accepts_pgvector_backed_match(monkeypatch):
    target_entry = {
        "question": "Seed germination improve kaise kare",
        "category": "planning",
        "keywords": ["seed treatment", "germination", "vigour"],
        "answer": "Healthy treated seed aur moisture management se germination improve hoti hai.",
    }
    source_key = app.build_ai_crop_doctor_local_qa_source_key(target_entry)

    monkeypatch.setattr(app, "load_disease_symptom_rules", lambda: {})
    monkeypatch.setattr(app, "lookup_ai_crop_doctor_chat_knowledge", lambda query: None)
    monkeypatch.setattr(app, "load_ai_crop_doctor_local_qa", lambda: [target_entry])
    monkeypatch.setattr(app, "rerank_ai_crop_doctor_semantic_candidates", lambda query, candidates: None)
    monkeypatch.setattr(
        app,
        "search_ai_crop_doctor_pgvector",
        lambda source_kind, query_text, limit=5: [{"source_key": source_key, "semantic_score": 0.87}],
    )

    answer = app.lookup_ai_crop_doctor_local_qa("beej ugne ki power kaise badhaye")

    assert answer is not None
    assert "germination" in answer.lower() or "seed" in answer.lower()
