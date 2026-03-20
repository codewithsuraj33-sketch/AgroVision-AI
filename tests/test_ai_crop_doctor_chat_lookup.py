import app as app_module


def test_structured_chat_knowledge_matches_non_exact_powder_query():
    reply = app_module.lookup_ai_crop_doctor_local_qa(
        "bhai mere paudhe pe safed safed powder aa gaya hai kya karu"
    )

    assert reply is not None
    assert "Powdery Mildew" in reply or "Sulfur" in reply or "Milk spray" in reply


def test_structured_chat_knowledge_matches_judge_style_question():
    reply = app_module.lookup_ai_crop_doctor_local_qa("tumhara system dusre farming apps se alag kaise hai")

    assert reply is not None
    assert (
        "image diagnosis" in reply.lower()
        or "symptom-based chat doctor" in reply.lower()
        or "platform" in reply.lower()
    )


def test_structured_chat_knowledge_matches_hindi_script_yellowing_query():
    reply = app_module.lookup_ai_crop_doctor_local_qa("मेरे पौधे पीले पड़ रहे हैं")

    assert reply is not None
    assert "Nutrient Deficiency" in reply or "NPK" in reply or "Yellow leaves" in reply
