import app as app_module


class DummyUser:
    id = 7
    email = "rotation@example.com"
    location = "Nashik"
    crop_type = "Banana"


def build_weather(temp=31, humidity=68, rainfall=2.5, pressure=1007):
    return {
        "city": "Nashik",
        "temp": temp,
        "humidity": humidity,
        "rainfall_mm": rainfall,
        "wind_speed": 4.0,
        "wind_speed_kmh": 14.4,
        "wind_deg": 90,
        "clouds": 30,
        "pressure": pressure,
        "lat": 19.99,
        "lon": 73.79,
        "updated_at": "10:00 AM",
        "chart": [],
        "chart_polyline": "",
        "slider_percent": 42,
        "feels_like": temp + 1,
        "description": "clear sky",
        "icon_code": "01d",
        "icon_url": "https://example.com/icon.png",
    }


def test_resolve_switch_crop_entry_matches_fuzzy_name():
    entry = app_module.resolve_switch_crop_entry("bananna")

    assert entry is not None
    assert entry["name"] == "Banana"


def test_resolve_switch_crop_entry_matches_rrice_to_rice():
    entry = app_module.resolve_switch_crop_entry("rrice")

    assert entry is not None
    assert entry["name"] == "Rice"


def test_recommend_switch_crops_for_banana_prefers_recovery_friendly_options(monkeypatch):
    previous_crop = app_module.resolve_switch_crop_entry("Banana")
    monkeypatch.setattr(app_module, "search_ai_crop_doctor_pgvector", lambda source_kind, query_text, limit=5: [])

    result = app_module.recommend_switch_crops(previous_crop, DummyUser(), build_weather())

    assert result is not None
    recommended_names = [item["name"] for item in result["recommended_crops"]]
    assert len(recommended_names) <= 3
    assert any(name in recommended_names for name in ["Millet", "Mango", "Guava", "Sorghum"])
    assert result["impact_analysis"][0]["title"] == "Nitrogen draw"
    assert "soil estimator" in result["summary"]["description"].lower()


def test_build_switch_crop_page_context_warns_when_crop_missing(monkeypatch):
    monkeypatch.setattr(app_module, "fetch_weather_bundle", lambda location: build_weather())

    page = app_module.build_switch_crop_page_context(DummyUser(), "Unknown Crop")

    assert page["warning"] is not None
    assert page["rotation_plan"] is None
    assert page["crop_options"]


def test_build_switch_crop_page_context_starts_blank_without_preselected_crop(monkeypatch):
    class WheatUser(DummyUser):
        crop_type = "Wheat"

    monkeypatch.setattr(app_module, "fetch_weather_bundle", lambda location: build_weather())

    page = app_module.build_switch_crop_page_context(WheatUser())

    assert page["selected_crop_name"] == ""
    assert "Wheat" in page["crop_options"]
    assert "Rice" in page["crop_options"]
    assert page["warning"] is None
    assert page["rotation_plan"] is None


def test_detect_switch_crop_context_uses_meaningful_season_and_ranked_climate(monkeypatch):
    class FakeDateTime:
        @classmethod
        def now(cls):
            class _Now:
                month = 3
            return _Now()

    monkeypatch.setattr(app_module, "datetime", FakeDateTime)

    context = app_module.detect_switch_crop_context(build_weather(temp=22, humidity=44, rainfall=1, pressure=1011))

    assert context["season"] == "Rabi"
    assert context["climate_display"]
    assert context["climate_tags"]
    assert "All" not in context["climate_display"]


def test_recommend_switch_crops_changes_with_weather_context(monkeypatch):
    previous_crop = app_module.resolve_switch_crop_entry("Banana")
    monkeypatch.setattr(app_module, "search_ai_crop_doctor_pgvector", lambda source_kind, query_text, limit=5: [])

    dry_weather = build_weather(temp=22, humidity=40, rainfall=1, pressure=1011)
    rainy_weather = build_weather(temp=31, humidity=86, rainfall=12, pressure=1004)

    dry_result = app_module.recommend_switch_crops(previous_crop, DummyUser(), dry_weather)
    rainy_result = app_module.recommend_switch_crops(previous_crop, DummyUser(), rainy_weather)

    assert dry_result is not None
    assert rainy_result is not None
    assert dry_result["summary"]["chips"][0]["value"] != rainy_result["summary"]["chips"][0]["value"]
    assert dry_result["recommended_crops"] != rainy_result["recommended_crops"]


