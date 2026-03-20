import app as app_module


class DummyUser:
    crop_type = "Rice"
    location = "Bhubaneswar"


def test_lookup_kisan_dost_knowledge_does_not_match_crop_only_for_greeting():
    reply = app_module.lookup_kisan_dost_knowledge("Hii", "Rice")

    assert reply is None


def test_build_ai_chat_context_query_does_not_merge_history_for_greeting():
    history = [{"role": "user", "content": "paddy me kitna pani dena chahiye"}]

    context_query = app_module.build_ai_chat_context_query("Hii", history)

    assert context_query == "Hii"


def test_build_ai_chat_context_query_does_not_merge_non_followup_short_query():
    history = [{"role": "user", "content": "White powder leaf pe hai"}]

    context_query = app_module.build_ai_chat_context_query("Leaf me holes hai", history)

    assert context_query == "Leaf me holes hai"


def test_disease_dataset_lookup_matches_strong_powdery_mildew_symptoms():
    reply = app_module.lookup_ai_crop_doctor_disease_dataset_answer(
        "leaf par white powder layer hai aur leaf curl ke saath slow growth bhi hai"
    )

    assert reply is not None
    assert "Powdery Mildew" in reply
    assert "Sulfur" in reply or "Neem Oil" in reply


def test_lookup_ai_crop_doctor_local_qa_matches_greeting_variant():
    reply = app_module.lookup_ai_crop_doctor_local_qa("hii")

    assert reply is not None
    assert "AI Crop Doctor" in reply


def test_lookup_ai_crop_doctor_chat_knowledge_matches_direct_tag_name():
    reply = app_module.lookup_ai_crop_doctor_local_qa("bacterial_spot")

    assert reply is not None
    assert "Bacterial Spot" in reply


def test_lookup_ai_crop_doctor_local_qa_matches_seed_definition_typo():
    reply = app_module.lookup_ai_crop_doctor_local_qa("beej kiya hai")

    assert reply is not None
    assert "Beej" in reply or "starting planting material" in reply


def test_lookup_ai_crop_doctor_local_qa_matches_fertilizer_definition_typo():
    reply = app_module.lookup_ai_crop_doctor_local_qa("fertilizer kiya hai")

    assert reply is not None
    assert "Fertilizer" in reply or "khad" in reply or "nutrients" in reply


def test_lookup_ai_crop_doctor_local_qa_matches_irrigation_definition_typo():
    reply = app_module.lookup_ai_crop_doctor_local_qa("irrigasion kiya hai")

    assert reply is not None
    assert "Irrigation" in reply or "sinchai" in reply or "paani" in reply


def test_resolve_ai_chat_response_prefers_groq_before_local(monkeypatch):
    monkeypatch.setattr(app_module, "ask_groq_ai_crop_doctor", lambda user, query, history: "Groq answer")
    monkeypatch.setattr(app_module, "lookup_ai_crop_doctor_local_qa", lambda query: "Local answer")

    result = app_module.resolve_ai_chat_response(DummyUser(), "anything", [])

    assert result["provider"] == "groq"
    assert result["response"] == "Groq answer"


def test_resolve_ai_chat_response_uses_local_when_groq_missing(monkeypatch):
    monkeypatch.setattr(app_module, "ask_groq_ai_crop_doctor", lambda user, query, history: None)
    monkeypatch.setattr(app_module, "lookup_ai_crop_doctor_local_qa", lambda query: "Local answer")

    result = app_module.resolve_ai_chat_response(DummyUser(), "anything", [])

    assert result["provider"] == "local_knowledge"
    assert result["response"] == "Local answer"


def test_resolve_ai_chat_response_uses_simple_fallback_last(monkeypatch):
    monkeypatch.setattr(app_module, "ask_groq_ai_crop_doctor", lambda user, query, history: None)
    monkeypatch.setattr(app_module, "lookup_ai_crop_doctor_local_qa", lambda query: None)

    result = app_module.resolve_ai_chat_response(DummyUser(), "unknown thing", [])

    assert result["provider"] == "fallback"
    assert "Sorry" in result["response"]
