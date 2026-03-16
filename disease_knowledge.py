import json
from pathlib import Path

# Detailed Disease Knowledge Base for AgroVision AI
# Maps PlantVillage model classes to farmer insights
# Classes from models/crop_disease_labels.json (16 classes)

DISEASE_KNOWLEDGE = {
    # Rice analogs
    "Potato___Early_blight": {
        "disease": "Leaf Blight",
        "confidence": None,  
        "cause": "Fungal infection (Alternaria) from humid weather & wet leaves",
        "recommendation": "Immediate fungicide spray + improve field drainage",
        "solution": "Apply Copper Oxychloride or Mancozeb (2g/liter water)",
        "best_product": "Copper Oxychloride 50% WP",
        "product_link": "https://www.amazon.in/Copper-Oxychloride-50-WP-Fungicide/dp/B08L5K2Q3P"
    },
    "Potato___Late_blight": {
        "disease": "Late Blight",
        "confidence": None,
        "cause": "Phytophthora infestans fungus thrives in cool humid conditions",
        "recommendation": "Apply systemic fungicide every 7-10 days",
        "solution": "Metalaxyl + Mancozeb or Ridomil Gold",
        "best_product": "Ridomil Gold 68WG",
        "product_link": "https://www.amazon.in/Ridomil-Gold-68WG-Fungicide-250gm/dp/B07Z8G5Q2K"
    },
    
    # Rust analogs
    "Tomato_Septoria_leaf_spot": {
        "disease": "Rust / Leaf Spot",
        "confidence": None,
        "cause": "Septoria fungi from wet foliage & poor air circulation",
        "recommendation": "Remove infected leaves + fungicide rotation",
        "solution": "Chlorothalonil or Mancozeb spray (preventive)",
        "best_product": "Kavach Fungicide (Chlorothalonil)",
        "product_link": "https://www.amazon.in/Dhanuka-Kavach-Chlorothalonil-500gm/dp/B09M5N2Q3R"
    },
    
    # Bacterial spots
    "Pepper__bell___Bacterial_spot": {
        "disease": "Bacterial Spot",
        "confidence": None,
        "cause": "Xanthomonas bacteria spreads via rain splash",
        "recommendation": "Copper bactericide + avoid overhead irrigation",
        "solution": "Kocide 101 or Blitox spray weekly",
        "best_product": "Blitox Copper Fungicide",
        "product_link": "https://www.amazon.in/Blitox-Copper-Fungicide-500g/dp/B07H5K3L2P"
    },
    
    # Healthy classes
    "Pepper__bell___healthy": {"disease": "Healthy", "confidence": None, "cause": "No disease", "recommendation": "Continue monitoring", "solution": "Routine care", "best_product": "N/A", "product_link": ""},
    "Potato___healthy": {"disease": "Healthy", "confidence": None, "cause": "No disease", "recommendation": "Continue monitoring", "solution": "Routine care", "best_product": "N/A", "product_link": ""},
    "Tomato_healthy": {"disease": "Healthy", "confidence": None, "cause": "No disease", "recommendation": "Continue monitoring", "solution": "Routine care", "best_product": "N/A", "product_link": ""},
    
    # Tomato diseases
    "Tomato_Early_blight": {
        "disease": "Early Blight",
        "confidence": None,
        "cause": "Alternaria solani fungus on stressed plants",
        "recommendation": "Mulch soil + staking for air flow",
        "solution": "Dithane M-45 or Score fungicide",
        "best_product": "Dithane M45 75% WP",
        "product_link": "https://www.amazon.in/Dithane-M45-75-WP-Fungicide/dp/B07Z7K3P2Q"
    },
    "Tomato_Late_blight": {
        "disease": "Late Blight",
        "confidence": None,
        "cause": "Phytophthora infestans (cool wet weather)",
        "recommendation": "Space plants + prune lower leaves",
        "solution": "Curzate 60% + Mancozeb",
        "best_product": "Curzate Fungicide",
        "product_link": "https://www.flipkart.com/search?q=curzate+60"
    },
    "Tomato_Leaf_Mold": {
        "disease": "Leaf Mold",
        "confidence": None,
        "cause": "Cladosporium fulvum in high humidity",
        "recommendation": "Ventilation + lower humidity",
        "solution": "Cabendazim or Hexaconazole",
        "best_product": "Bavistin 50% WP",
        "product_link": "https://www.amazon.in/Bavistin-50-WP-Fungicide-100g/dp/B07H4L5M2N"
    },
    "Tomato_Bacterial_spot": {
        "disease": "Bacterial Spot",
        "confidence": None,
        "cause": "Xanthomonas campestris pv. vesicatoria",
        "recommendation": "Copper sprays + resistant varieties",
        "solution": "Kocide or Cuprav",
        "best_product": "Kocide 2000",
        "product_link": "https://www.amazon.in/s?k=kocide+fungicide"
    },
    
    # Fallback
    "DEFAULT": {
        "disease": "Unknown Disease",
        "confidence": 0,
        "cause": "Requires expert field inspection",
        "recommendation": "Consult local agriculture officer",
        "solution": "Take leaf sample to Krishi Vigyan Kendra",
        "best_product": "N/A",
        "product_link": ""
    }
}

# Load model class labels
LABELS_PATH = Path("models/crop_disease_labels.json")
MODEL_LABELS = {}
if LABELS_PATH.exists():
    try:
        with open(LABELS_PATH, 'r') as f:
            MODEL_LABELS = json.load(f)
    except Exception:
        pass

def get_disease_info(model_class_id: str, confidence: float) -> dict:
    """Map model prediction to detailed knowledge base."""
    class_name = MODEL_LABELS.get(model_class_id, model_class_id)
    info = DISEASE_KNOWLEDGE.get(class_name, DISEASE_KNOWLEDGE["DEFAULT"])
    info = info.copy()
    info["confidence"] = f"{confidence:.0%}"
    info["explanation_hinglish"] = f"Aapke photo mein {info['disease']} ({info['confidence']}) dikha. {info['recommendation']} karo jaldi."
    return info

if __name__ == "__main__":
    # Test
    test_id = "3"  # Potato___Early_blight
    info = get_disease_info(test_id, 0.92)
    print(json.dumps(info, indent=2, ensure_ascii=False))

