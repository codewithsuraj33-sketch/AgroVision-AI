import os

import google.generativeai as genai # type: ignore

import json
from PIL import Image # type: ignore

api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
if not api_key:
    raise SystemExit("Set GEMINI_API_KEY or GOOGLE_API_KEY before running this test.")

genai.configure(api_key=api_key)

def test_prompt():
    user_crop = "Tomato"
    user_location = "Andhra Pradesh, India"
    
    prompt = f"""
    As a Plant Pathology Expert specializing in {user_crop}, analyze this leaf image.
    Use diagnostic criteria comparable to Kaggle/PlantVillage datasets to provide a precise diagnosis.
    
    Location Context: {user_location}
    Anticipated Crop: {user_crop}
    
    Provide the analysis in the following strict JSON format:
    {{
      "disease": "Specific Scientific/Common Name",
      "confidence": 85,
      "symptoms": "Description of visible markers",
      "cause": "Specific biological or environmental cause",
      "organic_solution": "Non-chemical treatment",
      "chemical_solution": "Recommended fungicide/pesticide",
      "prevention": ["tip 1", "tip 2", "tip 3"],
      "explanation_hinglish": "A simple 2-sentence explanation in Hinglish (Hindi + English) for the farmer",
      "risk_level": "Low/Medium/High",
      "crop": "Detected crop name"
    }}
    Return ONLY raw JSON. No markdown.
    """
    
    print("Testing Prompt with Gemini...")
    model = genai.GenerativeModel("gemini-2.0-flash")
    
    # Using a dummy image or a local image if exists for testing
    # For now, let's just see if the prompt structure is accepted and it returns valid JSON.
    # To truly test "image" analysis, we need an actual image. 
    # Let's try to find an image in the project to test with.
    
    img_path = r"c:\Users\suraj\OneDrive\Desktop\New folder (4)\static\uploads\tomato.jpg"
    if not os.path.exists(img_path):
        # try to find any image
        for root, dirs, files in os.walk(r"c:\Users\suraj\OneDrive\Desktop\New folder (4)\static\uploads"):
            for f in files:
                if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                    img_path = os.path.join(root, f)
                    break
            if img_path: break

    if os.path.exists(img_path):
        print(f"Using image: {img_path}")
        img = Image.open(img_path)
        response = model.generate_content([prompt, img])
        print("\n--- Response Text ---")
        print(response.text)
        print("--- End Response ---\n")
        
        try:
            text = response.text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            data = json.loads(text)
            print("Successfully parsed JSON:")
            print(json.dumps(data, indent=2))
        except Exception as e:
            print(f"Failed to parse JSON: {e}")
    else:
        print("No test image found. Please provide an image to test full analysis.")

if __name__ == "__main__":
    test_prompt()
