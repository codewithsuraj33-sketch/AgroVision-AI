import app as app_module


class DummyUser:
    id = 1
    email = "farmer@example.com"
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


def test_lookup_ai_crop_doctor_chat_knowledge_matches_typoed_tag_name():
    reply = app_module.lookup_ai_crop_doctor_local_qa("bactarial_spot")

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


def test_extract_ai_chat_weather_location_uses_query_location():
    location = app_module.extract_ai_chat_weather_location("Mumbai ka weather report kya hai", "Bhubaneswar")

    assert location == "Mumbai"


def test_disease_dataset_lookup_matches_typoed_powdery_mildew_query():
    reply = app_module.lookup_ai_crop_doctor_disease_dataset_answer(
        "leaf par powdary mildew jaisa white powder hai aur slo growth ho raha hai"
    )

    assert reply is not None
    assert "Powdery Mildew" in reply
    assert "Sulfur" in reply or "Neem Oil" in reply


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


def test_resolve_ai_chat_response_uses_contextual_assistant_for_weather(monkeypatch):
    monkeypatch.setattr(app_module, "ask_groq_ai_crop_doctor", lambda user, query, history: "Groq answer")
    monkeypatch.setattr(app_module, "lookup_ai_crop_doctor_local_qa", lambda query: None)
    monkeypatch.setattr(
        app_module,
        "fetch_weather_bundle",
        lambda location: {
            "city": location,
            "temp": 29,
            "feels_like": 31,
            "humidity": 78,
            "rainfall_mm": 6,
            "wind_speed": 4.2,
            "wind_speed_kmh": 15.1,
            "clouds": 64,
            "pressure": 1008,
            "lat": None,
            "lon": None,
            "updated_at": "09:30 AM",
            "chart": [],
            "chart_polyline": "",
            "slider_percent": 58,
            "wind_deg": 110,
            "description": "light rain",
            "icon_code": "10d",
            "icon_url": "https://example.com/icon.png",
        },
    )

    result = app_module.resolve_ai_chat_response(DummyUser(), "Delhi ka weather report kya hai", [])

    assert result["provider"] == "contextual_assistant"
    assert "weather update" in result["response"].lower() or "temperature" in result["response"].lower()
    assert "humidity" in result["response"].lower()
    assert "Delhi" in result["response"]


def test_resolve_ai_chat_response_uses_simple_fallback_last(monkeypatch):
    monkeypatch.setattr(app_module, "ask_groq_ai_crop_doctor", lambda user, query, history: None)
    monkeypatch.setattr(app_module, "lookup_ai_crop_doctor_local_qa", lambda query: None)

    result = app_module.resolve_ai_chat_response(DummyUser(), "unknown thing", [])

    assert result["provider"] == "fallback"
    assert "Sorry" in result["response"]
