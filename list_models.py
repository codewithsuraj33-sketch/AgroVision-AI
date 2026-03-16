import os

import google.generativeai as genai # type: ignore

api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
if not api_key:
    raise SystemExit("Set GEMINI_API_KEY or GOOGLE_API_KEY before listing models.")

genai.configure(api_key=api_key)
for m in genai.list_models():
    if "generateContent" in m.supported_generation_methods:
        print(m.name)
