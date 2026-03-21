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
