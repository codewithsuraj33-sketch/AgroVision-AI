import json
from pathlib import Path

# Detailed Disease Knowledge Base for AgroVision AI
# Maps PlantVillage model classes to farmer insights
# Classes from models/crop_disease_labels.json (16 classes)

DISEASE_KNOWLEDGE = {
    # Rice analogs
    "Potato___Early_blight": {
        "disease": "Early Blight",
        "confidence": None,
        "cause": "Fungal infection (Alternaria) from humid weather & wet leaves",
        "symptoms": "Concentric brown spots on older leaves, yellowing around lesions, and faster spread after leaf wetness.",
        "recommendation": "Remove lower infected leaves, avoid wet foliage, and start protective spray quickly.",
        "organic_solution": "Use neem-based bio-fungicidal spray with compost support and keep airflow open.",
        "solution": "Apply a protective fungicide like mancozeb-based spray at the recommended interval.",
        "best_product": "Bio Pesticide",
        "product_link": "/market?recommended=bio-pesticide",
    },
    "Potato___Late_blight": {
        "disease": "Late Blight",
        "confidence": None,
        "cause": "Phytophthora infestans fungus thrives in cool humid conditions",
        "symptoms": "Water-soaked dark lesions, rapid leaf collapse, and white fungal growth under humid conditions.",
        "recommendation": "Start blight control immediately and separate badly infected foliage from the field.",
        "organic_solution": "Improve drainage, prune infected leaves, and use a bio-protective spray in the evening.",
        "solution": "Apply a systemic anti-blight fungicide as per label schedule, especially after cool wet weather.",
        "best_product": "Pesticide Spray",
        "product_link": "/market?recommended=pesticide-spray",
    },
    # Rust analogs
    "Tomato_Septoria_leaf_spot": {
        "disease": "Rust / Leaf Spot",
        "confidence": None,
        "cause": "Septoria fungi from wet foliage & poor air circulation",
        "symptoms": "Small circular leaf spots with gray centers and yellow halo on older leaves.",
        "recommendation": "Remove infected leaves, open canopy airflow, and continue preventive spray rotation.",
        "organic_solution": "Use neem-based or bio-fungicidal support and avoid splashing soil onto leaves.",
        "solution": "Apply a preventive fungicide on schedule and repeat after rainy spells if spread continues.",
        "best_product": "Bio Pesticide",
        "product_link": "/market?recommended=bio-pesticide",
    },
    # Bacterial spots
    "Pepper__bell___Bacterial_spot": {
        "disease": "Bacterial Spot",
        "confidence": None,
        "cause": "Xanthomonas bacteria spreads via rain splash",
        "symptoms": "Small water-soaked spots that turn dark and rough on leaves and young fruit.",
        "recommendation": "Avoid overhead irrigation, sanitize tools, and spray early before lesions spread.",
        "organic_solution": "Use copper-compatible bio support with strict field hygiene and dry-canopy management.",
        "solution": "Apply a bactericide or copper-based crop protection spray as advised on the label.",
        "best_product": "Pesticide Spray",
        "product_link": "/market?recommended=pesticide-spray",
    },
    # Healthy classes
    "Pepper__bell___healthy": {
        "disease": "Healthy",
        "confidence": None,
        "cause": "No disease",
        "symptoms": "Leaf surface looks uniform and healthy.",
        "recommendation": "Continue monitoring and keep balanced irrigation.",
        "organic_solution": "Maintain compost, airflow, and preventive scouting.",
        "solution": "Routine care",
        "best_product": "N/A",
        "product_link": "",
    },
    "Potato___healthy": {
        "disease": "Healthy",
        "confidence": None,
        "cause": "No disease",
        "symptoms": "Leaf surface looks uniform and healthy.",
        "recommendation": "Continue monitoring and keep balanced irrigation.",
        "organic_solution": "Maintain compost, airflow, and preventive scouting.",
        "solution": "Routine care",
        "best_product": "N/A",
        "product_link": "",
    },
    "Tomato_healthy": {
        "disease": "Healthy",
        "confidence": None,
        "cause": "No disease",
        "symptoms": "Leaf surface looks uniform and healthy.",
        "recommendation": "Continue monitoring and keep balanced irrigation.",
        "organic_solution": "Maintain compost, airflow, and preventive scouting.",
        "solution": "Routine care",
        "best_product": "N/A",
        "product_link": "",
    },
    # Tomato diseases
    "Tomato_Early_blight": {
        "disease": "Early Blight",
        "confidence": None,
        "cause": "Alternaria solani fungus on stressed plants",
        "symptoms": "Brown concentric lesions on older leaves, lower canopy infection, and yellowing around spots.",
        "recommendation": "Mulch soil, remove lower infected leaves, and support the plant before spread increases.",
        "organic_solution": "Apply bio-fungicidal support with neem blend and keep leaf surface dry after irrigation.",
        "solution": "Spray a labeled protective fungicide and repeat based on weather pressure.",
        "best_product": "Bio Pesticide",
        "product_link": "/market?recommended=bio-pesticide",
    },
    "Tomato_Late_blight": {
        "disease": "Late Blight",
        "confidence": None,
        "cause": "Phytophthora infestans (cool wet weather)",
        "symptoms": "Dark oily lesions, rapid tissue collapse, and spread after cool wet nights.",
        "recommendation": "Space plants, prune infected leaves, and act immediately after humid weather.",
        "organic_solution": "Remove infected parts fast, reduce leaf wetness, and support with a bio-protective spray.",
        "solution": "Use a strong anti-blight spray program as per label recommendation.",
        "best_product": "Pesticide Spray",
        "product_link": "/market?recommended=pesticide-spray",
    },
    "Tomato_Leaf_Mold": {
        "disease": "Leaf Mold",
        "confidence": None,
        "cause": "Cladosporium fulvum in high humidity",
        "symptoms": "Yellow patches on upper leaf surface with olive-gray mold below.",
        "recommendation": "Increase ventilation, reduce humidity, and remove infected leaf clusters.",
        "organic_solution": "Use natural plant care spray, prune dense canopy, and irrigate only at root zone.",
        "solution": "Apply a labeled fungicide where mold patches are actively spreading.",
        "best_product": "Natural Plant Care Kit",
        "product_link": "/market?recommended=natural-plant-care-kit",
    },
    "Tomato_Bacterial_spot": {
        "disease": "Bacterial Spot",
        "confidence": None,
        "cause": "Xanthomonas campestris pv. vesicatoria",
        "symptoms": "Small dark spots with yellow halo on foliage and fruit surface injury on severe infection.",
        "recommendation": "Use copper-compatible spray support and avoid working in wet fields.",
        "organic_solution": "Sanitize tools, rogue badly infected leaves, and reduce rain splash around foliage.",
        "solution": "Apply a bactericide schedule suitable for bacterial spot management.",
        "best_product": "Pesticide Spray",
        "product_link": "/market?recommended=pesticide-spray",
    },
    "Tomato_Spider_mites_Two_spotted_spider_mite": {
        "disease": "Spider Mite Infestation",
        "confidence": None,
        "cause": "Two-spotted spider mites multiply rapidly in hot dry conditions.",
        "symptoms": "Tiny yellow stippling, fine webbing, and leaf bronzing on the underside.",
        "recommendation": "Spray underside of leaves thoroughly and reduce dust and heat stress in the canopy.",
        "organic_solution": "Use neem oil in the evening and wash heavily infested leaf clusters if possible.",
        "solution": "Apply a miticide or insecticidal spray with full lower-leaf coverage.",
        "best_product": "Neem Oil",
        "product_link": "/market?recommended=neem-oil",
    },
    "Tomato__Target_Spot": {
        "disease": "Target Spot",
        "confidence": None,
        "cause": "Fungal pressure increases under warm wet canopy conditions.",
        "symptoms": "Round brown lesions with concentric rings and leaf yellowing around the spots.",
        "recommendation": "Start protective spray and remove infected lower canopy leaves.",
        "organic_solution": "Use bio-fungicidal support and avoid late-day leaf wetness.",
        "solution": "Apply a broad-spectrum fungicidal spray where spotting is increasing.",
        "best_product": "Bio Pesticide",
        "product_link": "/market?recommended=bio-pesticide",
    },
    "Tomato__Tomato_YellowLeaf__Curl_Virus": {
        "disease": "Leaf Curl Virus",
        "confidence": None,
        "cause": "Virus spread mainly by whitefly vectors.",
        "symptoms": "Upward curling leaves, yellowing, stunted growth, and poor fruit set.",
        "recommendation": "Rogue badly infected plants early and control vector insects aggressively.",
        "organic_solution": "Use neem oil repeatedly in evening hours and install sticky traps for vector control.",
        "solution": "Focus on vector management and remove heavily infected plants from the field.",
        "best_product": "Neem Oil",
        "product_link": "/market?recommended=neem-oil",
    },
    "Tomato__Tomato_mosaic_virus": {
        "disease": "Mosaic Disease",
        "confidence": None,
        "cause": "Virus spread through handling, infected sap, and insect vectors.",
        "symptoms": "Mottled yellow-green pattern, distorted leaves, and reduced plant vigor.",
        "recommendation": "Remove infected plants early, sanitize hands/tools, and suppress vectors.",
        "organic_solution": "Use neem-based vector control and maintain strict tool hygiene.",
        "solution": "There is no curative spray for the virus itself, so focus on vector control and sanitation.",
        "best_product": "Neem Oil",
        "product_link": "/market?recommended=neem-oil",
    },
    # Fallback
    "DEFAULT": {
        "disease": "Unknown Disease",
        "confidence": 0,
        "cause": "Requires expert field inspection",
        "symptoms": "Mixed stress signals are visible but not conclusive from the current image.",
        "recommendation": "Capture a closer image of one clear leaf and compare with the library guide.",
        "organic_solution": "Keep the field clean, avoid overwatering, and isolate suspicious leaves until confirmed.",
        "solution": "Take a fresh leaf sample to a Krishi Vigyan Kendra or local expert for confirmation.",
        "best_product": "N/A",
        "product_link": "",
    },
}