def test_build_agricultural_risk_module_detects_storm_and_fungal_risks():
    weather = build_weather(temp=29, humidity=86, rainfall=6, pressure=1009)
    weather["wind_speed_kmh"] = 33
    weather["wind_speed"] = round(weather["wind_speed_kmh"] / 3.6, 1)

    forecast_payload = {
        "list": [
            {
                "main": {"temp": 26, "temp_min": 25, "temp_max": 27, "pressure": 1004, "humidity": 88},
                "wind": {"speed": 9.5},
                "rain": {"3h": 12},
            },
            {
                "main": {"temp": 24, "temp_min": 23, "temp_max": 25, "pressure": 1001, "humidity": 90},
                "wind": {"speed": 11.0},
                "rain": {"3h": 18},
            },
            {
                "main": {"temp": 28, "temp_min": 27, "temp_max": 29, "pressure": 998, "humidity": 86},
                "wind": {"speed": 12.2},
                "rain": {"3h": 8},
            },
        ]
    }
    onecall_payload = {
        "daily": [
            {"temp": {"min": 24, "max": 30}, "rain": 22},
            {"temp": {"min": 25, "max": 31}, "rain": 12},
            {"temp": {"min": 26, "max": 32}, "rain": 4},
        ]
    }

    module = app_module.build_agricultural_risk_module(
        "Nashik",
        weather=weather,
        forecast_payload=forecast_payload,
        onecall_payload=onecall_payload,
    )

    risk_types = [item["type"] for item in module["json"]["risks"]]
    assert "Thunderstorm" in risk_types
    assert "Fungal Risk" in risk_types
    assert module["summary"]
    assert len(module["trend"]) == 3
    assert module["assessments"]
    assert "Climate Risk Prediction (Next 3-7 Days)" in module["farmer_report"]
    assert "Recommended Farmer Actions:" in module["farmer_report"]


def test_build_agricultural_risk_module_detects_drought_and_heatwave():
    weather = build_weather(temp=39, humidity=28, rainfall=0, pressure=1006)
    weather["wind_speed_kmh"] = 18
    weather["wind_speed"] = round(weather["wind_speed_kmh"] / 3.6, 1)

    forecast_payload = {
        "list": [
            {
                "main": {"temp": 40, "temp_min": 38, "temp_max": 41, "pressure": 1005, "humidity": 26},
                "wind": {"speed": 5.0},
                "rain": {"3h": 0},
            },
            {
                "main": {"temp": 41, "temp_min": 39, "temp_max": 42, "pressure": 1004, "humidity": 24},
                "wind": {"speed": 4.0},
                "rain": {"3h": 0},
            },
        ]
    }
    onecall_payload = {
        "daily": [
            {"temp": {"min": 29, "max": 40}, "rain": 0},
            {"temp": {"min": 30, "max": 41}, "rain": 0},
            {"temp": {"min": 31, "max": 42}, "rain": 0},
        ]
    }

    module = app_module.build_agricultural_risk_module(
        "Nashik",
        weather=weather,
        forecast_payload=forecast_payload,
        onecall_payload=onecall_payload,
    )

    risk_types = [item["type"] for item in module["json"]["risks"]]
    assert "Drought" in risk_types
    assert "Heatwave" in risk_types
    assert module["cards"][0]["severity"] in {"High", "Medium"}


def test_build_agricultural_risk_module_includes_conservative_low_risk_assessments():
    weather = build_weather(temp=30, humidity=82, rainfall=4, pressure=1008)
    weather["wind_speed_kmh"] = 14
    weather["wind_speed"] = round(weather["wind_speed_kmh"] / 3.6, 1)

    forecast_payload = {
        "list": [
            {
                "main": {"temp": 31, "temp_min": 28, "temp_max": 32, "pressure": 1008, "humidity": 83},
                "wind": {"speed": 4.0},
                "rain": {"3h": 2},
            },
            {
                "main": {"temp": 30, "temp_min": 27, "temp_max": 31, "pressure": 1007, "humidity": 80},
                "wind": {"speed": 3.8},
                "rain": {"3h": 1},
            },
        ]
    }
    onecall_payload = {
        "daily": [
            {"temp": {"min": 25, "max": 31}, "rain": 4},
            {"temp": {"min": 24, "max": 30}, "rain": 3},
            {"temp": {"min": 24, "max": 30}, "rain": 2},
        ]
    }

    module = app_module.build_agricultural_risk_module(
        "Puri",
        weather=weather,
        forecast_payload=forecast_payload,
        onecall_payload=onecall_payload,
    )

    assessments = {item["type"]: item for item in module["assessments"]}
    assert set(assessments) >= {"Drought", "Thunderstorm", "Heatwave", "Cold Wave"}
    assert assessments["Drought"]["severity"] in {"Low", "Moderate"}
    assert "Humidity is high" in " ".join(assessments["Drought"]["reason"])
    assert module["json"]["forecast_text"]
    assert len(module["json"]["recommended_actions"]) == 3


def test_build_agricultural_risk_module_preserves_requested_location():
    weather = build_weather(temp=31, humidity=60, rainfall=1, pressure=1008)
    weather["city"] = "Cuttack"

    module = app_module.build_agricultural_risk_module("Bhubaneswar", weather=weather)

    assert module["location"] == "Bhubaneswar"
    assert module["json"]["location"] == "Bhubaneswar"
    assert module["matched_location"] == "Cuttack"
    assert module["data_location"] == "Cuttack"


def test_switch_crop_pgvector_documents_include_crop_profiles():
    documents = app_module.build_switch_crop_pgvector_documents()

    assert documents
    assert any(item["source_kind"] == "switch_crop" for item in documents)
    assert any("Banana" in item["title"] for item in documents)