# Load model class labels
LABELS_PATH = Path("models/crop_disease_labels.json")
MODEL_LABELS = {}
if LABELS_PATH.exists():
    try:
        with open(LABELS_PATH) as f:
            MODEL_LABELS = json.load(f)
    except Exception:
        pass


def get_disease_info(model_class_id: str, confidence: float) -> dict:
    """Map model prediction to detailed knowledge base."""
    class_name = MODEL_LABELS.get(model_class_id, model_class_id)
    info = DISEASE_KNOWLEDGE.get(class_name, DISEASE_KNOWLEDGE["DEFAULT"])
    info = info.copy()
    info["confidence"] = f"{confidence:.0%}"
    info.setdefault("symptoms", info.get("recommendation", "Visible stress markers detected on the leaf."))
    info.setdefault(
        "organic_solution",
        info.get("recommendation", "Maintain field hygiene and use organic support spray."),
    )
    info["explanation_hinglish"] = (
        f"Aapke photo mein {info['disease']} ({info['confidence']}) ka signal mila. {info['recommendation']}"
    )
    return info


if __name__ == "__main__":
    # Test
    test_id = "3"  # Potato___Early_blight
    info = get_disease_info(test_id, 0.92)
    print(json.dumps(info, indent=2, ensure_ascii=False))
