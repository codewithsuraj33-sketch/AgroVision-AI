import json
import os
import random
import re
import smtplib
import time
import uuid
import hmac
from functools import wraps
from base64 import b64encode
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from io import BytesIO
from pathlib import Path
from hashlib import sha1, sha256
from datetime import date, datetime, timedelta, timezone
from html import escape
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse, parse_qsl, urlunparse
from urllib.request import Request, urlopen

import numpy as np # type: ignore
from flask import Flask, Response, abort, redirect, render_template, request, session, jsonify # type: ignore
from flask_sqlalchemy import SQLAlchemy # type: ignore
from PIL import Image, ImageOps, UnidentifiedImageError # type: ignore
from werkzeug.security import check_password_hash, generate_password_hash # type: ignore
from werkzeug.utils import secure_filename # type: ignore
import google.generativeai as genai # type: ignore

try:
    import torch # type: ignore
    import torch.nn as nn # type: ignore
    from torchvision import transforms # type: ignore
except Exception:
    torch = None

app = Flask(__name__)

SHARED_UI_CSS_TAG = '<link rel="stylesheet" href="/static/shared-ui.css">'
SHARED_UI_JS_TAG = '<script src="/static/shared-ui.js"></script>'
<<<<<<< HEAD
=======
MOBILE_SIDEBAR_JS_TAG = '<script src="/static/mobile-sidebar.js"></script>'
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057


@app.template_filter("static_version")
def static_version_filter(url):
    """Cache-bust local /static/... assets when files are replaced on disk."""
    raw_url = str(url or "").strip()
    if not raw_url or not raw_url.startswith("/static/"):
        return raw_url

    parsed = urlparse(raw_url)
    static_path = (parsed.path or "").lstrip("/")
    file_path = Path(app.root_path) / static_path
    try:
        mtime = int(file_path.stat().st_mtime)
    except OSError:
        return raw_url

    query = dict(parse_qsl(parsed.query or "", keep_blank_values=True))
    query["v"] = str(mtime)
    return urlunparse(parsed._replace(query=urlencode(query)))


@app.after_request
def inject_shared_ui_assets(response):
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.direct_passthrough or "text/html" not in content_type:
        return response

    try:
        html = response.get_data(as_text=True)
    except (RuntimeError, UnicodeDecodeError):
        return response

<<<<<<< HEAD
    if not html or "/static/shared-ui.js" in html:
        return response

    if "</head>" in html:
        html = html.replace("</head>", f"  {SHARED_UI_CSS_TAG}\n</head>", 1)
    else:
        html = f"{SHARED_UI_CSS_TAG}\n{html}"

    if "</body>" in html:
        html = html.replace("</body>", f"  {SHARED_UI_JS_TAG}\n</body>", 1)
    else:
        html = f"{html}\n{SHARED_UI_JS_TAG}"

    response.set_data(html)
=======
    if not html:
        return response

    changed = False

    if "/static/shared-ui.js" not in html:
        if "</head>" in html:
            html = html.replace("</head>", f"  {SHARED_UI_CSS_TAG}\n</head>", 1)
        else:
            html = f"{SHARED_UI_CSS_TAG}\n{html}"

        if "</body>" in html:
            html = html.replace("</body>", f"  {SHARED_UI_JS_TAG}\n</body>", 1)
        else:
            html = f"{html}\n{SHARED_UI_JS_TAG}"
        changed = True

    # Inject mobile sidebar behavior on pages that include the hamburger button.
    if 'id="mobileMenuToggle"' in html and "/static/mobile-sidebar.js" not in html:
        if "</body>" in html:
            html = html.replace("</body>", f"  {MOBILE_SIDEBAR_JS_TAG}\n</body>", 1)
        else:
            html = f"{html}\n{MOBILE_SIDEBAR_JS_TAG}"
        changed = True

    if changed:
        response.set_data(html)
    return response


@app.after_request
def set_csrf_cookie(response):
    """Expose a CSRF token to JavaScript via cookie (double-submit pattern)."""
    try:
        token = get_csrf_token()
    except Exception:
        return response

    # Not HttpOnly on purpose: JS fetch() reads it and sends back in X-CSRFToken.
    response.set_cookie(
        "csrf_token",
        token,
        samesite="Lax",
        secure=bool(app.config.get("SESSION_COOKIE_SECURE")),
        httponly=False,
        max_age=60 * 60 * 24 * 30,
    )
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    return response


def load_local_env_file(env_path):
    if not env_path.exists():
        return

    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def get_env_int(name, default):
    try:
        raw_value = (os.getenv(name) or "").strip()
        return int(raw_value or default)
    except (TypeError, ValueError):
        return default


load_local_env_file(Path(app.root_path) / ".env")

GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
GROQ_MODEL = (os.getenv("GROQ_CHAT_MODEL") or "llama-3.1-8b-instant").strip()

_secret_from_env = (
    os.getenv("FLASK_SECRET_KEY")
    or os.getenv("SECRET_KEY")
    or os.getenv("APP_SECRET_KEY")
    or ""
).strip()

if _secret_from_env:
    app.secret_key = _secret_from_env
else:
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    _secret_path = Path(app.instance_path) / "secret_key.txt"
    try:
        if _secret_path.exists():
            app.secret_key = (_secret_path.read_text(encoding="utf-8") or "").strip() or uuid.uuid4().hex
        else:
            app.secret_key = uuid.uuid4().hex
            _secret_path.write_text(app.secret_key, encoding="utf-8")
    except OSError:
        app.secret_key = uuid.uuid4().hex

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (os.getenv("FLASK_ENV") or "").strip().lower() == "production"

db = SQLAlchemy(app)

CSRF_SESSION_KEY = "_csrf_token"
CSRF_COOKIE_NAME = "csrf_token"
OTP_EXPIRY_MINUTES = get_env_int("OTP_EXPIRY_MINUTES", 5)
OTP_RESEND_INTERVAL_SECONDS = get_env_int("OTP_RESEND_INTERVAL_SECONDS", 30)
_otp_debug_fallback_env = (os.getenv("OTP_DEBUG_FALLBACK") or "").strip().lower()
OTP_DEBUG_FALLBACK_ENABLED = (
    _otp_debug_fallback_env in {"1", "true", "yes", "on"}
    or (not _otp_debug_fallback_env and not app.config["SESSION_COOKIE_SECURE"])
)

UPLOADS_DIR = Path(app.root_path) / "static" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

PRODUCTS_UPLOAD_DIR = Path(app.root_path) / "static" / "products"
PRODUCTS_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app.config["UPLOAD_FOLDER"] = str(UPLOADS_DIR)
KISAN_DOST_KNOWLEDGE_PATH = Path(app.root_path) / "dataset" / "kisan_dost_faq.json"
<<<<<<< HEAD
AI_CROP_DOCTOR_FAQ_PATH = Path(app.root_path) / "dataset" / "ai_crop_doctor_project_faq.txt"
AI_CROP_DOCTOR_LOCAL_QA_PATH = Path(app.root_path) / "dataset" / "ai_crop_doctor_local_qa.json"
DISEASE_SYMPTOM_RULES_PATH = Path(app.root_path) / "dataset" / "disease_symptom_rules.json"
CROP_LIBRARY_DATA_PATH = Path(app.root_path) / "dataset" / "crop_library.json"
STORE_PRODUCTS_DATA_PATH = Path(app.root_path) / "dataset" / "store_products.json"
DISEASE_STORE_PRODUCTS_DATA_PATH = Path(app.root_path) / "dataset" / "disease_store_products.json"
DISEASE_PRODUCT_MAPPINGS_DATA_PATH = Path(app.root_path) / "dataset" / "disease_product_mappings.json"
=======
CROP_LIBRARY_DATA_PATH = Path(app.root_path) / "dataset" / "crop_library.json"
STORE_PRODUCTS_DATA_PATH = Path(app.root_path) / "dataset" / "store_products.json"
DISEASE_LIBRARY_DATA_PATH = Path(app.root_path) / "dataset" / "disease_data.json"
CULTIVATION_TIPS_DATA_PATH = Path(app.root_path) / "dataset" / "cultivation_tips.json"
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
EMAIL_LOGO_PATH = Path(app.root_path) / "static" / "brand" / "agrovision-email-logo.png"
EMAIL_LOGO_FILENAME = EMAIL_LOGO_PATH.name
EMAIL_LOGO_SUBTYPE = "png"
CROP_DISEASE_MODEL_PATH = Path(
    os.getenv("CROP_DISEASE_MODEL_PATH", Path(app.root_path) / "models" / "agrovision_disease_model.pth")
)
CROP_DISEASE_LABELS_PATH = Path(
    os.getenv("CROP_DISEASE_LABELS_PATH", Path(app.root_path) / "models" / "agrovision_disease_labels.json")
)
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
DISEASE_MODEL_CACHE = {"attempted": False, "model": None, "labels": None}
CROP_LIBRARY_CACHE = None
<<<<<<< HEAD
CROP_LIBRARY_IMAGE_DIR = Path(app.root_path) / "static" / "images" / "crops"
CROP_LIBRARY_DEFAULT_IMAGE = "/static/images/default_crop.png"
=======
DISEASE_LIBRARY_CACHE = {"mtime": 0.0, "items": []}
CULTIVATION_TIPS_CACHE = {"mtime": 0.0, "payload": {}}
CROP_LIBRARY_IMAGE_DIR = Path(app.root_path) / "static" / "images" / "crops"
CROP_LIBRARY_DEFAULT_IMAGE = "/static/images/default_crop.png"
DISEASE_LIBRARY_IMAGE_DIR = Path(app.root_path) / "static" / "library" / "diseases"
DISEASE_LIBRARY_DEFAULT_IMAGES = {
    "insect": "/static/library/diseases/aphids.jpg",
    "pest": "/static/library/diseases/aphids.jpg",
    "fungus": "/static/library/diseases/leaf-blight.jpg",
    "bacteria": "/static/library/diseases/bacterial-spot.jpg",
    "virus": "/static/library/diseases/mosaic-disease.jpg",
    "disease": "/static/library/diseases/leaf-blight.jpg",
}
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
STORE_PRODUCT_FALLBACK_IMAGE = "/static/images/store-product-fallback.svg"
CROP_LIBRARY_REMOTE_IMAGE_FALLBACKS = {
    "barley": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/20/Barley_%28Hordeum_vulgare%29_-_United_States_National_Arboretum_-_24_May_2009.jpg/1280px-Barley_%28Hordeum_vulgare%29_-_United_States_National_Arboretum_-_24_May_2009.jpg",
    "ginger": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/18/Koeh-146-no_text.jpg/1280px-Koeh-146-no_text.jpg",
    "muskmelon": "https://upload.wikimedia.org/wikipedia/commons/a/ae/Meloen_vrucht_met_bloem.jpg",
}

STORE_CATEGORY_ORDER = ["All", "Pesticides", "Fertilizers", "Seeds", "Tools", "Organic"]
STORE_CATEGORY_META = {
    "Pesticides": {
        "icon": "fa-shield-virus",
        "accent": "pesticide",
        "description": "Protect crops from fungal outbreaks, pests, and field stress.",
    },
    "Fertilizers": {
        "icon": "fa-flask",
        "accent": "fertilizer",
        "description": "Balanced nutrient inputs for growth, flowering, and recovery.",
    },
    "Seeds": {
        "icon": "fa-seedling",
        "accent": "seed",
        "description": "High-yield seed packs for seasonal sowing and crop planning.",
    },
    "Tools": {
        "icon": "fa-screwdriver-wrench",
        "accent": "tool",
        "description": "Smart farming tools for irrigation, seeding, and monitoring.",
    },
    "Organic": {
        "icon": "fa-leaf",
        "accent": "organic",
        "description": "Eco-friendly farm care for soil, plant vigor, and pest management.",
    },
}

<<<<<<< HEAD
=======
MAX_PROFILE_PHOTO_BYTES = get_env_int("MAX_PROFILE_PHOTO_BYTES", 5 * 1024 * 1024)
MAX_PRODUCT_IMAGE_BYTES = get_env_int("MAX_PRODUCT_IMAGE_BYTES", 10 * 1024 * 1024)
MAX_DISEASE_IMAGE_BYTES = get_env_int("MAX_DISEASE_IMAGE_BYTES", 10 * 1024 * 1024)

# Very small in-memory rate limiter (demo-friendly). Resets on server restart.
RATE_LIMIT_BUCKET = {}

>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
# Admin Panel (defaults requested by user; override via environment for safety)
ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "admin123@gmail.com").strip().lower()
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "123").strip()
ADMIN_NOTIFY_EMAIL = (os.getenv("ADMIN_NOTIFY_EMAIL") or ADMIN_EMAIL).strip().lower()
<<<<<<< HEAD
=======
RAZORPAY_WEBHOOK_SECRET = (os.getenv("RAZORPAY_WEBHOOK_SECRET") or "").strip()
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
STORE_CATEGORY_HIGHLIGHTS = {
    "Pesticides": [
        "Targets disease and pest hotspots quickly.",
        "Useful for preventive and curative crop protection schedules.",
        "Follow label dose and interval before each application.",
    ],
    "Fertilizers": [
        "Supports balanced crop nutrition across growth stages.",
        "Helps improve vigor, root activity, and field uniformity.",
        "Apply with irrigation or basal schedule as recommended.",
    ],
    "Seeds": [
        "Designed for healthy germination and crop stand establishment.",
        "Useful for seasonal sowing plans and kitchen garden setups.",
        "Store in cool, dry conditions before planting.",
    ],
    "Tools": [
        "Built to save field labor and improve operational precision.",
        "Useful for monitoring water, weather, or planting tasks.",
        "Check calibration before each field use for reliable results.",
    ],
    "Organic": [
        "Supports low-residue farming and steady soil improvement.",
        "Useful for preventive farm care and regenerative practices.",
        "Pair with mulch, compost, and scouting for best results.",
    ],
}
<<<<<<< HEAD
STORE_DISEASE_PRODUCT_RULES = [
    (("healthy",), None),
    (("bacterial", "blight", "mold", "rust", "spot", "mildew", "fungal", "fungus", "rot"), "Bio Pesticide"),
    (("pest", "insect", "mite", "aphid", "vector", "mosaic", "curl virus", "whitefly", "thrips"), "Neem Oil"),
    (("deficiency", "chlorosis", "yellowing", "stress", "wilt"), "Soil Health Booster"),
]
DISEASE_PRODUCT_RECOMMENDATION_PROFILES = {
    "healthy": {
        "category_boosts": {"Tools": 6, "Organic": 3, "Fertilizers": 2},
        "keywords": {"soil test": 9, "kit": 4, "booster": 3, "compost": 2},
    },
    "fungus": {
        "category_boosts": {"Pesticides": 7, "Organic": 5, "Tools": 1},
        "keywords": {"bio pesticide": 9, "pesticide spray": 7, "spray": 5, "neem oil": 4, "organic pest spray": 5, "sprayer": 2},
    },
    "bacteria": {
        "category_boosts": {"Pesticides": 7, "Organic": 5, "Tools": 1},
        "keywords": {"bio pesticide": 9, "pesticide spray": 7, "spray": 5, "neem oil": 3, "organic pest spray": 4, "sprayer": 2},
    },
    "virus": {
        "category_boosts": {"Organic": 7, "Pesticides": 6, "Tools": 1},
        "keywords": {"neem oil": 10, "organic pest spray": 8, "bio pesticide": 6, "pesticide spray": 5, "plant cover": 2},
    },
    "insect": {
        "category_boosts": {"Organic": 7, "Pesticides": 6, "Tools": 1},
        "keywords": {"neem oil": 10, "organic pest spray": 8, "bio pesticide": 7, "pesticide spray": 6, "sprayer": 2},
    },
    "stress": {
        "category_boosts": {"Organic": 6, "Fertilizers": 6, "Tools": 2},
        "keywords": {"soil health booster": 9, "vermicompost": 7, "growth booster": 7, "npk fertilizer": 6, "compost": 4, "soil test": 4},
    },
    "general": {
        "category_boosts": {"Organic": 4, "Pesticides": 4, "Tools": 1},
        "keywords": {"bio pesticide": 5, "neem oil": 5, "organic pest spray": 5, "soil health booster": 4},
    },
=======


STATIC_PRODUCTS_DIR = Path(app.root_path) / "static" / "products"
ALLOWED_LOCAL_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_STORE_PRODUCT_IMAGE_INDEX = None


def _tokenize_filename(value):
    return set(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _build_store_product_image_index():
    """Cache a lightweight index of local product images for fuzzy matching."""
    global _STORE_PRODUCT_IMAGE_INDEX
    if _STORE_PRODUCT_IMAGE_INDEX is not None:
        return _STORE_PRODUCT_IMAGE_INDEX

    index = []
    try:
        for path in STATIC_PRODUCTS_DIR.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() not in ALLOWED_LOCAL_IMAGE_SUFFIXES:
                continue
            tokens = _tokenize_filename(path.stem)
            index.append(
                {
                    "name": path.name,
                    "tokens": tokens,
                    "url": f"/static/products/{path.name}",
                }
            )
    except OSError:
        index = []

    _STORE_PRODUCT_IMAGE_INDEX = index
    return _STORE_PRODUCT_IMAGE_INDEX


def _local_static_path_from_url(static_url):
    url = str(static_url or "").strip()
    if not url.startswith("/static/products/"):
        return None
    filename = url.split("/static/products/", 1)[1]
    if not filename or "/" in filename or "\\" in filename:
        return None
    return STATIC_PRODUCTS_DIR / filename


def resolve_store_product_image_url(product):
    """Prefer a stable local image URL when possible (avoids external Unsplash dependency)."""
    raw = str(getattr(product, "image_url", "") or "").strip()

    # If admin provided a remote URL, keep it.
    if raw and (raw.startswith("http://") or raw.startswith("https://")):
        return raw

    # If it's a local static products URL and the file exists, use it.
    local_path = _local_static_path_from_url(raw) if raw else None
    if local_path is not None and local_path.exists():
        return raw

    # Try exact matches by slug / name.
    slug = str(getattr(product, "slug", "") or "").strip().lower()
    name_slug = slugify_crop_name(getattr(product, "name", "") or "")
    for candidate_base in [slug, name_slug]:
        if not candidate_base:
            continue
        for ext in [".jpg", ".jpeg", ".png", ".webp"]:
            candidate = STATIC_PRODUCTS_DIR / f"{candidate_base}{ext}"
            if candidate.exists():
                return f"/static/products/{candidate.name}"

    # Fuzzy match against local images by token overlap.
    tokens = _tokenize_filename(slug) | _tokenize_filename(name_slug) | _tokenize_filename(getattr(product, "name", ""))
    best = None
    best_score = 0
    for item in _build_store_product_image_index():
        overlap = len(tokens & (item.get("tokens") or set()))
        if overlap > best_score:
            best_score = overlap
            best = item

    if best is not None and best_score >= 2:
        return best.get("url") or STORE_PRODUCT_FALLBACK_IMAGE

    return STORE_PRODUCT_FALLBACK_IMAGE
STORE_DISEASE_PRODUCT_RULES = [
    (("healthy",), None),
    # Keep mappings aligned with products that actually exist in dataset/store_products.json.
    (("pest", "insect", "mite", "aphid", "vector", "whitefly", "thrips", "armyworm", "worm", "caterpillar", "mosaic", "curl virus"), "Neem Oil"),
    (("blight", "mold", "rust", "spot", "mildew", "fungal", "fungus", "bacterial", "rot", "scab"), "Bio Pesticide"),
    (("organic", "chemical-free", "bio"), "Organic Pest Spray"),
]
DISEASE_PRODUCT_PREFERENCES = {
    "healthy": ["Soil Health Booster", "Natural Plant Care Kit"],
    "brown spot": ["Soil Health Booster", "NPK Fertilizer", "Bio Pesticide"],
    "rice blast": ["Pesticide Spray", "Bio Pesticide"],
    "leaf blight": ["Pesticide Spray", "Bio Pesticide"],
    "early blight": ["Bio Pesticide", "Pesticide Spray"],
    "late blight": ["Pesticide Spray", "Bio Pesticide"],
    "yellow rust": ["Bio Pesticide", "Pesticide Spray"],
    "leaf rust": ["Bio Pesticide", "Pesticide Spray"],
    "powdery mildew": ["Organic Pest Spray", "Bio Pesticide"],
    "leaf mold": ["Natural Plant Care Kit", "Bio Pesticide"],
    "bacterial spot": ["Pesticide Spray", "Bio Pesticide"],
    "mosaic disease": ["Neem Oil", "Organic Pest Spray"],
    "leaf curl virus": ["Neem Oil", "Organic Pest Spray"],
    "spider mite infestation": ["Neem Oil", "Organic Pest Spray"],
    "fall armyworm": ["Neem Oil", "Pesticide Spray"],
    "stem borer": ["Neem Oil", "Pesticide Spray"],
    "whitefly": ["Neem Oil", "Organic Pest Spray"],
    "aphids": ["Neem Oil", "Organic Pest Spray"],
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
}
RAZORPAY_KEY_ID = (os.getenv("RAZORPAY_KEY_ID") or "rzp_test_SRyeQDEMFrRHwD").strip()
RAZORPAY_KEY_SECRET = (os.getenv("RAZORPAY_KEY_SECRET") or "b1mY7dNC8mDuxekLF2YmiV0I").strip()
RAZORPAY_CURRENCY = "INR"
RAZORPAY_CHECKOUT_NAME = "AgroVision AI Store"

# Subscription plans (monthly, demo-ready).
SUBSCRIPTION_TRIAL_DAYS = get_env_int("SUBSCRIPTION_TRIAL_DAYS", 7)
SUBSCRIPTION_PLANS = {
    "free": {
        "label": "Free",
        "price_inr": 0,
        "duration_days": 0,
        "rank": 0,
        "description": "Basic access to core modules.",
    },
    "pro": {
        "label": "Pro",
        "price_inr": 99,
        "duration_days": 30,
        "rank": 1,
        "description": "Unlock AI disease detection and faster insights.",
    },
    "premium": {
        "label": "Premium",
        "price_inr": 199,
        "duration_days": 30,
        "rank": 2,
        "description": "All Pro features + satellite farm twin monitoring.",
    },
}

CROP_LIBRARY_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

CROP_LIBRARY_CATEGORY_MAP = {
    "rice": "cereal",
    "wheat": "cereal",
    "maize": "cereal",
    "barley": "cereal",
    "millet": "cereal",
    "sorghum": "cereal",
    "soybean": "legume",
    "pea": "legume",
    "chickpea": "legume",
    "lentil": "legume",
    "pigeon-pea": "legume",
    "green-gram": "legume",
    "black-gram": "legume",
    "tomato": "vegetable",
    "cucumber": "vegetable",
    "pumpkin": "vegetable",
    "chili": "vegetable",
    "brinjal": "vegetable",
    "okra": "vegetable",
    "cabbage": "vegetable",
    "cauliflower": "vegetable",
    "spinach": "vegetable",
    "lettuce": "vegetable",
    "potato": "root_tuber",
    "onion": "root_tuber",
    "garlic": "root_tuber",
    "carrot": "root_tuber",
    "cassava": "root_tuber",
    "sweet-potato": "root_tuber",
    "banana": "fruit",
    "mango": "fruit",
    "papaya": "fruit",
    "watermelon": "fruit",
    "muskmelon": "fruit",
    "guava": "fruit",
    "pomegranate": "fruit",
    "coconut": "fruit",
    "turmeric": "spice",
    "ginger": "spice",
    "coriander": "spice",
    "fenugreek": "spice",
    "mustard": "oilseed",
    "sunflower": "oilseed",
    "groundnut": "oilseed",
    "sesame": "oilseed",
    "cotton": "fiber",
    "jute": "fiber",
    "sugarcane": "cash_crop",
    "coffee": "beverage",
    "tea": "beverage",
}

CROP_LIBRARY_CATEGORY_LABELS = {
    "cereal": "Cereal",
    "legume": "Legume",
    "vegetable": "Vegetable",
    "root_tuber": "Root & Tuber",
    "fruit": "Fruit",
    "spice": "Spice & Herb",
    "oilseed": "Oilseed",
    "fiber": "Fiber Crop",
    "cash_crop": "Cash Crop",
    "beverage": "Beverage Crop",
}

CROP_LIBRARY_CATEGORY_DEFAULTS = {
    "cereal": {
        "good_companions": ["Pea", "Soybean", "Coriander"],
        "bad_companions": ["Fennel", "Sunflower", "Pumpkin"],
        "farming_tips": [
            "Use certified seed and maintain timely sowing for uniform crop establishment.",
            "Split nitrogen application between early growth stages to reduce losses.",
            "Keep the field weed-free during the first 30 to 40 days after planting.",
        ],
    },
    "legume": {
        "good_companions": ["Maize", "Sorghum", "Sesame"],
        "bad_companions": ["Onion", "Garlic", "Sunflower"],
        "farming_tips": [
            "Treat seed with suitable Rhizobium culture before sowing where recommended.",
            "Avoid standing water around the root zone during flowering and pod filling.",
            "Harvest on time once pods mature to reduce shattering and quality loss.",
        ],
    },
    "vegetable": {
        "good_companions": ["Onion", "Garlic", "Coriander"],
        "bad_companions": ["Potato", "Fennel", "Cabbage"],
        "farming_tips": [
            "Use raised beds or well-drained rows to prevent root diseases in humid spells.",
            "Mulch the root zone to conserve moisture and suppress weed pressure.",
            "Scout leaves and flowers twice a week for early pest and disease detection.",
        ],
    },
    "root_tuber": {
        "good_companions": ["Pea", "Bean", "Coriander"],
        "bad_companions": ["Sunflower", "Pumpkin", "Fennel"],
        "farming_tips": [
            "Prepare a loose, friable seedbed so roots and tubers can expand evenly.",
            "Irrigate lightly and consistently to avoid cracking, bolting, or bulb splitting.",
            "Stop heavy irrigation before harvest to improve skin set and storage life.",
        ],
    },
    "fruit": {
        "good_companions": ["Coriander", "Legumes", "Marigold"],
        "bad_companions": ["Potato", "Cabbage", "Waterlogging-prone crops"],
        "farming_tips": [
            "Keep a mulch ring around the base to stabilize soil moisture and temperature.",
            "Prune damaged or overcrowded growth to improve airflow and light penetration.",
            "Apply organic matter regularly to support steady fruiting and root health.",
        ],
    },
    "spice": {
        "good_companions": ["Chili", "Onion", "Legumes"],
        "bad_companions": ["Pumpkin", "Cucumber", "Waterlogging-prone crops"],
        "farming_tips": [
            "Use disease-free planting material and avoid repeated planting in the same bed.",
            "Maintain even soil moisture without water stagnation around the crown or rhizome.",
            "Dry harvested produce quickly and uniformly to preserve color and aroma.",
        ],
    },
    "oilseed": {
        "good_companions": ["Chickpea", "Lentil", "Coriander"],
        "bad_companions": ["Potato", "Pumpkin", "Sunflower"],
        "farming_tips": [
            "Avoid excess nitrogen because it pushes leaf growth over seed development.",
            "Protect flowering plants from moisture stress to improve seed set.",
            "Harvest once seed heads mature and dry them on a clean surface before storage.",
        ],
    },
    "fiber": {
        "good_companions": ["Sesame", "Groundnut", "Coriander"],
        "bad_companions": ["Potato", "Pumpkin", "Watermelon"],
        "farming_tips": [
            "Maintain proper plant population because both crowding and gaps reduce fiber quality.",
            "Weed early and keep the field aerated during vigorous vegetative growth.",
            "Schedule irrigation and top dressing before peak growth to avoid yield checks.",
        ],
    },
    "cash_crop": {
        "good_companions": ["Onion", "Garlic", "Soybean"],
        "bad_companions": ["Pumpkin", "Cucumber", "Watermelon"],
        "farming_tips": [
            "Use healthy planting material and remove weak stools or sets before planting.",
            "Apply nutrients in splits to support steady tillering and stalk development.",
            "Keep drainage channels open during monsoon periods to prevent root stress.",
        ],
    },
    "beverage": {
        "good_companions": ["Coriander", "Ginger", "Legumes"],
        "bad_companions": ["Potato", "Sunflower", "Waterlogging-prone crops"],
        "farming_tips": [
            "Maintain a consistent mulch layer to protect feeder roots and soil structure.",
            "Prune, tip, or manage canopy density to balance vegetative and productive growth.",
            "Harvest selectively at the correct maturity stage for better end-use quality.",
        ],
    },
}

CROP_LIBRARY_OVERRIDES = {
    "rice": {
        "aliases": ["Paddy"],
        "good_companions": ["Sesame", "Green Gram", "Azolla"],
        "bad_companions": ["Sugarcane", "Pumpkin", "Cucumber"],
        "farming_tips": [
            "Maintain a shallow water layer after transplanting until the crop is well established.",
            "Drain excess water before top dressing to improve fertilizer use efficiency.",
            "Monitor stem borer and leaf folder pressure from the tillering stage onward.",
        ],
    },
    "wheat": {
        "good_companions": ["Pea", "Mustard", "Coriander"],
        "bad_companions": ["Sunflower", "Pumpkin", "Fennel"],
    },
    "maize": {
        "aliases": ["Corn"],
        "good_companions": ["Bean", "Pea", "Pumpkin"],
        "bad_companions": ["Tomato", "Sunflower", "Potato"],
    },
    "soybean": {
        "good_companions": ["Maize", "Sugarcane", "Sesame"],
        "bad_companions": ["Onion", "Garlic", "Sunflower"],
    },
    "tomato": {
        "good_companions": ["Basil", "Onion", "Garlic"],
        "bad_companions": ["Potato", "Cabbage", "Fennel"],
        "farming_tips": [
            "Stake or trellis plants early to keep fruits clean and improve airflow.",
            "Water at the base and keep foliage dry to reduce blight pressure.",
            "Harvest regularly at breaker stage for better shelf life and continued fruiting.",
        ],
    },
    "potato": {
        "good_companions": ["Bean", "Coriander", "Cabbage"],
        "bad_companions": ["Tomato", "Pumpkin", "Sunflower"],
    },
    "onion": {
        "good_companions": ["Carrot", "Tomato", "Cucumber"],
        "bad_companions": ["Pea", "Bean", "Sage"],
    },
    "garlic": {
        "good_companions": ["Tomato", "Brinjal", "Cabbage"],
        "bad_companions": ["Pea", "Bean", "Sesame"],
    },
    "cucumber": {
        "good_companions": ["Pea", "Bean", "Onion"],
        "bad_companions": ["Potato", "Sage", "Pumpkin"],
    },
    "pumpkin": {
        "good_companions": ["Maize", "Bean", "Sunflower"],
        "bad_companions": ["Potato", "Fennel", "Cucumber"],
    },
    "chili": {
        "aliases": ["Chilli Pepper"],
        "good_companions": ["Onion", "Garlic", "Coriander"],
        "bad_companions": ["Fennel", "Bean", "Cabbage"],
    },
    "cotton": {
        "good_companions": ["Groundnut", "Sesame", "Green Gram"],
        "bad_companions": ["Okra", "Sunflower", "Watermelon"],
    },
    "sugarcane": {
        "good_companions": ["Onion", "Garlic", "Soybean"],
        "bad_companions": ["Pumpkin", "Watermelon", "Sweet Potato"],
    },
    "banana": {
        "good_companions": ["Papaya", "Turmeric", "Coriander"],
        "bad_companions": ["Potato", "Cabbage", "Waterlogging-prone crops"],
    },
    "mango": {
        "good_companions": ["Coriander", "Legumes", "Marigold"],
        "bad_companions": ["Potato", "Tomato", "Waterlogging-prone crops"],
    },
    "papaya": {
        "good_companions": ["Banana", "Coriander", "Legumes"],
        "bad_companions": ["Potato", "Cabbage", "Waterlogging-prone crops"],
    },
    "brinjal": {
        "aliases": ["Eggplant", "Aubergine"],
        "good_companions": ["Bean", "Marigold", "Coriander"],
        "bad_companions": ["Potato", "Fennel", "Cabbage"],
    },
    "pea": {
        "good_companions": ["Carrot", "Cucumber", "Maize"],
        "bad_companions": ["Onion", "Garlic", "Potato"],
    },
    "turmeric": {
        "good_companions": ["Onion", "Chili", "Banana"],
        "bad_companions": ["Pumpkin", "Waterlogging-prone crops", "Cucumber"],
    },
    "groundnut": {
        "aliases": ["Peanut"],
    },
    "pigeon-pea": {
        "aliases": ["Arhar", "Toor Dal"],
    },
    "green-gram": {
        "aliases": ["Mung Bean", "Moong"],
    },
    "black-gram": {
        "aliases": ["Urad", "Urad Dal"],
    },
    "mustard": {
        "good_companions": ["Wheat", "Chickpea", "Lentil"],
        "bad_companions": ["Pumpkin", "Sunflower", "Potato"],
    },
    "sesame": {
        "aliases": ["Til"],
    },
}

OPENWEATHER_API_KEY = os.getenv(
    "OPENWEATHER_API_KEY",
    "",
)
CDSE_CLIENT_ID = os.getenv(
    "CDSE_CLIENT_ID",
    "",
)
CDSE_CLIENT_SECRET = os.getenv(
    "CDSE_CLIENT_SECRET",
    "",
)
GOOGLE_MAPS_API_KEY = os.getenv(
    "GOOGLE_MAPS_API_KEY",
    "",
)

API_TIMEOUT_SECONDS = 12
CDSE_TOKEN_CACHE = {"access_token": None, "expires_at": 0}
NDVI_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["B04", "B08", "dataMask"],
    output: { bands: 4 }
  };
}

function evaluatePixel(sample) {
  if (sample.dataMask === 0) {
    return [0, 0, 0, 0];
  }

  let ndvi = index(sample.B08, sample.B04);

  if (ndvi < 0.15) return [0.93, 0.63, 0.26, 1];
  if (ndvi < 0.3) return [0.98, 0.82, 0.24, 1];
  if (ndvi < 0.45) return [0.62, 0.82, 0.26, 1];
  if (ndvi < 0.6) return [0.34, 0.72, 0.23, 1];
  return [0.14, 0.55, 0.16, 1];
}
""".strip()

CROP_DISEASE_LIBRARY = {
    "rice": [
        {
            "name": "Brown Spot",
            "signals": {"brown", "yellow"},
            "cause": "Fungal infection encouraged by nutrient deficiency and prolonged leaf wetness.",
            "solution": "Apply a recommended fungicide spray and improve field nutrition.",
            "prevention_tips": [
                "Maintain balanced nitrogen and potassium fertilizer.",
                "Avoid stagnant moisture on leaf surfaces.",
                "Use clean seed and remove infected residue after harvest.",
            ],
        },
        {
            "name": "Leaf Blight",
            "signals": {"humid", "dark"},
            "cause": "Bacterial infection spreads quickly in warm, humid field conditions.",
            "solution": "Use copper-based bactericide support and avoid overhead irrigation.",
            "prevention_tips": [
                "Keep irrigation controlled during humid periods.",
                "Improve drainage and spacing for better airflow.",
                "Do not move infected plant debris across the field.",
            ],
        },
        {
            "name": "Rice Blast",
            "signals": {"dark", "stress"},
            "cause": "Fungal spores become active when leaf moisture stays high for long hours.",
            "solution": "Apply blast-management fungicide and reduce excess nitrogen input.",
            "prevention_tips": [
                "Monitor early lesions after rain and cloudy weather.",
                "Avoid excessive nitrogen application.",
                "Use tolerant seed varieties when possible.",
            ],
        },
    ],
    "wheat": [
        {
            "name": "Leaf Rust",
            "signals": {"brown"},
            "cause": "Fungal infection due to high humidity and repeated dew formation.",
            "solution": "Apply sulfur-based or triazole fungicide as per field recommendation.",
            "prevention_tips": [
                "Monitor humidity and leaf wetness during the week.",
                "Use resistant cultivars when available.",
                "Scout field edges to catch spread early.",
            ],
        },
        {
            "name": "Yellow Rust",
            "signals": {"yellow", "humid"},
            "cause": "Stripe rust develops fast under cool, humid weather conditions.",
            "solution": "Spray a suitable rust fungicide and inspect adjoining zones quickly.",
            "prevention_tips": [
                "Avoid delayed field scouting during cool mornings.",
                "Do not over-apply nitrogen late in the cycle.",
                "Remove volunteer host plants around the plot.",
            ],
        },
        {
            "name": "Powdery Mildew",
            "signals": {"white"},
            "cause": "Dense canopy and poor air circulation allow fungal powdering to spread.",
            "solution": "Apply mildew fungicide and open the canopy where possible.",
            "prevention_tips": [
                "Improve airflow between crop rows.",
                "Avoid overly dense plant stand.",
                "Monitor lower leaves after cloudy weather.",
            ],
        },
    ],
    "tomato": [
        {
            "name": "Early Blight",
            "signals": {"brown"},
            "cause": "Alternaria fungus spreads through infected debris and wet leaves.",
            "solution": "Apply recommended fungicide and remove infected lower leaves.",
            "prevention_tips": [
                "Mulch the base to reduce soil splash.",
                "Do not wet leaves late in the day.",
                "Rotate crops away from the same field next season.",
            ],
        },
        {
            "name": "Late Blight",
            "signals": {"dark", "humid"},
            "cause": "Cool, wet conditions trigger rapid blight development on foliage.",
            "solution": "Use blight-control fungicide immediately and isolate infected plants.",
            "prevention_tips": [
                "Keep foliage dry and improve ventilation.",
                "Remove infected plants before spread accelerates.",
                "Inspect after rainfall or fog-heavy nights.",
            ],
        },
        {
            "name": "Leaf Mold",
            "signals": {"yellow", "humid"},
            "cause": "High humidity and poor greenhouse or field airflow promote mold growth.",
            "solution": "Reduce humidity and spray a labeled fungicide if symptoms spread.",
            "prevention_tips": [
                "Improve air circulation across the canopy.",
                "Avoid excessive overhead irrigation.",
                "Remove infected foliage promptly.",
            ],
        },
    ],
    "potato": [
        {
            "name": "Early Blight",
            "signals": {"brown"},
            "cause": "Alternaria lesions expand when foliage remains wet and stressed.",
            "solution": "Use a preventive fungicide program and remove damaged leaves.",
            "prevention_tips": [
                "Keep nutrition balanced to reduce stress.",
                "Scout older leaves for early spotting.",
                "Rotate fields and manage debris after harvest.",
            ],
        },
        {
            "name": "Late Blight",
            "signals": {"dark", "humid"},
            "cause": "Late blight spreads quickly in cool, humid weather with long leaf wetness.",
            "solution": "Apply blight fungicide immediately and isolate affected foliage.",
            "prevention_tips": [
                "Do not leave infected foliage in the field.",
                "Increase scouting after rain or fog.",
                "Maintain clean irrigation scheduling.",
            ],
        },
        {
            "name": "Powdery Mildew",
            "signals": {"white"},
            "cause": "Surface fungal growth appears under shaded, stagnant air conditions.",
            "solution": "Use mildew-control spray and improve airflow across rows.",
            "prevention_tips": [
                "Avoid dense foliage without pruning.",
                "Track moisture build-up after irrigation.",
                "Inspect both leaf sides regularly.",
            ],
        },
    ],
    "maize": [
        {
            "name": "Common Rust",
            "signals": {"brown"},
            "cause": "Rust pustules develop after humid nights and mild daytime temperatures.",
            "solution": "Apply rust fungicide where severity is increasing in upper canopy leaves.",
            "prevention_tips": [
                "Monitor middle and upper leaves twice weekly.",
                "Choose resistant hybrids in high-risk zones.",
                "Avoid delaying field inspection after dew-heavy mornings.",
            ],
        },
        {
            "name": "Northern Leaf Blight",
            "signals": {"dark"},
            "cause": "Fungal blight spreads in humid weather and on wet leaf surfaces.",
            "solution": "Use a blight fungicide and check field blocks with dense canopy first.",
            "prevention_tips": [
                "Improve residue management after harvest.",
                "Rotate with non-host crops when possible.",
                "Monitor long lesions after rainfall periods.",
            ],
        },
        {
            "name": "Gray Leaf Spot",
            "signals": {"white", "humid"},
            "cause": "Warm, humid canopy conditions favor rectangular gray lesions.",
            "solution": "Apply labeled fungicide and reduce prolonged leaf wetness periods.",
            "prevention_tips": [
                "Improve airflow where planting density is high.",
                "Avoid late-day irrigation on foliage.",
                "Scout low-lying humid field sections early.",
            ],
        },
    ],
    "generic": [
        {
            "name": "Leaf Rust",
            "signals": {"brown"},
            "cause": "Fungal infection becomes active when humidity stays high around the canopy.",
            "solution": "Apply a suitable fungicide spray and monitor surrounding plants.",
            "prevention_tips": [
                "Avoid excess humidity around leaves.",
                "Improve field airflow wherever possible.",
                "Scout adjacent plants for early spread.",
            ],
        },
        {
            "name": "Leaf Blight",
            "signals": {"dark", "humid"},
            "cause": "Leaf blight expands when wet foliage remains untreated for too long.",
            "solution": "Use recommended fungicide or bactericide support based on field advice.",
            "prevention_tips": [
                "Do not overwater during humid days.",
                "Remove heavily infected leaves quickly.",
                "Sanitize tools used across crop rows.",
            ],
        },
        {
            "name": "Mosaic Disease",
            "signals": {"yellow", "stress"},
            "cause": "Virus-like leaf stress may spread through vectors and contaminated handling.",
            "solution": "Remove badly affected leaves and control vector insects immediately.",
            "prevention_tips": [
                "Maintain field hygiene and vector control.",
                "Use clean planting material.",
                "Inspect irregular yellow pattern spread weekly.",
            ],
        },
    ],
}

DISEASE_VISUAL_PROFILES = {
    "Brown Spot": {"brown": 3.1, "yellow": 2.6, "warm": 1.2, "stress": 1.0, "humid": 0.8},
    "Leaf Blight": {"dark": 3.0, "brown": 1.7, "stripe": 1.8, "edge": 1.0, "humid": 1.4, "heat": 0.7},
    "Rice Blast": {"dark": 2.6, "brown": 1.4, "stripe": 1.3, "stress": 1.4, "humid": 1.2},
    "Leaf Rust": {"brown": 3.2, "warm": 1.8, "humid": 1.0, "stress": 0.7},
    "Yellow Rust": {"yellow": 3.0, "brown": 1.0, "stripe": 1.4, "humid": 1.5, "cool": 1.3},
    "Powdery Mildew": {"white": 3.5, "gray": 1.5, "humid": 1.0, "green_loss": 0.7},
    "Early Blight": {"brown": 2.8, "dark": 1.4, "edge": 1.1, "stress": 1.3, "humid": 0.8},
    "Late Blight": {"dark": 3.1, "brown": 1.5, "stripe": 1.3, "humid": 1.6, "edge": 1.0},
    "Leaf Mold": {"yellow": 2.3, "white": 1.2, "gray": 1.3, "humid": 1.7, "green_loss": 0.9},
    "Common Rust": {"brown": 3.0, "warm": 1.7, "humid": 0.9, "stress": 0.7},
    "Northern Leaf Blight": {"dark": 3.0, "stripe": 1.7, "brown": 1.1, "humid": 1.2},
    "Gray Leaf Spot": {"gray": 3.0, "white": 1.4, "stripe": 1.2, "humid": 1.3},
    "Mosaic Disease": {"yellow": 2.8, "mottle": 2.4, "green_loss": 1.0, "stress": 1.2},
}


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(100))
    password = db.Column(db.String(255))
    location = db.Column(db.String(100))
    crop_type = db.Column(db.String(100))
    farm_size = db.Column(db.String(50))
    profile_photo = db.Column(db.String(200))
    phone = db.Column(db.String(20))
    
    # Subscription & Trial
    is_pro = db.Column(db.Boolean, default=False)
    plan = db.Column(db.String(16), default="free")
    trial_start_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    subscription_start_date = db.Column(db.DateTime, nullable=True)
    subscription_end_date = db.Column(db.DateTime, nullable=True)
    
    # Referral System
    referral_code = db.Column(db.String(20), unique=True)
    referred_by = db.Column(db.String(20), nullable=True)
    loyalty_points = db.Column(db.Integer, default=0)
    wallet_balance = db.Column(db.Integer, default=0)

    disease_histories = db.relationship('DiseaseHistory', backref='user', lazy=True)
    farms = db.relationship('Farm', backref='user', lazy=True, cascade="all, delete-orphan")
    farm_tasks = db.relationship('FarmTask', backref='user', lazy=True, cascade="all, delete-orphan")
    preferences = db.relationship('UserPreference', backref='user', uselist=False, lazy=True, cascade="all, delete-orphan")
    store_orders = db.relationship('StoreOrder', backref='buyer', lazy=True, cascade="all, delete-orphan")

    def __init__(self, **kwargs):
        super(User, self).__init__(**kwargs)


class AdminUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="admin")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __init__(self, **kwargs):
        super(AdminUser, self).__init__(**kwargs)

class DiseaseHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    crop_type = db.Column(db.String(100))
    detected_disease = db.Column(db.String(100))
    confidence = db.Column(db.Integer)
    date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __init__(self, **kwargs):
        super(DiseaseHistory, self).__init__(**kwargs)

class CarbonRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    crop_type = db.Column(db.String(100))
    farm_size = db.Column(db.Float)
    co2_sequestered = db.Column(db.Float) # in tonnes
    carbon_credits = db.Column(db.Float)
    date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __init__(self, **kwargs):
        super(CarbonRecord, self).__init__(**kwargs)

class CommunityPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200))
    content = db.Column(db.Text)
    category = db.Column(db.String(50)) # e.g., 'Disease', 'Market', 'Tips'
    date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    comments = db.relationship('CommunityComment', backref='post', lazy=True)

    def __init__(self, **kwargs):
        super(CommunityPost, self).__init__(**kwargs)

class CommunityComment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('community_post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text)
    date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __init__(self, **kwargs):
        super(CommunityComment, self).__init__(**kwargs)

class Farm(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    location = db.Column(db.String(120))
    crop_type = db.Column(db.String(100))
    farm_size = db.Column(db.String(50))
    notes = db.Column(db.Text)
    is_primary = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    tasks = db.relationship('FarmTask', backref='farm', lazy=True, cascade="all, delete-orphan")

    def __init__(self, **kwargs):
        super(Farm, self).__init__(**kwargs)


class FarmTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    farm_id = db.Column(db.Integer, db.ForeignKey('farm.id'))
    title = db.Column(db.String(160), nullable=False)
    details = db.Column(db.Text)
    category = db.Column(db.String(50), default="General")
    priority = db.Column(db.String(20), default="medium")
    status = db.Column(db.String(20), default="todo")
    due_date = db.Column(db.Date)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __init__(self, **kwargs):
        super(FarmTask, self).__init__(**kwargs)


class UserPreference(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    crop_alerts = db.Column(db.Boolean, default=True)
    disease_alerts = db.Column(db.Boolean, default=True)
    weather_alerts = db.Column(db.Boolean, default=False)
    data_updates = db.Column(db.Boolean, default=True)
    email_alerts = db.Column(db.Boolean, default=True)
    sms_alerts = db.Column(db.Boolean, default=False)
    daily_briefing = db.Column(db.Boolean, default=True)
    alert_email = db.Column(db.String(100))
    alert_phone = db.Column(db.String(20))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def __init__(self, **kwargs):
        super(UserPreference, self).__init__(**kwargs)


class StoreProduct(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(160), unique=True, nullable=False)
    name = db.Column(db.String(180), nullable=False)
    category = db.Column(db.String(50), nullable=False, index=True)
    price = db.Column(db.Integer, nullable=False)
    mrp = db.Column(db.Integer, nullable=False)
    discount_pct = db.Column(db.Integer, default=0)
    rating = db.Column(db.Float, default=4.0)
    image_url = db.Column(db.String(600))
    description = db.Column(db.Text)
    seller = db.Column(db.String(120))
    unit = db.Column(db.String(60))
    stock = db.Column(db.Integer, default=0)
    tags_json = db.Column(db.Text, default="[]")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    orders = db.relationship('StoreOrder', backref='product', lazy=True, cascade="all, delete-orphan")

    def __init__(self, **kwargs):
        super(StoreProduct, self).__init__(**kwargs)


class DiseaseProductMapping(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    disease_key = db.Column(db.String(180), unique=True, nullable=False, index=True)
    disease_label = db.Column(db.String(180), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("store_product.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    product = db.relationship("StoreProduct", lazy=True)

    def __init__(self, **kwargs):
        super(DiseaseProductMapping, self).__init__(**kwargs)


class StoreOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('store_product.id'), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(8), default=RAZORPAY_CURRENCY)
    status = db.Column(db.String(20), default="created")
    checkout_mode = db.Column(db.String(20), default="demo")
    source = db.Column(db.String(50), default="store")
    razorpay_order_id = db.Column(db.String(120))
    razorpay_payment_id = db.Column(db.String(120))
    razorpay_signature = db.Column(db.String(255))
    notes_json = db.Column(db.Text, default="{}")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __init__(self, **kwargs):
        super(StoreOrder, self).__init__(**kwargs)


class WalletTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    direction = db.Column(db.String(12), default="credit")  # credit | debit
    amount_inr = db.Column(db.Integer, default=0)
    reason = db.Column(db.String(60), default="")
    meta_json = db.Column(db.Text, default="{}")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __init__(self, **kwargs):
        super(WalletTransaction, self).__init__(**kwargs)


class ReferralReward(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    referrer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    new_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True, index=True)
    referrer_reward_inr = db.Column(db.Integer, default=20)
    new_user_bonus_inr = db.Column(db.Integer, default=10)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __init__(self, **kwargs):
        super(ReferralReward, self).__init__(**kwargs)


class SubscriptionPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    plan = db.Column(db.String(16), default="pro")
    amount_inr = db.Column(db.Integer, default=0)  # plan price
    wallet_used_inr = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default="created")  # created|pending|paid|failed|demo
    razorpay_order_id = db.Column(db.String(120))
    razorpay_payment_id = db.Column(db.String(120))
    razorpay_signature = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __init__(self, **kwargs):
        super(SubscriptionPayment, self).__init__(**kwargs)


def generate_otp():
    return "".join([str(random.randint(0, 9)) for _ in range(6)])


def compute_otp_signature(otp, target_email, otp_type):
    secret = app.secret_key or ""
    if isinstance(secret, str):
        secret_bytes = secret.encode("utf-8", errors="ignore")
    else:
        secret_bytes = bytes(secret)

    payload = f"{str(otp_type or '').strip().lower()}|{str(target_email or '').strip().lower()}|{str(otp or '').strip()}"
    return hmac.new(secret_bytes, payload.encode("utf-8", errors="ignore"), sha256).hexdigest()


def verify_otp_signature(user_otp, expected_signature, target_email, otp_type):
    expected = str(expected_signature or "").strip().lower()
    if not expected:
        return False
    actual = compute_otp_signature(user_otp, target_email, otp_type).strip().lower()
    return hmac.compare_digest(actual, expected)


def is_password_hash(password_value):
    value = (password_value or "").strip()
    return value.startswith("pbkdf2:") or value.startswith("scrypt:")


def hash_password(password_value):
    return generate_password_hash(password_value, method="pbkdf2:sha256", salt_length=16)


def generate_unique_referral_code():
    while True:
        code = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=8))
        if User.query.filter_by(referral_code=code).first() is None:
            return code


def normalize_plan_name(value):
    plan = str(value or "").strip().lower()
    return plan if plan in SUBSCRIPTION_PLANS else "free"


def plan_rank(plan_name):
    return int(SUBSCRIPTION_PLANS.get(normalize_plan_name(plan_name), SUBSCRIPTION_PLANS["free"]).get("rank", 0))


def is_trial_active(user):
    if user is None or not user.trial_start_date:
        return False
    try:
        trial_end = user.trial_start_date.replace(tzinfo=timezone.utc) + timedelta(days=SUBSCRIPTION_TRIAL_DAYS)
    except Exception:
        return False
    return datetime.now(timezone.utc) <= trial_end


def is_paid_subscription_active(user):
    if user is None:
        return False
    end_date = user.subscription_end_date
    if not end_date:
        return False
    try:
        end_date = end_date.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return datetime.now(timezone.utc) <= end_date


def sync_legacy_pro_flag(user):
    if user is None:
        return
    active_paid = is_paid_subscription_active(user)
    user.is_pro = bool(normalize_plan_name(user.plan) in {"pro", "premium"} and active_paid)


def ensure_user_subscription_state(user, commit=False):
    """Auto-downgrade expired paid plans to free (keeps trial separate)."""
    if user is None:
        return

    # Backward-compat: legacy pro users without a plan should map to Pro.
    if normalize_plan_name(user.plan) == "free" and bool(user.is_pro) and is_paid_subscription_active(user):
        user.plan = "pro"

    if normalize_plan_name(user.plan) in {"pro", "premium"} and not is_paid_subscription_active(user):
        user.plan = "free"
        user.subscription_start_date = None
        user.subscription_end_date = None

    sync_legacy_pro_flag(user)
    if commit:
        db.session.commit()


def wallet_credit(user, amount_inr, reason, meta=None):
    amount = int(amount_inr or 0)
    if user is None or amount <= 0:
        return
    user.wallet_balance = int(user.wallet_balance or 0) + amount
    tx = WalletTransaction(  # type: ignore
        user_id=user.id,
        direction="credit",
        amount_inr=amount,
        reason=str(reason or "").strip(),
        meta_json=json.dumps(meta or {}, ensure_ascii=False),
    )
    db.session.add(tx)


def wallet_debit(user, amount_inr, reason, meta=None):
    amount = int(amount_inr or 0)
    if user is None or amount <= 0:
        return False
    balance = int(user.wallet_balance or 0)
    if balance < amount:
        return False
    user.wallet_balance = balance - amount
    tx = WalletTransaction(  # type: ignore
        user_id=user.id,
        direction="debit",
        amount_inr=amount,
        reason=str(reason or "").strip(),
        meta_json=json.dumps(meta or {}, ensure_ascii=False),
    )
    db.session.add(tx)
    return True


def require_plan(min_plan_name):
    min_plan = normalize_plan_name(min_plan_name)

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_current_user()
            if not user:
                return redirect("/login")

            ensure_user_subscription_state(user, commit=True)
            user_plan = normalize_plan_name(user.plan)

<<<<<<< HEAD
            # Free plan always has access to non-premium routes.
            if plan_rank(user_plan) >= plan_rank(min_plan) and is_paid_subscription_active(user):
                return f(*args, **kwargs)

            # Trial acts like Pro access for a limited window.
            if min_plan == "pro" and is_trial_active(user):
=======
            # Trial is full-access for a limited window (all plans, including premium).
            if is_trial_active(user):
                return f(*args, **kwargs)

            # Paid access (after trial) requires an active subscription.
            if plan_rank(user_plan) >= plan_rank(min_plan) and is_paid_subscription_active(user):
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
                return f(*args, **kwargs)

            return redirect("/subscriptions?required=1")

        return decorated_function

    return decorator


def check_subscription(f):
    return require_plan("pro")(f)


<<<<<<< HEAD
=======
def is_user_paywall_blocked(user):
    """After the free trial ends, block access unless the user has an active paid plan."""
    if user is None:
        return False
    ensure_user_subscription_state(user, commit=True)
    if is_trial_active(user):
        return False
    return not (normalize_plan_name(user.plan) in {"pro", "premium"} and is_paid_subscription_active(user))


def is_paywall_exempt_path(path_value):
    path = str(path_value or "")
    if not path or path == "/":
        return True

    exact_allow = {
        "/login",
        "/register",
        "/verify-otp",
        "/logout",
        "/subscriptions",
    }
    if path in exact_allow:
        return True

    prefix_allow = (
        "/static/",
        "/admin",
        "/webhooks/",
        "/api/subscription/",
        "/api/apply-wallet",
    )
    return any(path.startswith(prefix) for prefix in prefix_allow)


@app.before_request
def enforce_trial_paywall():
    """Give 7-day full access; after that redirect to subscriptions until paid."""
    # Skip preflight/health.
    if request.method == "OPTIONS":
        return None

    if is_paywall_exempt_path(request.path):
        return None

    user = get_current_user()
    if not user:
        return None

    if not is_user_paywall_blocked(user):
        return None

    # For API-like routes, return JSON so frontend can handle gracefully.
    if request.path.startswith("/api/") or request.path == "/predict-disease":
        return jsonify(
            {
                "success": False,
                "error": "Trial ended. Please subscribe to continue using AgroVision AI.",
                "redirect_url": "/subscriptions?expired=1",
            }
        ), 403

    return redirect("/subscriptions?expired=1")


>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
def check_user_password(user, password_value, upgrade_legacy=True):
    if user is None:
        return False, False

    stored_password = user.password or ""
    candidate = password_value or ""
    if not stored_password or not candidate:
        return False, False

    if is_password_hash(stored_password):
        return check_password_hash(stored_password, candidate), False

    if stored_password == candidate:
        if upgrade_legacy:
            user.password = hash_password(candidate)
            return True, True
        return True, False

    return False, False


def check_admin_password(candidate_password):
<<<<<<< HEAD
    stored = (ADMIN_PASSWORD or "").strip()
    candidate = (candidate_password or "").strip()
    if not stored or not candidate:
        return False

    if is_password_hash(stored):
        return check_password_hash(stored, candidate)

=======
    candidate = (candidate_password or "").strip()
    if not candidate:
        return False

    # Prefer DB-backed admin users when available.
    try:
        admin = AdminUser.query.filter_by(email=ADMIN_EMAIL).first()  # type: ignore
    except Exception:
        admin = None

    if admin is not None:
        return check_password_hash(admin.password_hash or "", candidate)

    # Fallback to env-based credentials (backward compatible).
    stored = (ADMIN_PASSWORD or "").strip()
    if not stored:
        return False
    if is_password_hash(stored):
        return check_password_hash(stored, candidate)
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    return stored == candidate


def is_admin_authenticated():
<<<<<<< HEAD
    return bool(session.get("admin_authed") and session.get("admin_email") == ADMIN_EMAIL)
=======
    if not session.get("admin_authed"):
        return False
    email = (session.get("admin_email") or "").strip().lower()
    if email and email == ADMIN_EMAIL:
        return True
    # If multiple admins are added, fall back to ID check.
    try:
        admin_id = int(session.get("admin_id") or 0)
    except (TypeError, ValueError):
        admin_id = 0
    if not admin_id:
        return False
    try:
        return db.session.get(AdminUser, admin_id) is not None
    except Exception:
        return False
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_admin_authenticated():
            return redirect("/admin/login")
        return f(*args, **kwargs)

    return decorated


def clear_otp_session_state():
    for key in (
        "otp",
        "otp_sig",
        "otp_target",
        "otp_type",
        "otp_user_id",
        "otp_expiry",
<<<<<<< HEAD
        "otp_notice",
        "otp_debug_available",
        "otp_sent_at",
=======
        "otp_attempts",
        "otp_notice",
        "otp_dev_code",
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
        "pending_user",
    ):
        session.pop(key, None)


<<<<<<< HEAD
def csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = f"{uuid.uuid4().hex}{uuid.uuid4().hex}"
        session[CSRF_SESSION_KEY] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


def require_csrf():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    expected = str(session.get(CSRF_SESSION_KEY) or "")
    provided = str(
        request.form.get("csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or ""
    )
    cookie_token = str(request.cookies.get(CSRF_COOKIE_NAME) or "")

    if (
        expected
        and provided
        and cookie_token
        and hmac.compare_digest(expected, provided)
        and hmac.compare_digest(expected, cookie_token)
    ):
        return None

    return Response("CSRF validation failed.", status=400)


def update_otp_session_state(otp, target_email, otp_type, *, user_id=None, email_sent=True, notice=None):
    session["otp"] = otp
    session["otp_target"] = str(target_email or "").strip().lower()
    session["otp_type"] = str(otp_type or "").strip().lower()
    session["otp_expiry"] = (datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)).timestamp()
    session["otp_sent_at"] = time.time()
    session["otp_notice"] = str(notice or "").strip()
    session["otp_debug_available"] = bool(not email_sent and OTP_DEBUG_FALLBACK_ENABLED)

    if user_id is None:
        session.pop("otp_user_id", None)
    else:
        session["otp_user_id"] = int(user_id)


def build_otp_notice(email_sent, failure_reason=None):
    if email_sent:
        return f"Verification code sent successfully. The code stays valid for {OTP_EXPIRY_MINUTES} minutes."

    if OTP_DEBUG_FALLBACK_ENABLED:
        return (
            "Email delivery failed in this environment, so a local fallback OTP is shown below for testing."
        )

    if failure_reason:
        return "We could not send the OTP email right now. Please try again after checking the mail settings."

    return "We could not send the OTP email right now. Please try again."


def get_otp_page_context(error=None, notice=None):
    return {
        "target": session.get("otp_target"),
        "error": error,
        "notice": session.get("otp_notice") if notice is None else notice,
        "dev_otp": session.get("otp") if session.get("otp_debug_available") else None,
    }


@app.after_request
def sync_csrf_cookie(response):
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = csrf_token()

    if request.cookies.get(CSRF_COOKIE_NAME) != token:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            token,
            secure=app.config["SESSION_COOKIE_SECURE"],
            httponly=False,
            samesite=app.config["SESSION_COOKIE_SAMESITE"],
        )
    return response
=======
def read_upload_bytes(file_storage, max_bytes, label="file"):
    if file_storage is None:
        raise ValueError("No file uploaded.")

    try:
        stream = file_storage.stream
    except Exception as exc:
        raise ValueError("Upload stream is not available.") from exc

    try:
        stream.seek(0)
    except Exception:
        pass

    data = stream.read(int(max_bytes) + 1)
    if not data:
        raise ValueError("Uploaded file is empty.")
    if len(data) > int(max_bytes):
        raise ValueError(f"{label} is too large. Please upload a smaller file.")
    return data
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057


def save_profile_photo_upload(file_storage, prefix):
    original_name = secure_filename(file_storage.filename or "")
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("Only PNG, JPG, JPEG, or WEBP images are allowed.")

    file_name = f"{prefix}_{uuid.uuid4().hex[:12]}.jpg"
    save_path = UPLOADS_DIR / file_name

    image_bytes = read_upload_bytes(file_storage, MAX_PROFILE_PHOTO_BYTES, label="Profile photo")

    try:
        img = Image.open(BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img = ImageOps.fit(img, (512, 512), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        img.save(save_path, format="JPEG", quality=86, optimize=True, progressive=True)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Could not process profile photo. Please upload a valid image file.") from exc

    return file_name


def save_product_image_upload(file_storage, slug_hint="product"):
    """Save an admin-uploaded product image into /static/products and return its public URL."""
    original_name = secure_filename(file_storage.filename or "")
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("Only PNG, JPG, JPEG, or WEBP images are allowed.")

    safe_hint = slugify_crop_name(slug_hint or "product")[:28] or "product"
    file_name = f"{safe_hint}_{uuid.uuid4().hex[:12]}.jpg"
    save_path = PRODUCTS_UPLOAD_DIR / file_name

    # Normalize all uploads to a square JPEG for consistent store UI.
    try:
<<<<<<< HEAD
        img = Image.open(file_storage.stream)
=======
        image_bytes = read_upload_bytes(file_storage, MAX_PRODUCT_IMAGE_BYTES, label="Product image")
        img = Image.open(BytesIO(image_bytes))
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img = ImageOps.fit(img, (900, 900), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        img.save(save_path, format="JPEG", quality=86, optimize=True, progressive=True)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Could not process image upload. Please upload a valid image file.") from exc

    return f"/static/products/{file_name}"


# SMTP Configuration
APP_DISPLAY_NAME = (os.getenv("APP_NAME") or "AgroVisionAI").strip() or "AgroVisionAI"
SMTP_SERVER = (os.getenv("SMTP_SERVER") or "smtp.gmail.com").strip()
SMTP_PORT = get_env_int("SMTP_PORT", 587)
SMTP_EMAIL = (os.getenv("SMTP_EMAIL") or "").strip()
_smtp_password_raw = (os.getenv("SMTP_PASSWORD") or "").strip()
SMTP_PASSWORD = (
    _smtp_password_raw.replace(" ", "")
    if SMTP_SERVER.lower() == "smtp.gmail.com"
    and " " in _smtp_password_raw
    and len(_smtp_password_raw.replace(" ", "")) == 16
    else _smtp_password_raw
)
SMTP_SENDER_NAME = (os.getenv("SMTP_SENDER_NAME") or APP_DISPLAY_NAME).strip() or APP_DISPLAY_NAME
SMTP_USE_SSL = (os.getenv("SMTP_USE_SSL") or "").strip().lower() in {"1", "true", "yes", "on"}
SMTP_TIMEOUT_SECONDS = get_env_int("SMTP_TIMEOUT_SECONDS", 20)
OTP_EMAIL_EMBED_LOGO = (os.getenv("OTP_EMAIL_EMBED_LOGO") or "").strip().lower() in {"1", "true", "yes", "on"}


def load_email_logo_bytes():
    if not EMAIL_LOGO_PATH.exists():
        return None

    try:
        return EMAIL_LOGO_PATH.read_bytes()
    except OSError:
        return None


def build_otp_email_text(otp):
    return (
        f"{APP_DISPLAY_NAME} Verification Code\n\n"
        f"Your one-time verification code is: {otp}\n\n"
        "This code is valid for the next 5 minutes.\n"
        "Do not share this code with anyone.\n"
        "If you did not request this code, you can safely ignore this email.\n\n"
        f"- Team {APP_DISPLAY_NAME}\n"
    )


def build_otp_email_html(otp, logo_cid=None):
    logo_markup = ""
    if logo_cid:
        logo_markup = (
            f'<img src="cid:{logo_cid}" alt="{APP_DISPLAY_NAME}" '
            'style="display:block;width:44px;height:44px;border-radius:10px;margin-right:12px;">'
        )

    preheader = f"Your {APP_DISPLAY_NAME} verification code is {otp}. Valid for 5 minutes."

    return f"""\
<!DOCTYPE html>
<html lang="en">
  <body style="margin:0;padding:0;background:#f6f8fb;font-family:Manrope,Segoe UI,Arial,sans-serif;color:#0b1b2b;">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      {preheader}
    </div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f6f8fb;padding:28px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border:1px solid #dbe3ee;border-radius:18px;overflow:hidden;">
            <tr>
              <td style="padding:18px 20px;background:#0b1b2b;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                  <tr>
                    <td style="vertical-align:middle;">
                      <div style="display:flex;align-items:center;">
                        {logo_markup}
                        <div style="color:#ffffff;">
                          <div style="font-size:16px;font-weight:900;letter-spacing:0.01em;">{APP_DISPLAY_NAME}</div>
                          <div style="font-size:12px;opacity:0.85;font-weight:700;">Verification code</div>
                        </div>
                      </div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:18px 20px 8px;">
                <h1 style="margin:0 0 8px;font-size:22px;line-height:1.25;font-weight:900;color:#0b1b2b;">
                  Use this code to continue
                </h1>
                <p style="margin:0;color:#30455b;font-size:14px;line-height:1.65;">
                  Enter the verification code below to complete your sign in. This code expires in 5 minutes.
                </p>
              </td>
            </tr>
            <tr>
              <td style="padding:8px 20px 10px;">
                <div style="border:1px solid #dbe3ee;border-radius:14px;background:#f6f8fb;padding:14px 14px;">
                  <div style="font-size:12px;letter-spacing:0.12em;text-transform:uppercase;font-weight:900;color:#54708b;margin-bottom:8px;">
                    Verification code
                  </div>
                  <div style="font-size:28px;font-weight:900;letter-spacing:0.10em;color:#0b1b2b;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,Liberation Mono,monospace;">
                    {otp}
                  </div>
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:6px 20px 18px;">
                <p style="margin:0;color:#30455b;font-size:13px;line-height:1.65;">
                  Security note: Do not share this code with anyone. If you did not request it, you can ignore this email.
                </p>
              </td>
            </tr>
            <tr>
              <td style="padding:12px 20px;background:#f6f8fb;border-top:1px solid #dbe3ee;">
                <div style="font-size:12px;color:#54708b;line-height:1.6;">
                  Sent by {APP_DISPLAY_NAME}. This is an automated message.
                </div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
""".strip()


def send_otp_email(target_email, otp):
    """
    Sends a 6-digit OTP to the user's email using SMTP.
    """
    if not SMTP_EMAIL or not SMTP_PASSWORD:
<<<<<<< HEAD
        print("SMTP credentials are not configured. OTP email skipped.")
        return False, "SMTP credentials are not configured."
=======
        return False
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057

    logo_bytes = None
    logo_cid = None
    if OTP_EMAIL_EMBED_LOGO:
        logo_bytes = load_email_logo_bytes()
        if logo_bytes:
            logo_cid = make_msgid(domain="agrovisionai.local")[1:-1]

    msg = EmailMessage()
    msg["Subject"] = f"{APP_DISPLAY_NAME} verification code: {otp}"
    msg["From"] = formataddr((SMTP_SENDER_NAME, SMTP_EMAIL))
    msg["To"] = target_email
    msg["Reply-To"] = SMTP_EMAIL
    msg["X-Auto-Response-Suppress"] = "OOF, AutoReply"
    msg.set_content(build_otp_email_text(otp))
    msg.add_alternative(build_otp_email_html(otp, logo_cid=logo_cid), subtype="html")

    if logo_bytes and logo_cid:
        html_part = msg.get_payload()[-1]
        html_part.add_related(
            logo_bytes,
            maintype="image",
            subtype=EMAIL_LOGO_SUBTYPE,
            cid=f"<{logo_cid}>",
            filename=EMAIL_LOGO_FILENAME,
        )

    try:
        if SMTP_USE_SSL or SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.send_message(msg)
        return True, None
    except Exception as e:
        print(f"SMTP Error: {e}")
        return False, str(e)


def send_admin_order_email(order, user, product):
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print("SMTP credentials are not configured. Admin order email skipped.")
        return False

    if not ADMIN_NOTIFY_EMAIL:
        print("Admin notification email is not configured. Admin order email skipped.")
        return False

    order_id = getattr(order, "id", None)
    amount_paise = int(getattr(order, "amount", 0) or 0)
    amount_inr = amount_paise / 100.0
    currency = str(getattr(order, "currency", "INR") or "INR")
    buyer_name = str(getattr(user, "name", "") or "").strip() or "Customer"
    buyer_email = str(getattr(user, "email", "") or "").strip()
    product_name = str(getattr(product, "name", "") or "").strip() or "Store Product"

    subject = f"New Order Received #{order_id}" if order_id else "New Order Received"
    body_lines = [
        "New Order Received!",
        "",
        f"Order: #{order_id}" if order_id else "Order: (unknown id)",
        f"Product: {product_name}",
        f"User: {buyer_name}" + (f" ({buyer_email})" if buyer_email else ""),
        f"Amount: {currency} {amount_inr:.2f}",
        f"Status: {get_fulfillment_status(order).title()}",
    ]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((SMTP_SENDER_NAME, SMTP_EMAIL))
    msg["To"] = ADMIN_NOTIFY_EMAIL
    msg["Reply-To"] = SMTP_EMAIL
    msg.set_content("\n".join(body_lines))

    try:
        if SMTP_USE_SSL or SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.send_message(msg)
        return True
    except Exception as e:
        print(f"SMTP Error (admin order email): {e}")
        return False


def send_admin_order_email(order, user, product):
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print("SMTP credentials are not configured. Admin order email skipped.")
        return False

    if not ADMIN_NOTIFY_EMAIL:
        print("Admin notification email is not configured. Admin order email skipped.")
        return False

    order_id = getattr(order, "id", None)
    amount_paise = int(getattr(order, "amount", 0) or 0)
    amount_inr = amount_paise / 100.0
    currency = str(getattr(order, "currency", "INR") or "INR")
    buyer_name = str(getattr(user, "name", "") or "").strip() or "Customer"
    buyer_email = str(getattr(user, "email", "") or "").strip()
    product_name = str(getattr(product, "name", "") or "").strip() or "Store Product"

    subject = f"New Order Received #{order_id}" if order_id else "New Order Received"
    body_lines = [
        "New Order Received!",
        "",
        f"Order: #{order_id}" if order_id else "Order: (unknown id)",
        f"Product: {product_name}",
        f"User: {buyer_name}" + (f" ({buyer_email})" if buyer_email else ""),
        f"Amount: {currency} {amount_inr:.2f}",
        f"Status: {get_fulfillment_status(order).title()}",
    ]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((SMTP_SENDER_NAME, SMTP_EMAIL))
    msg["To"] = ADMIN_NOTIFY_EMAIL
    msg["Reply-To"] = SMTP_EMAIL
    msg.set_content("\n".join(body_lines))

    try:
        if SMTP_USE_SSL or SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.send_message(msg)
        return True
    except Exception as e:
        print(f"SMTP Error (admin order email): {e}")
        return False


def clamp(value, lower, upper):
    return max(lower, min(value, upper))


def _client_ip():
    # If behind a proxy, set proper proxy headers in production (not enabled by default).
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown").split(",")[0].strip()


def rate_limit_exceeded(bucket_key, max_hits, window_seconds):
    """Return True if exceeded, else False."""
    now = time.time()
    window = float(window_seconds or 1)
    limit = int(max_hits or 1)
    key = str(bucket_key or "")
    if not key:
        return False

    hits = RATE_LIMIT_BUCKET.get(key) or []
    hits = [ts for ts in hits if now - float(ts) <= window]
    if len(hits) >= limit:
        RATE_LIMIT_BUCKET[key] = hits
        return True

    hits.append(now)
    RATE_LIMIT_BUCKET[key] = hits
    return False

def calculate_carbon_credits(user):
    """
    Simulates carbon credit calculation based on farm size and crop type.
    """
    # Logic: 1 hectare of farm sequestrates roughly 1-3 tonnes of CO2 per year depending on crop
    farm_size_str = user.farm_size or "1"
    try:
        # Extract number from "5 Acres" or "2 Hectares"
        size = float(farm_size_str.split()[0])
        # Convert to hectares if needed (rough estimation)
        if "acre" in farm_size_str.lower():
            size = size * 0.404
    except:
        size = 1.0
        
    # Standard rates (simulated)
    crop_multipliers = {
        "rice": 1.8,
        "wheat": 2.2,
        "maize": 2.5,
        "sugarcane": 3.5,
        "generic": 2.0
    }
    
    crop_type = (user.crop_type or "generic").lower()
    multiplier = crop_multipliers.get(crop_type, 2.0)
    
    total_co2 = size * multiplier
    credits = total_co2 * 0.85 # Efficiency factor
    
    return {
        "co2_tonnes": round(total_co2, 2), # type: ignore
        "credits": round(credits, 1), # type: ignore
        "impact_level": "Outstanding" if credits > 10 else ("Significant" if credits > 5 else "Good")
    }


def build_mock_mandi_rates(location):
    market_location = location or "India"
    return [
        {"crop": "Paddy (Rice)", "price": 2183, "unit": "Quintal", "change": "+1.2%", "trend": "up", "mandi": f"{market_location} Central"},
        {"crop": "Wheat", "price": 2425, "unit": "Quintal", "change": "-0.5%", "trend": "down", "mandi": f"{market_location} Market"},
        {"crop": "Maize", "price": 1960, "unit": "Quintal", "change": "+2.1%", "trend": "up", "mandi": f"{market_location} Grain Yard"},
        {"crop": "Mustard", "price": 5450, "unit": "Quintal", "change": "+0.8%", "trend": "up", "mandi": f"{market_location} APMC"},
    ]


def load_kisan_dost_knowledge():
    if not KISAN_DOST_KNOWLEDGE_PATH.exists():
        return []

    try:
        data = json.loads(KISAN_DOST_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []


<<<<<<< HEAD
def load_ai_crop_doctor_faq_reference():
    if not AI_CROP_DOCTOR_FAQ_PATH.exists():
        return ""

    try:
        return AI_CROP_DOCTOR_FAQ_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_ai_crop_doctor_faq_entries():
    reference = load_ai_crop_doctor_faq_reference()
    if not reference:
        return []

    entries = []
    current_question = None
    current_answer_lines = []

    for raw_line in reference.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if re.match(r"^\d+\.\s+.+\?$", line):
            if current_question and current_answer_lines:
                entries.append(
                    {
                        "question": current_question,
                        "answer": " ".join(current_answer_lines).strip(),
                    }
                )
            current_question = re.sub(r"^\d+\.\s*", "", line).strip()
            current_answer_lines = []
            continue

        if current_question:
            current_answer_lines.append(line)

    if current_question and current_answer_lines:
        entries.append(
            {
                "question": current_question,
                "answer": " ".join(current_answer_lines).strip(),
            }
        )

    return entries


def load_ai_crop_doctor_local_qa():
    if not AI_CROP_DOCTOR_LOCAL_QA_PATH.exists():
        return []

    try:
        data = json.loads(AI_CROP_DOCTOR_LOCAL_QA_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []


def get_ai_crop_doctor_local_answer(entry, language="Hinglish"):
    if not isinstance(entry, dict):
        return None

    language_map = {
        "english": ["answer_en", "english_answer", "answer"],
        "hindi": ["answer_hi", "hindi_answer", "answer"],
        "odia": ["answer_od", "odia_answer", "answer"],
        "hinglish": ["answer", "answer_hi", "answer_en"],
    }
    keys = language_map.get(str(language or "").strip().lower(), ["answer"])
    for key in keys:
        value = str(entry.get(key) or "").strip()
        if value:
            return value
    return None


AI_CROP_DOCTOR_LOCAL_QA_STOPWORDS = {
    "a", "an", "the", "is", "am", "are", "was", "were", "be", "to", "of", "and", "or",
    "me", "my", "mere", "meri", "mera", "mujhe", "hai", "ho", "hota", "raha", "rahe",
    "rahi", "kya", "ka", "ki", "ke", "ko", "se", "par", "per", "aur", "best", "kaunsa",
    "kaunse", "kitna", "kitni", "kitne", "dena", "use", "karu", "kare", "karna", "kab",
    "kaise", "kar", "do", "diya", "gaya", "hoon", "hu", "mein", "main", "please", "help",
    "what", "when", "where", "which", "who", "why", "how", "in", "on", "for",
}

AI_CROP_DOCTOR_LOCAL_QA_TOKEN_ALIASES = {
    "patta": "leaf",
    "patte": "leaf",
    "patti": "leaf",
    "leaves": "leaf",
    "daag": "spots",
    "dhabba": "spots",
    "dhabbe": "spots",
    "spot": "spots",
    "bhura": "brown",
    "bhure": "brown",
    "bhoora": "brown",
    "bhoore": "brown",
    "peela": "yellow",
    "peele": "yellow",
    "pila": "yellow",
    "pile": "yellow",
    "safed": "white",
    "kaala": "black",
    "kaali": "black",
    "kaale": "black",
    "jad": "root",
    "jaden": "root",
    "jadon": "root",
    "roots": "root",
    "keeda": "pest",
    "keede": "pest",
    "insects": "pest",
    "bug": "pest",
    "bugs": "pest",
    "paani": "water",
    "sinchai": "irrigation",
    "khad": "fertilizer",
    "dawai": "spray",
    "murjha": "wilt",
    "murjhaya": "wilt",
    "murjhane": "wilt",
    "wilted": "wilt",
    "wilting": "wilt",
    "sookh": "dry",
    "sukh": "dry",
    "sukha": "dry",
    "sukhi": "dry",
    "sukhe": "dry",
    "drying": "dry",
    "powdery": "powder",
}

AI_CROP_DOCTOR_SYMPTOM_CUES = {
    "yellow", "brown", "white", "black", "leaf", "root", "spots", "powder", "curl",
    "holes", "wilt", "dry", "pest", "fungal", "fungus", "rot", "infection", "burn",
}

AI_CROP_DOCTOR_LOW_SIGNAL_TOKENS = {
    "agriculture", "farming", "farm", "crop", "plant", "technology", "system",
}


def normalize_ai_crop_doctor_match_text(text):
    tokens = []
    for raw_token in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        token = AI_CROP_DOCTOR_LOCAL_QA_TOKEN_ALIASES.get(raw_token, raw_token)
        if token.endswith("s") and len(token) > 4 and token not in {"ph", "tips"}:
            token = token[:-1]
        tokens.append(token)
    return " ".join(tokens).strip()


def extract_ai_crop_doctor_match_tokens(text):
    normalized_text = normalize_ai_crop_doctor_match_text(text)
    return {
        token
        for token in normalized_text.split()
        if token and token not in AI_CROP_DOCTOR_LOCAL_QA_STOPWORDS
    }


def format_ai_crop_doctor_symptom_rule_answer(rule, language="Hinglish"):
    if not isinstance(rule, dict):
        return None

    issue = str(rule.get("issue") or "").strip()
    cause = str(rule.get("cause") or "").strip()
    solution = str(rule.get("solution") or "").strip()
    prevention = str(rule.get("prevention") or "").strip()
    if not issue:
        return None

    if str(language or "").strip().lower() == "english":
        parts = [f"It looks like {issue}."]
        if cause:
            parts.append(f"Common cause: {cause}.")
        if solution:
            parts.append(f"Do this now: {solution}.")
        if prevention:
            parts.append(f"Prevention: {prevention}.")
        return " ".join(parts).strip()

    parts = [f"{issue} jaisa symptom lag raha hai."]
    if cause:
        parts.append(f"Isska common cause {cause} ho sakta hai.")
    if solution:
        parts.append(f"Abhi {solution}.")
    if prevention:
        parts.append(f"Bachav ke liye {prevention}.")
    return " ".join(parts).strip()


def lookup_ai_crop_doctor_project_faq(query_text):
    query_lower = str(query_text or "").strip().lower()
    query_tokens = set(re.findall(r"[a-z0-9]+", query_lower))
    if not query_tokens:
        return None

    best_entry = None
    best_score = 0
    for entry in load_ai_crop_doctor_faq_entries():
        question = str(entry.get("question") or "").strip()
        answer = str(entry.get("answer") or "").strip()
        haystack = f"{question} {answer}".lower()
        entry_tokens = set(re.findall(r"[a-z0-9]+", haystack))
        overlap = len(query_tokens & entry_tokens)
        score = overlap
        if question.lower() in query_lower:
            score += 6
        elif overlap and any(token in question.lower() for token in query_tokens):
            score += 2
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry and best_score >= 3:
        return str(best_entry.get("answer") or "").strip() or None
    return None


def lookup_ai_crop_doctor_local_qa(query_text):
    query_text = str(query_text or "").strip()
    query_lower = query_text.lower()
    normalized_query = normalize_ai_crop_doctor_match_text(query_text)
    query_tokens = extract_ai_crop_doctor_match_tokens(query_text)
    if not query_tokens:
        return None

    language = detect_ai_chat_language(query_text)
    best_entry = None
    best_score = 0
    best_strong_overlap = 0
    best_phrase_match = False
    for entry in load_ai_crop_doctor_local_qa():
        question = str(entry.get("question") or entry.get("q") or "").strip()
        answer = get_ai_crop_doctor_local_answer(entry, language=language)
        if not question or not answer:
            continue

        question_lower = question.lower()
        normalized_question = normalize_ai_crop_doctor_match_text(question)
        question_tokens = extract_ai_crop_doctor_match_tokens(question)
        overlap = len(query_tokens & question_tokens)
        score = 0
        phrase_match = False
        if normalized_query == normalized_question:
            score += 12
            phrase_match = True
        elif normalized_question and (normalized_question in normalized_query or normalized_query in normalized_question):
            score += 6
            phrase_match = True

        keywords = entry.get("keywords", [])
        keyword_tokens = set()
        if isinstance(keywords, list):
            for keyword in keywords:
                keyword_text = str(keyword or "").strip()
                keyword_lower = keyword_text.lower()
                normalized_keyword = normalize_ai_crop_doctor_match_text(keyword_text)
                if not keyword_lower:
                    continue
                if keyword_lower in query_lower or (normalized_keyword and normalized_keyword in normalized_query):
                    score += 5
                    phrase_match = True
                current_keyword_tokens = extract_ai_crop_doctor_match_tokens(keyword_text)
                keyword_tokens.update(current_keyword_tokens)
                if current_keyword_tokens:
                    score += min(4, len(query_tokens & current_keyword_tokens) * 2)

        category = str(entry.get("category") or "").strip().lower()
        if category and category in query_lower:
            score += 2

        signature_tokens = set(question_tokens) | keyword_tokens
        strong_query_tokens = query_tokens - AI_CROP_DOCTOR_LOW_SIGNAL_TOKENS
        strong_signature_tokens = signature_tokens - AI_CROP_DOCTOR_LOW_SIGNAL_TOKENS
        strong_overlap = len(strong_query_tokens & strong_signature_tokens)
        if not phrase_match and strong_overlap == 0:
            continue
        score += overlap
        if strong_overlap:
            score += strong_overlap * 3
            score += round((strong_overlap / max(len(strong_query_tokens), 1)) * 4, 2)

        if score > best_score:
            best_score = score
            best_entry = entry
            best_strong_overlap = strong_overlap
            best_phrase_match = phrase_match

    if best_entry and best_score >= 7 and (best_phrase_match or best_strong_overlap >= 2):
        return get_ai_crop_doctor_local_answer(best_entry, language=language)

    if query_tokens & AI_CROP_DOCTOR_SYMPTOM_CUES:
        symptom_rule = match_disease_symptom_rule(normalized_query, query_text)
        formatted_answer = format_ai_crop_doctor_symptom_rule_answer(symptom_rule, language=language)
        if formatted_answer:
            return formatted_answer

    return None


def load_disease_symptom_rules():
    if not DISEASE_SYMPTOM_RULES_PATH.exists():
        return {}

    try:
        data = json.loads(DISEASE_SYMPTOM_RULES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def find_store_product_by_asset_hint(product_hint):
    hint = Path(str(product_hint or "").strip()).stem.lower()
    if not hint:
        return None

    alias_map = {
        "npk": "NPK Fertilizer",
        "fungicide": "Bio Pesticide",
        "neem_oil": "Neem Oil",
        "neem-oil": "Neem Oil",
        "pesticide": "Pesticide Spray",
        "irrigation": "Drip Pipes",
        "fertilizer": "NPK Fertilizer",
        "soil_booster": "Soil Health Booster",
        "soil-booster": "Soil Health Booster",
    }
    mapped_name = alias_map.get(hint)
    if mapped_name:
        product = find_store_product_by_name(mapped_name)
        if product is not None:
            return product

    for product in get_all_store_products():
        haystack = " ".join(
            [
                str(getattr(product, "name", "") or ""),
                str(getattr(product, "description", "") or ""),
                " ".join(getattr(product, "tags", []) or []),
            ]
        ).lower()
        if hint.replace("_", " ") in haystack or hint.replace("-", " ") in haystack:
            return product
    return None


def match_disease_symptom_rule(*values):
    diagnostic_text = " ".join(str(value or "") for value in values).strip().lower()
    if not diagnostic_text:
        return None

    best_key = None
    best_rule = None
    best_score = 0
    for raw_key, raw_rule in load_disease_symptom_rules().items():
        key = str(raw_key or "").strip().lower()
        if not key or not isinstance(raw_rule, dict):
            continue
        score = 0
        if key in diagnostic_text:
            score += 5
        key_tokens = set(re.findall(r"[a-z0-9]+", key))
        text_tokens = set(re.findall(r"[a-z0-9]+", diagnostic_text))
        score += len(key_tokens & text_tokens)
        if score > best_score:
            best_score = score
            best_key = key
            best_rule = raw_rule

    if best_rule is None or best_score < 2:
        return None
    return {"key": best_key, **best_rule}


=======
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
def slugify_crop_name(name):
    parts = re.findall(r"[a-z0-9]+", (name or "").lower())
    return "-".join(parts) or "crop"


def normalize_disease_key(disease_name):
    value = str(disease_name or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value


<<<<<<< HEAD
=======
def resolve_disease_library_image(raw_item):
    item = raw_item if isinstance(raw_item, dict) else {}
    explicit = str(item.get("image") or "").strip()
    if explicit:
        if explicit.startswith(("http://", "https://", "data:image/")):
            return explicit
        if explicit.startswith("/static/"):
            candidate = Path(app.root_path) / explicit.lstrip("/").replace("/", os.sep)
            if candidate.exists():
                return explicit

    name = str(item.get("name") or item.get("slug") or "").strip()
    slug = slugify_crop_name(item.get("slug") or name)
    tags = [str(tag).strip().lower() for tag in (item.get("tags") or []) if str(tag).strip()]
    disease_type = str(item.get("type") or "disease").strip().lower()

    candidate_keys = [
        slug,
        normalize_disease_key(name).replace(" ", "-"),
    ]

    alias_map = {
        "common-rust": "yellow-rust",
        "leaf-rust": "yellow-rust",
        "rust-leaf-spot": "yellow-rust",
        "northern-leaf-blight": "leaf-blight",
        "gray-leaf-spot": "brown-spot",
        "spider-mite-infestation": "aphids",
        "spider-mites-two-spotted-spider-mite": "aphids",
        "leaf-curl-virus": "leaf-curl-virus",
        "tomato-yellowleaf-curl-virus": "leaf-curl-virus",
        "tomato-mosaic-virus": "mosaic-disease",
        "mosaic-virus": "mosaic-disease",
        "target-spot": "brown-spot",
    }

    keyword_candidates = [
        ("armyworm", "fall-armyworm"),
        ("stem borer", "stem-borer"),
        ("hispa", "rice-hispa"),
        ("thrips", "thrips"),
        ("whitefly", "whitefly"),
        ("aphid", "aphids"),
        ("mite", "aphids"),
        ("mosaic", "mosaic-disease"),
        ("curl", "leaf-curl-virus"),
        ("blast", "rice-blast"),
        ("powdery mildew", "powdery-mildew"),
        ("downy mildew", "downy-mildew"),
        ("mildew", "powdery-mildew"),
        ("rust", "yellow-rust"),
        ("leaf mold", "leaf-mold"),
        ("mold", "leaf-mold"),
        ("bacterial leaf blight", "bacterial-leaf-blight"),
        ("bacterial spot", "bacterial-spot"),
        ("leaf blight", "leaf-blight"),
        ("blight", "leaf-blight"),
        ("root rot", "root-rot"),
        ("damping off", "damping-off"),
        ("brown spot", "brown-spot"),
        ("spot", "brown-spot"),
    ]

    for key in list(candidate_keys):
        mapped_key = alias_map.get(key)
        if mapped_key:
            candidate_keys.append(mapped_key)

    search_text = " ".join(
        [name.lower(), slug.replace("-", " "), disease_type, " ".join(tags)]
    )
    for keyword, asset_key in keyword_candidates:
        if keyword in search_text:
            candidate_keys.append(asset_key)

    seen = set()
    for key in candidate_keys:
        key = str(key or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        for suffix in ALLOWED_IMAGE_SUFFIXES:
            image_path = DISEASE_LIBRARY_IMAGE_DIR / f"{key}{suffix}"
            if image_path.exists():
                return f"/static/library/diseases/{image_path.name}"

    return DISEASE_LIBRARY_DEFAULT_IMAGES.get(disease_type, DISEASE_LIBRARY_DEFAULT_IMAGES["disease"])


def get_best_disease_library_entry(disease_name, crop_name=""):
    disease_key = normalize_disease_key(disease_name)
    crop_key = normalize_crop_key(crop_name)
    if not disease_key:
        return None

    entries = load_disease_library()
    scored_matches = []

    for entry in entries:
        entry_name = str(entry.get("name") or "").strip()
        entry_key = normalize_disease_key(entry_name)
        if not entry_key:
            continue

        score = 0.0
        if entry_key == disease_key or entry.get("slug") == slugify_crop_name(disease_name):
            score += 7.0
        elif disease_key in entry_key or entry_key in disease_key:
            score += 4.2

        disease_tokens = set(re.findall(r"[a-z0-9]+", disease_key))
        entry_tokens = set(re.findall(r"[a-z0-9]+", entry_key))
        score += len(disease_tokens & entry_tokens) * 1.1

        crops = [str(c).strip().lower() for c in (entry.get("crops") or []) if str(c).strip()]
        if crops:
            if crop_key != "generic" and any(normalize_crop_key(crop) == crop_key for crop in crops):
                score += 3.4
            elif crop_name and any(str(crop_name).strip().lower() == crop for crop in crops):
                score += 2.0

        if score > 0:
            scored_matches.append((score, entry))

    if not scored_matches:
        return None

    scored_matches.sort(key=lambda item: item[0], reverse=True)
    return scored_matches[0][1]


def summarize_disease_symptoms(entry, fallback_text=""):
    if isinstance(entry, dict):
        symptoms = [str(item).strip() for item in (entry.get("symptoms") or []) if str(item).strip()]
        if symptoms:
            return "; ".join(symptoms[:3])
    return str(fallback_text or "Visible crop stress markers detected on the leaf surface.").strip()


def derive_organic_solution(disease_name="", entry=None, crop_name=""):
    disease_text = normalize_disease_key(disease_name)
    crop_label = str(crop_name or "crop").strip() or "crop"
    entry_type = str((entry or {}).get("type") or "").strip().lower() if isinstance(entry, dict) else ""

    if any(keyword in disease_text for keyword in ("mite", "whitefly", "thrips", "aphid", "armyworm", "borer", "pest", "insect")) or entry_type in {"insect", "pest"}:
        return f"Spray neem oil during evening hours, remove heavily infested leaves, and keep {crop_label} scouting frequent."
    if any(keyword in disease_text for keyword in ("virus", "mosaic", "curl")) or entry_type == "virus":
        return "Rogue infected plants early, sanitize tools, and suppress vector insects with neem-based management."
    if any(keyword in disease_text for keyword in ("bacterial",)) or entry_type == "bacteria":
        return "Use copper-compatible organic support, avoid overhead irrigation, and keep tools and hands sanitized."
    if any(keyword in disease_text for keyword in ("brown spot", "nutrient", "deficiency")):
        return f"Add compost or soil booster, keep irrigation uniform, and reduce stress on {crop_label} plants."
    return "Use a bio-protective spray, prune infected leaves, and improve airflow around the crop canopy."


>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
def resolve_crop_library_image(slug):
    for suffix in (".jpg", ".jpeg", ".png", ".webp"):
        image_path = CROP_LIBRARY_IMAGE_DIR / f"{slug}{suffix}"
        if image_path.exists():
            return f"/static/images/crops/{image_path.name}"
    if slug in CROP_LIBRARY_REMOTE_IMAGE_FALLBACKS:
        return CROP_LIBRARY_REMOTE_IMAGE_FALLBACKS[slug]
    return CROP_LIBRARY_DEFAULT_IMAGE


def unique_crop_list(values):
    seen = set()
    unique_values = []
    for value in values:
        item = str(value or "").strip()
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        unique_values.append(item)
    return unique_values


def infer_crop_labour_requirement(planting_method, life_cycle, category):
    method = (planting_method or "").strip().lower()
    cycle = (life_cycle or "").strip().lower()
    if any(keyword in method for keyword in ("transplant", "grafted", "sucker")):
        return "High"
    if any(keyword in method for keyword in ("setts", "tuber", "bulb", "clove")):
        return "Moderate"
    if category in {"fruit", "beverage"} or cycle == "perennial":
        return "Moderate to High"
    return "Moderate"


def resolve_crop_library_metadata(slug, raw_entry):
    category = CROP_LIBRARY_CATEGORY_MAP.get(slug, "vegetable")
    defaults = CROP_LIBRARY_CATEGORY_DEFAULTS.get(category, {})
    overrides = CROP_LIBRARY_OVERRIDES.get(slug, {})
    raw_aliases = raw_entry.get("aliases", [])
    raw_good = raw_entry.get("good_companions", [])
    raw_bad = raw_entry.get("bad_companions", [])
    raw_tips = raw_entry.get("farming_tips", [])

    aliases = unique_crop_list([*raw_aliases, *overrides.get("aliases", [])])
    good_companions = unique_crop_list(raw_good or overrides.get("good_companions", []) or defaults.get("good_companions", []))
    bad_companions = unique_crop_list(raw_bad or overrides.get("bad_companions", []) or defaults.get("bad_companions", []))
    farming_tips = unique_crop_list(raw_tips or overrides.get("farming_tips", []) or defaults.get("farming_tips", []))

    labour_requirement = (
        str(raw_entry.get("labour_requirement") or "").strip()
        or str(overrides.get("labour_requirement") or "").strip()
        or infer_crop_labour_requirement(raw_entry.get("planting_method"), raw_entry.get("life_cycle"), category)
    )

    return {
        "aliases": aliases,
        "category": category,
        "category_label": CROP_LIBRARY_CATEGORY_LABELS.get(category, "Crop"),
        "good_companions": good_companions,
        "bad_companions": bad_companions,
        "farming_tips": farming_tips,
        "labour_requirement": labour_requirement,
    }


def pick_related_crops(crop_slug, category, life_cycle, soil_type, limit=6):
    related_candidates = []
    for entry in load_crop_library():
        if entry["slug"] == crop_slug:
            continue

        score = 0
        if entry.get("category") == category:
            score += 4
        if entry.get("life_cycle") == life_cycle:
            score += 2
        if entry.get("soil_type") == soil_type:
            score += 1

        related_candidates.append((score, entry["name"], entry))

    related_candidates.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in related_candidates[:limit]]


def load_crop_library():
    global CROP_LIBRARY_CACHE

    if CROP_LIBRARY_CACHE is not None:
        return CROP_LIBRARY_CACHE

    if not CROP_LIBRARY_DATA_PATH.exists():
        CROP_LIBRARY_CACHE = []
        return CROP_LIBRARY_CACHE

    try:
        raw_entries = json.loads(CROP_LIBRARY_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        CROP_LIBRARY_CACHE = []
        return CROP_LIBRARY_CACHE

    if not isinstance(raw_entries, list):
        CROP_LIBRARY_CACHE = []
        return CROP_LIBRARY_CACHE

    normalized_entries = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            continue

        name = (raw_entry.get("name") or "").strip()
        if not name:
            continue

        slug = slugify_crop_name(raw_entry.get("slug") or name)
        crop_metadata = resolve_crop_library_metadata(slug, raw_entry)
        aliases = crop_metadata["aliases"]
        good_companions = crop_metadata["good_companions"]
        bad_companions = crop_metadata["bad_companions"]
        farming_tips = crop_metadata["farming_tips"]

        search_terms = " ".join(
            filter(
                None,
                [
                    name,
                    slug.replace("-", " "),
                    crop_metadata["category_label"],
                    " ".join(aliases),
                    raw_entry.get("soil_type", ""),
                    raw_entry.get("planting_method", ""),
                    raw_entry.get("life_cycle", ""),
                ],
            )
        ).lower()

        normalized_entries.append(
            {
                "name": name,
                "slug": slug,
                "aliases": aliases,
                "temperature": str(raw_entry.get("temperature") or "N/A"),
                "rainfall": str(raw_entry.get("rainfall") or "N/A"),
                "sunlight": str(raw_entry.get("sunlight") or "N/A"),
                "humidity": str(raw_entry.get("humidity") or "N/A"),
                "life_cycle": str(raw_entry.get("life_cycle") or "N/A"),
                "planting_method": str(raw_entry.get("planting_method") or "N/A"),
                "labour_requirement": crop_metadata["labour_requirement"],
                "soil_type": str(raw_entry.get("soil_type") or "N/A"),
                "ph_range": str(raw_entry.get("ph_range") or "N/A"),
                "row_spacing": str(raw_entry.get("row_spacing") or "N/A"),
                "plant_spacing": str(raw_entry.get("plant_spacing") or "N/A"),
                "nitrogen": str(raw_entry.get("nitrogen") or "N/A"),
                "phosphorus": str(raw_entry.get("phosphorus") or "N/A"),
                "potassium": str(raw_entry.get("potassium") or "N/A"),
                "category": crop_metadata["category"],
                "category_label": crop_metadata["category_label"],
                "good_companions": good_companions,
                "bad_companions": bad_companions,
                "farming_tips": farming_tips,
                "image_url": resolve_crop_library_image(slug),
                "search_text": search_terms,
            }
        )

    CROP_LIBRARY_CACHE = normalized_entries
    return CROP_LIBRARY_CACHE


def get_crop_library_entry(slug):
    crop_slug = slugify_crop_name(slug)
    return next((entry for entry in load_crop_library() if entry["slug"] == crop_slug), None)


def build_crop_library_context():
    crops = load_crop_library()
    return {
        "count": len(crops),
        "annual_count": sum(1 for crop in crops if crop["life_cycle"].lower() == "annual"),
        "perennial_count": sum(1 for crop in crops if crop["life_cycle"].lower() == "perennial"),
        "category_count": len({crop["category"] for crop in crops}),
        "crops": crops,
        "featured": crops[:8],
    }


<<<<<<< HEAD
def infer_library_disease_type(entry):
    text = " ".join(
        [
            str(entry.get("name") or ""),
            str(entry.get("cause") or ""),
            " ".join(str(signal) for signal in entry.get("signals", set())),
        ]
    ).lower()
    if any(keyword in text for keyword in ("aphid", "armyworm", "borer", "hispa", "thrips", "whitefly", "fly", "insect", "pest", "mite")):
        return "insect"
    if any(keyword in text for keyword in ("bacterial", "bacteria")):
        return "bacteria"
    if any(keyword in text for keyword in ("virus", "viral", "mosaic", "curl")):
        return "virus"
    return "fungus"


def build_library_symptoms(entry):
    signals = {str(signal).strip().lower() for signal in entry.get("signals", set())}
    symptom_map = {
        "brown": "Brown lesions or rusty patches appear on leaves.",
        "yellow": "Yellowing, mottling, or chlorotic streaks spread across foliage.",
        "white": "White powdery or pale fungal growth appears on the leaf surface.",
        "dark": "Dark, water-soaked, or blackened spots expand quickly.",
        "humid": "Symptoms worsen after humid weather, dew, or extended leaf wetness.",
        "stress": "Plants lose vigor, canopy freshness, and uniform leaf color.",
        "heat": "Leaf burn or stress becomes more visible during hot afternoons.",
    }
    symptoms = [text for key, text in symptom_map.items() if key in signals]
    if symptoms:
        return symptoms[:4]
    cause = str(entry.get("cause") or "").strip()
    return [cause] if cause else ["Visible symptoms vary with crop stage and field conditions."]


def resolve_library_disease_image(slug, disease_name):
    disease_slug = slugify_crop_name(disease_name)
    image_dir = Path(app.root_path) / "static" / "library" / "diseases"
    candidates = [
        slug,
        disease_slug,
        slug.replace("generic-", ""),
    ]
    for candidate in candidates:
        for suffix in (".jpg", ".jpeg", ".png", ".webp"):
            file_path = image_dir / f"{candidate}{suffix}"
            if file_path.exists():
                return f"/static/library/diseases/{candidate}{suffix}"
    return build_disease_sample_data_uri(disease_name)


def derive_library_signals_from_text(text):
    text_value = str(text or "").lower()
    signals = set()
    keyword_map = {
        "brown": {"blight", "rust", "spot", "lesion", "necrosis"},
        "yellow": {"yellow", "chlorosis"},
        "white": {"powder", "white", "mildew", "mold"},
        "dark": {"dark", "black", "late blight", "water-soaked"},
        "humid": {"humid", "wet", "dew", "rain", "leaf wetness"},
        "stress": {"stress", "weak", "stunted", "damage"},
        "heat": {"heat", "hot"},
    }
    for signal, keywords in keyword_map.items():
        if any(keyword in text_value for keyword in keywords):
            signals.add(signal)
    return signals


def parse_model_label_entry(raw_label):
    raw_value = str(raw_label or "").strip()
    if not raw_value:
        return None
    lowered = raw_value.lower()
    if lowered == "plantvillage" or "healthy" in lowered:
        return None

    normalized = raw_value.replace("___", "|").replace("__", "|")
    parts = [segment.strip() for segment in normalized.split("|") if segment.strip()]
    if len(parts) < 2:
        return None

    crop_label = re.sub(r"\s+", " ", parts[0].replace("_", " ")).strip().title()
    disease_label = re.sub(r"\s+", " ", " ".join(parts[1:]).replace("_", " ")).strip().title()
    if not crop_label or not disease_label:
        return None

    try:
        from disease_knowledge import DISEASE_KNOWLEDGE  # type: ignore
    except Exception:
        DISEASE_KNOWLEDGE = {}

    info = DISEASE_KNOWLEDGE.get(raw_value, DISEASE_KNOWLEDGE.get("DEFAULT", {}))
    cause = str(info.get("cause") or f"{disease_label} affects {crop_label} under favorable field conditions.").strip()
    solution = str(info.get("solution") or info.get("recommendation") or "Inspect the crop and apply the recommended treatment quickly.").strip()
    prevention = unique_crop_list(
        [
            str(info.get("recommendation") or "").strip(),
            "Scout the crop twice a week and isolate affected foliage early.",
            "Keep field sanitation, airflow, and irrigation balance under control.",
        ]
    )
    signals = derive_library_signals_from_text(" ".join([raw_value, cause, solution]))

    crop_key = normalize_crop_key(crop_label)
    slug = slugify_crop_name(f"{crop_key}-{disease_label}")
    return {
        "slug": slug,
        "name": disease_label,
        "type": infer_library_disease_type({"name": disease_label, "cause": cause, "signals": signals}),
        "crop_key": crop_key,
        "crops": [crop_label],
        "cause": cause,
        "solution": solution,
        "prevention": prevention,
        "symptoms": build_library_symptoms({"signals": signals, "cause": cause}),
        "signals": sorted(str(signal) for signal in signals),
        "image": resolve_library_disease_image(slug, disease_label),
    }


def get_model_label_disease_items():
    label_paths = [CROP_DISEASE_LABELS_PATH]
    alt_path = Path(app.root_path) / "models" / "crop_disease_labels.json"
    if alt_path not in label_paths:
        label_paths.append(alt_path)

    raw_labels = []
    seen_labels = set()
    for label_path in label_paths:
        if not label_path.exists():
            continue
        try:
            payload = json.loads(label_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue

        iterable = payload.values() if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        for raw_label in iterable:
            label_text = str(raw_label or "").strip()
            if not label_text or label_text in seen_labels:
                continue
            seen_labels.add(label_text)
            raw_labels.append(label_text)

    items = []
    for raw_label in raw_labels:
        parsed = parse_model_label_entry(raw_label)
        if parsed is None:
            continue
        parsed["type_key"] = parsed["type"]
        parsed["type"] = parsed["type"].title()
        parsed["search_text"] = " ".join(
            [
                parsed["name"],
                " ".join(parsed["crops"]),
                parsed["type"],
                parsed["cause"],
                parsed["solution"],
                " ".join(parsed["prevention"]),
                " ".join(parsed["symptoms"]),
            ]
        ).lower()
        items.append(parsed)
    return items


def get_library_disease_items():
    items = []
    seen_slugs = set()
    for crop_key, entries in CROP_DISEASE_LIBRARY.items():
        crop_label = "General" if crop_key == "generic" else crop_key.title()
        for entry in entries:
            disease_name = str(entry.get("name") or "").strip() or "Unknown Disease"
            slug = slugify_crop_name(f"{crop_key}-{disease_name}")
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            disease_type = infer_library_disease_type(entry)
            prevention = [str(item).strip() for item in entry.get("prevention_tips", []) if str(item).strip()]
            items.append(
                {
                    "slug": slug,
                    "name": disease_name,
                    "type": disease_type.title(),
                    "type_key": disease_type,
                    "crop_key": crop_key,
                    "crops": [crop_label],
                    "cause": str(entry.get("cause") or "").strip(),
                    "solution": str(entry.get("solution") or "").strip(),
                    "prevention": prevention,
                    "symptoms": build_library_symptoms(entry),
                    "signals": sorted(str(signal) for signal in entry.get("signals", set())),
                    "image": resolve_library_disease_image(slug, disease_name),
                    "search_text": " ".join(
                        [
                            disease_name,
                            crop_label,
                            disease_type,
                            str(entry.get("cause") or ""),
                            str(entry.get("solution") or ""),
                            " ".join(prevention),
                        ]
                    ).lower(),
                }
            )
    for item in get_model_label_disease_items():
        if item["slug"] in seen_slugs:
            continue
        seen_slugs.add(item["slug"])
        items.append(item)
    items.sort(key=lambda item: (item["crops"][0], item["name"]))
    return items


def get_library_disease_item(slug):
    normalized_slug = slugify_crop_name(slug)
    return next((item for item in get_library_disease_items() if item["slug"] == normalized_slug), None)


def get_library_crop_options():
    crop_labels = sorted({item["crops"][0] for item in get_library_disease_items() if item["crops"]})
    return ["All"] + crop_labels


def build_library_stage_sections(items):
    grouped = {}
    for item in items:
        grouped.setdefault(item["type"], []).append(item)
    sections = []
    for label in ("Fungus", "Bacteria", "Virus", "Insect"):
        section_items = grouped.get(label, [])
        if section_items:
            sections.append({"label": label, "items": section_items[:8]})
    if not sections:
        sections.append({"label": "Popular Guides", "items": items[:8]})
    return sections


def build_library_home_context():
    disease_items = get_library_disease_items()
    crop_library_page = build_crop_library_context()
    tips = build_library_tips_data("All")
    type_counts = {}
    for item in disease_items:
        type_counts[item["type"]] = type_counts.get(item["type"], 0) + 1
    return {
        "crop_count": crop_library_page["count"],
        "disease_count": len(disease_items),
        "tip_task_count": len(tips.get("tasks", [])),
        "tip_stage_count": len(tips.get("stages", [])),
        "covered_crop_count": len({item["crops"][0] for item in disease_items if item["crops"]}),
        "fungus_count": type_counts.get("Fungus", 0),
        "bacteria_count": type_counts.get("Bacteria", 0),
        "virus_count": type_counts.get("Virus", 0),
        "insect_count": type_counts.get("Insect", 0),
    }


def build_library_tips_data(active_crop):
    crop_name = active_crop if active_crop and active_crop != "All" else "your crop"
    crop_text = str(crop_name)
    return {
        "tasks": [
            {
                "icon": "fa-droplet",
                "label": "Irrigation planning",
                "summary": f"Keep irrigation balanced for {crop_text} based on soil moisture and weather.",
                "detail": f"Water early in the day, avoid long leaf wetness at night, and increase scouting after irrigation cycles in {crop_text} fields.",
            },
            {
                "icon": "fa-flask",
                "label": "Nutrition management",
                "summary": "Split fertilizer doses and avoid stress from over-application.",
                "detail": f"Use balanced nitrogen, phosphorus, and potassium so {crop_text} plants stay vigorous and less prone to disease pressure.",
            },
            {
                "icon": "fa-bug",
                "label": "Pest scouting",
                "summary": "Scout twice a week and act on hotspots before spread accelerates.",
                "detail": f"Check lower leaves, canopy edges, and humid zones in {crop_text} plots for early symptoms, eggs, or insect feeding damage.",
            },
            {
                "icon": "fa-scissors",
                "label": "Field sanitation",
                "summary": "Remove infected residue and improve airflow around plants.",
                "detail": "Discard badly infected leaves, keep beds clean, and avoid moving infected plant material between plots.",
            },
        ],
        "stages": [
            {
                "icon": "fa-seedling",
                "label": "Early growth",
                "items": [
                    "Use healthy seed or planting material.",
                    "Check emergence and replace weak gaps quickly.",
                    "Protect seedlings from water stress and soil splash.",
                ],
            },
            {
                "icon": "fa-leaf",
                "label": "Vegetative stage",
                "items": [
                    "Monitor canopy color and leaf spots every few days.",
                    "Maintain airflow and avoid waterlogging.",
                    "Correct nutrient imbalance before symptoms spread.",
                ],
            },
            {
                "icon": "fa-wheat-awn",
                "label": "Flowering to harvest",
                "items": [
                    "Avoid heavy stress during flowering and grain fill.",
                    "Act fast on disease outbreaks to protect yield.",
                    "Keep harvest tools and storage areas clean.",
                ],
            },
        ],
    }


def build_library_alert_items(active_crop):
    items = get_library_disease_items()
    if active_crop and active_crop != "All":
        items = [item for item in items if active_crop in item["crops"]]
    alerts = []
    for item in items[:8]:
        signals = set(item.get("signals", []))
        severity = "low"
        if {"humid", "dark", "stress"} & signals:
            severity = "high"
        elif {"brown", "yellow", "white"} & signals:
            severity = "medium"
        alerts.append(
            {
                **item,
                "crop": item["crops"][0] if item["crops"] else "General",
                "severity": severity,
            }
        )
    return alerts


def build_library_disease_detail_payload(user, disease):
    related_items = [
        item
        for item in get_library_disease_items()
        if item["slug"] != disease["slug"] and (item["crop_key"] == disease["crop_key"] or item["type_key"] == disease["type_key"])
    ][:3]
    recommendation = resolve_store_recommendation(disease_name=disease["name"], cause=disease["cause"], chemical_solution=disease["solution"])
    recommended_product = serialize_store_product(recommendation) if recommendation is not None else None
    last_record = (
        DiseaseHistory.query.filter_by(user_id=user.id).order_by(DiseaseHistory.date.desc()).first()
        if user is not None
        else None
    )
    last_diagnosis = None
    if last_record is not None:
        record_name = str(last_record.detected_disease or "").strip()
        if not record_name or slugify_crop_name(record_name) == slugify_crop_name(disease["name"]):
            risk_level = "High" if int(last_record.confidence or 0) >= 85 else "Medium" if int(last_record.confidence or 0) >= 65 else "Low"
            last_diagnosis = {
                "confidence": int(last_record.confidence or 0),
                "risk_level": risk_level,
                "note": f"Latest saved scan for {record_name or disease['name']} on {format_relative_time(last_record.date)}.",
                "image_url": None,
            }
    detail_context = {
        "stage_affected": "Leaf and canopy stage monitoring",
        "urgency": "Immediate action recommended" if disease["type_key"] in {"bacteria", "virus"} else "Early action recommended",
        "crop_affected": ", ".join(disease["crops"]) if disease["crops"] else "Multiple crops",
        "do_now_checklist": unique_crop_list(
            [
                "Inspect nearby plants for matching symptoms.",
                "Isolate the worst affected leaves or plants where possible.",
                disease["solution"] or "Apply the recommended control measure as per label guidance.",
                "Repeat scouting in the next 48 to 72 hours.",
            ]
        )[:4],
        "product_reason": f"Suggested because it aligns with the treatment needs for {disease['name']}." if recommended_product else "",
        "similar_diseases": related_items,
    }
    return {
        "disease": disease,
        "detail_context": detail_context,
        "recommended_product": recommended_product,
        "last_diagnosis": last_diagnosis,
    }


def build_admin_audit_context():
    disease_items = get_library_disease_items()
    mappings = DiseaseProductMapping.query.all()
    products = StoreProduct.query.all()

    mapped_keys = {mapping.disease_key for mapping in mappings if getattr(mapping, "disease_key", "")}
    weak_tag_product_count = 0
    for product in products:
        tags = safe_json_loads(getattr(product, "tags_json", ""), [])
        clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
        if len(clean_tags) < 2:
            weak_tag_product_count += 1

    missing_mappings = []
    missing_content = []
    recommendation_review = []

    for item in disease_items:
        disease_key = normalize_disease_key(item["name"])
        if disease_key not in mapped_keys:
            missing_mappings.append({"name": item["name"], "type": item["type"]})

        gaps = []
        if not item.get("symptoms"):
            gaps.append("symptoms")
        if not str(item.get("cause") or "").strip():
            gaps.append("cause")
        if not str(item.get("solution") or "").strip():
            gaps.append("solution")
        if not item.get("prevention"):
            gaps.append("prevention")
        if gaps:
            missing_content.append({"name": item["name"], "gaps": gaps})

        mapped_product = get_admin_mapped_product_for_disease(item["name"])
        recommendation_review.append(
            {
                "disease": item["name"],
                "crop": item["crops"][0] if item.get("crops") else "General",
                "product_name": getattr(mapped_product, "name", "") or "Auto recommendation",
                "status": "Mapped" if mapped_product is not None else "Auto",
            }
        )

    return {
        "mapping_count": len(mappings),
        "unmapped_count": len(missing_mappings),
        "missing_content_count": len(missing_content),
        "weak_tag_product_count": weak_tag_product_count,
        "missing_mappings": missing_mappings[:12],
        "missing_content": missing_content[:12],
        "recommendation_review": recommendation_review[:10],
    }


=======
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
def safe_json_loads(raw_value, default):
    if raw_value in (None, ""):
        return default
    try:
        return json.loads(raw_value)
    except (TypeError, ValueError):
        return default


<<<<<<< HEAD
=======
def _cache_file_mtime(path):
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def load_disease_library():
    """Load pests & disease reference data from dataset/disease_data.json (cached)."""
    global DISEASE_LIBRARY_CACHE

    mtime = _cache_file_mtime(DISEASE_LIBRARY_DATA_PATH)
    if DISEASE_LIBRARY_CACHE.get("items") and DISEASE_LIBRARY_CACHE.get("mtime") == mtime:
        return DISEASE_LIBRARY_CACHE["items"]

    if not DISEASE_LIBRARY_DATA_PATH.exists():
        DISEASE_LIBRARY_CACHE = {"mtime": 0.0, "items": []}
        return []

    try:
        raw = json.loads(DISEASE_LIBRARY_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        DISEASE_LIBRARY_CACHE = {"mtime": mtime, "items": []}
        return []

    items = raw if isinstance(raw, list) else []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        slug = slugify_crop_name(item.get("slug") or name)
        normalized.append(
            {
                "slug": slug,
                "name": name,
                "type": str(item.get("type") or "Disease").strip() or "Disease",
                "crops": [str(c).strip() for c in (item.get("crops") or []) if str(c).strip()] if isinstance(item.get("crops"), list) else [],
                "stages": [str(s).strip().lower() for s in (item.get("stages") or []) if str(s).strip()] if isinstance(item.get("stages"), list) else [],
                "symptoms": [str(s).strip() for s in (item.get("symptoms") or []) if str(s).strip()] if isinstance(item.get("symptoms"), list) else [],
                "cause": str(item.get("cause") or "").strip(),
                "solution": str(item.get("solution") or "").strip(),
                "prevention": [str(p).strip() for p in (item.get("prevention") or []) if str(p).strip()] if isinstance(item.get("prevention"), list) else [],
                "image": resolve_disease_library_image(item),
                "tags": [str(t).strip() for t in (item.get("tags") or []) if str(t).strip()] if isinstance(item.get("tags"), list) else [],
            }
        )

    DISEASE_LIBRARY_CACHE = {"mtime": mtime, "items": normalized}
    return normalized


def get_disease_library_entry(slug_or_name):
    key = slugify_crop_name(slug_or_name)
    return next((entry for entry in load_disease_library() if entry["slug"] == key), None)


def load_cultivation_tips():
    """Load cultivation tips reference data from dataset/cultivation_tips.json (cached)."""
    global CULTIVATION_TIPS_CACHE

    mtime = _cache_file_mtime(CULTIVATION_TIPS_DATA_PATH)
    if CULTIVATION_TIPS_CACHE.get("payload") and CULTIVATION_TIPS_CACHE.get("mtime") == mtime:
        return CULTIVATION_TIPS_CACHE["payload"]

    if not CULTIVATION_TIPS_DATA_PATH.exists():
        CULTIVATION_TIPS_CACHE = {"mtime": 0.0, "payload": {}}
        return {}

    try:
        raw = json.loads(CULTIVATION_TIPS_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        CULTIVATION_TIPS_CACHE = {"mtime": mtime, "payload": {}}
        return {}

    payload = raw if isinstance(raw, dict) else {}
    CULTIVATION_TIPS_CACHE = {"mtime": mtime, "payload": payload}
    return payload


def build_library_crop_options(user=None):
    crops = load_crop_library()
    names = [crop["name"] for crop in crops]
    # Prefer the user's crop first when possible.
    preferred = str(getattr(user, "crop_type", "") or "").strip()
    if preferred:
        for item in list(names):
            if item.lower() == preferred.lower():
                names.remove(item)
                names.insert(0, item)
                break
    return ["All"] + names[:40]


>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
def truncate_text(text, limit=120):
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rsplit(" ", 1)[0].rstrip() + "..."


def default_store_seller(category):
    seller_map = {
        "Pesticides": "AgroShield Labs",
        "Fertilizers": "SoilSpring Nutrients",
        "Seeds": "HarvestGen Seeds",
        "Tools": "FieldSense Tools",
        "Organic": "EcoGrow Organics",
    }
    return seller_map.get(category, "AgroVision Store")


def estimate_store_mrp(price, category):
    markups = {
        "Pesticides": 0.18,
        "Fertilizers": 0.14,
        "Seeds": 0.12,
        "Tools": 0.16,
        "Organic": 0.15,
    }
    base_price = max(int(price or 0), 1)
    markup = markups.get(category, 0.15)
    estimated = int(round(base_price * (1 + markup)))
    return max(base_price + 20, estimated)


def compute_store_discount(price, mrp):
    base_price = max(int(price or 0), 1)
    base_mrp = max(int(mrp or 0), base_price)
    return max(0, int(round((base_mrp - base_price) * 100 / base_mrp)))


def get_store_category_meta(category):
    return STORE_CATEGORY_META.get(
        category,
        {
            "icon": "fa-bag-shopping",
            "accent": "generic",
            "description": "Farm essentials curated for daily field decisions.",
        },
    )


def load_store_seed_dataset():
<<<<<<< HEAD
    seed_rows = []
    for path in (STORE_PRODUCTS_DATA_PATH, DISEASE_STORE_PRODUCTS_DATA_PATH):
        if not path.exists():
            continue
        try:
            raw_data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(raw_data, list):
            seed_rows.extend(raw_data)
    return seed_rows


def load_disease_product_mapping_seed_dataset():
    if not DISEASE_PRODUCT_MAPPINGS_DATA_PATH.exists():
        return []

    try:
        raw_data = json.loads(DISEASE_PRODUCT_MAPPINGS_DATA_PATH.read_text(encoding="utf-8"))
=======
    if not STORE_PRODUCTS_DATA_PATH.exists():
        return []

    try:
        raw_data = json.loads(STORE_PRODUCTS_DATA_PATH.read_text(encoding="utf-8"))
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
        return raw_data if isinstance(raw_data, list) else []
    except (OSError, ValueError, TypeError):
        return []


def seed_store_products():
    seed_data = load_store_seed_dataset()
    if not seed_data:
        return 0

    seeded_count = 0
    for raw_product in seed_data:
        try:
            product_id = int(raw_product.get("id"))
            price = int(raw_product.get("price") or 0)
        except (TypeError, ValueError):
            continue

        if product_id <= 0 or price <= 0:
            continue

        name = str(raw_product.get("name") or f"Product {product_id}").strip()
        if not name:
            continue

        category = str(raw_product.get("category") or "Organic").strip() or "Organic"
        if category not in STORE_CATEGORY_META:
            category = "Organic"

        rating_value = raw_product.get("rating", 4.2)
        try:
            rating = round(float(rating_value), 1)
        except (TypeError, ValueError):
            rating = 4.2

        slug = slugify_crop_name(raw_product.get("slug") or name)
        mrp = int(raw_product.get("mrp") or estimate_store_mrp(price, category))
        discount_pct = int(raw_product.get("discount_pct") or compute_store_discount(price, mrp))
        seller = str(raw_product.get("seller") or default_store_seller(category)).strip() or default_store_seller(category)
        unit = str(raw_product.get("unit") or "Pack").strip() or "Pack"

        tags = raw_product.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        tags = unique_crop_list([str(item or "").strip() for item in tags if str(item or "").strip()])

        product = db.session.get(StoreProduct, product_id)
        seed_image_url = str(raw_product.get("image") or "").strip()
        if product is None:
            product = StoreProduct(id=product_id)

        product.slug = slug
        product.name = name
        product.category = category
        product.price = price
        product.mrp = max(mrp, price)
        product.discount_pct = max(discount_pct, compute_store_discount(product.price, product.mrp))
        product.rating = rating
        # Don't clobber admin/manual image updates on every restart. We only
        # apply seed images when the existing value is empty or a known fallback.
        existing_image_url = str(product.image_url or "").strip()
        if (
            not existing_image_url
            or existing_image_url == STORE_PRODUCT_FALLBACK_IMAGE
            or existing_image_url.lower().startswith("/static/images/store/")
            or existing_image_url.lower().endswith(".svg")
        ):
            product.image_url = seed_image_url
        product.description = str(raw_product.get("description") or "").strip()
        product.seller = seller
        product.unit = unit
        product.tags_json = json.dumps(tags, ensure_ascii=False)
        product.is_active = bool(raw_product.get("is_active", True))
        db.session.add(product)
        seeded_count += 1

    if seeded_count:
        db.session.commit()
    return seeded_count


<<<<<<< HEAD
def seed_disease_product_mappings():
    seed_data = load_disease_product_mapping_seed_dataset()
    if not seed_data:
        return 0

    seeded_count = 0
    for item in seed_data:
        if not isinstance(item, dict):
            continue

        disease_label = str(item.get("disease") or "").strip()
        product_name = str(item.get("product") or "").strip()
        if not disease_label or not product_name:
            continue

        product = find_store_product_by_name(product_name)
        if product is None:
            continue

        disease_key = normalize_disease_key(disease_label)
        mapping = DiseaseProductMapping.query.filter_by(disease_key=disease_key).first()
        if mapping is None:
            mapping = DiseaseProductMapping(
                disease_key=disease_key,
                disease_label=disease_label,
                product_id=product.id,
            )
            db.session.add(mapping)
        else:
            mapping.disease_label = disease_label
            mapping.product_id = product.id
        seeded_count += 1

    if seeded_count:
        db.session.commit()
    return seeded_count


=======
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
def build_store_product_highlights(product):
    tags = safe_json_loads(product.tags_json, [])
    if not isinstance(tags, list):
        tags = []

    highlights = []
    description = str(product.description or "").strip()
    if description:
        highlights.append(description)

    for tag in tags[:2]:
        tag_text = str(tag).strip()
        if tag_text:
            highlights.append(f"Supports {tag_text} workflows in the field.")

    highlights.extend(STORE_CATEGORY_HIGHLIGHTS.get(product.category, []))
    return unique_crop_list(highlights)[:3]


def serialize_store_product(product):
    meta = get_store_category_meta(product.category)
    tags = safe_json_loads(product.tags_json, [])
    if not isinstance(tags, list):
        tags = []

    description = str(product.description or "").strip()
    rating_value = round(float(product.rating or 0), 1)
    search_text = " ".join(
        [
            str(product.name or ""),
            str(product.category or ""),
            description,
            str(product.seller or ""),
            str(product.unit or ""),
            " ".join(str(tag) for tag in tags),
        ]
    ).lower()

<<<<<<< HEAD
=======
    image_url = resolve_store_product_image_url(product)

>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    return {
        "id": product.id,
        "slug": product.slug,
        "name": product.name,
        "category": product.category,
        "category_label": product.category,
        "category_icon": meta["icon"],
        "category_accent": meta["accent"],
        "category_description": meta["description"],
        "price": int(product.price or 0),
        "mrp": int(product.mrp or product.price or 0),
        "discount_pct": max(int(product.discount_pct or 0), compute_store_discount(product.price, product.mrp)),
        "rating": rating_value,
        "rating_label": f"{rating_value:.1f}",
        "rating_count": 48 + (product.id * 13),
<<<<<<< HEAD
        "image_url": (
            str(product.image_url or "").strip() 
            if str(product.image_url or "").strip()
            and str(product.image_url or "").strip() != STORE_PRODUCT_FALLBACK_IMAGE
            and not str(product.image_url or "").strip().lower().startswith("/static/images/store/")
            and not str(product.image_url or "").strip().lower().endswith(".svg")
            else f"https://images.unsplash.com/photo-1592982537447-7440770cbfc9?auto=format&fit=crop&q=80&w=600&sig={product.id}"
            if "seed" not in product.name.lower() and "fertilizer" not in product.name.lower() and "tool" not in product.name.lower() and "sprayer" not in product.name.lower()
            else f"https://images.unsplash.com/photo-1523348837708-15d4a09cfac2?auto=format&fit=crop&q=80&w=600&sig={product.id}"
            if "seed" in product.name.lower()
            else f"https://images.unsplash.com/photo-1628352081506-83c43123ed6d?auto=format&fit=crop&q=80&w=600&sig={product.id}"
            if "fertilizer" in product.name.lower()
            else f"https://images.unsplash.com/photo-1598902108854-10e335adac99?auto=format&fit=crop&q=80&w=600&sig={product.id}"
        ),
=======
        "image_url": image_url,
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
        "fallback_image": STORE_PRODUCT_FALLBACK_IMAGE,
        "description": description,
        "short_description": truncate_text(description, 96),
        "seller": str(product.seller or default_store_seller(product.category)).strip(),
        "unit": str(product.unit or "Pack").strip() or "Pack",
        "tags": [str(tag) for tag in tags],
        "highlights": build_store_product_highlights(product),
        "detail_url": f"/market/product/{product.slug}",
        "search_text": search_text,
    }


def get_store_product_by_id(product_id):
    try:
        normalized_id = int(product_id)
    except (TypeError, ValueError):
        return None

    return StoreProduct.query.filter_by(id=normalized_id, is_active=True).first()


def get_store_product_by_slug(product_slug):
    slug = slugify_crop_name(product_slug)
    return StoreProduct.query.filter_by(slug=slug, is_active=True).first()


def get_all_store_products():
    return StoreProduct.query.filter_by(is_active=True).order_by(StoreProduct.rating.desc(), StoreProduct.name.asc()).all()


def find_store_product_by_name(name):
    query_name = str(name or "").strip()
    if not query_name:
        return None

    normalized_name = slugify_crop_name(query_name)
    for product in get_all_store_products():
        if product.slug == normalized_name or str(product.name).strip().lower() == query_name.lower():
            return product

    name_tokens = set(re.findall(r"[a-z0-9]+", query_name.lower()))
    if not name_tokens:
        return None

    scored_matches = []
    for product in get_all_store_products():
        product_tokens = set(re.findall(r"[a-z0-9]+", str(product.name).lower()))
        overlap = len(name_tokens & product_tokens)
        if overlap:
            scored_matches.append((overlap, float(product.rating or 0), product))

    scored_matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored_matches[0][2] if scored_matches else None


<<<<<<< HEAD
=======
def score_store_product_for_diagnosis(product, crop_name="", disease_name="", diagnostic_text=""):
    disease_key = normalize_disease_key(disease_name)
    crop_key = normalize_crop_key(crop_name)
    product_name = str(product.name or "").strip()
    product_text = " ".join(
        [
            product_name.lower(),
            str(product.category or "").lower(),
            str(product.description or "").lower(),
            " ".join(str(tag).lower() for tag in safe_json_loads(product.tags_json, [])),
        ]
    )

    score = float(product.rating or 0) * 0.08

    preferred_names = DISEASE_PRODUCT_PREFERENCES.get(disease_key, [])
    for index, preferred_name in enumerate(preferred_names):
        if str(preferred_name).strip().lower() == product_name.lower():
            score += 8.5 - (index * 0.9)

    if disease_key == "healthy":
        if product.category in {"Fertilizers", "Organic"}:
            score += 2.8
        if "soil" in product_text or "care" in product_text or "booster" in product_text:
            score += 2.0
    else:
        if product.category == "Pesticides":
            score += 2.8
        if any(keyword in disease_key for keyword in ("fung", "blight", "spot", "rust", "mildew", "mold")) and any(
            token in product_text for token in ("pesticide", "spray", "fungicide", "bio")
        ):
            score += 1.6
        if any(keyword in disease_key for keyword in ("virus", "mosaic", "curl", "whitefly", "aphid", "thrips", "mite", "armyworm", "borer", "pest")) and any(
            token in product_text for token in ("neem", "organic pest", "pesticide", "spray")
        ):
            score += 1.9
        if "brown spot" in disease_key and any(token in product_text for token in ("soil", "npk", "booster", "compost")):
            score += 1.8

    disease_tokens = set(re.findall(r"[a-z0-9]+", disease_key))
    crop_tokens = set(re.findall(r"[a-z0-9]+", str(crop_key)))
    product_tokens = set(re.findall(r"[a-z0-9]+", product_text))
    score += len(disease_tokens & product_tokens) * 0.8
    score += len(crop_tokens & product_tokens) * 0.35

    if diagnostic_text:
        diagnostic_tokens = set(re.findall(r"[a-z0-9]+", diagnostic_text.lower()))
        score += len(diagnostic_tokens & product_tokens) * 0.12

    return score


>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
def apply_store_filters(products, search_query="", active_category="All", sort_option="featured", recommended_slug=None):
    normalized_query = str(search_query or "").strip().lower()
    category = active_category if active_category in STORE_CATEGORY_ORDER else "All"

    filtered = []
    for product in products:
        if category != "All" and product["category"] != category:
            continue
        if normalized_query and normalized_query not in product["search_text"]:
            continue
        filtered.append(product)

    if sort_option == "price_low":
        filtered.sort(key=lambda item: (item["price"], -item["rating"], item["name"]))
    elif sort_option == "price_high":
        filtered.sort(key=lambda item: (-item["price"], -item["rating"], item["name"]))
    elif sort_option == "rating":
        filtered.sort(key=lambda item: (-item["rating"], item["price"], item["name"]))
    else:
        filtered.sort(
            key=lambda item: (
                0 if recommended_slug and item["slug"] == recommended_slug else 1,
                -item["rating"],
                item["price"],
                item["name"],
            )
        )

    return filtered


def build_store_page_context(search_query="", active_category="All", sort_option="featured", recommended_slug=None):
    serialized_products = [serialize_store_product(product) for product in get_all_store_products()]
    filtered_products = apply_store_filters(
        serialized_products,
        search_query=search_query,
        active_category=active_category,
        sort_option=sort_option,
        recommended_slug=recommended_slug,
    )
    recommended_product = next((product for product in serialized_products if product["slug"] == recommended_slug), None)
    featured_product = recommended_product or (serialized_products[0] if serialized_products else None)

    categories = []
    for category_name in STORE_CATEGORY_ORDER:
        if category_name == "All":
            meta = {
                "icon": "fa-border-all",
                "accent": "all",
                "description": "Browse the full agro-input catalog in one place.",
            }
            count = len(serialized_products)
        else:
            meta = get_store_category_meta(category_name)
            count = sum(1 for product in serialized_products if product["category"] == category_name)

        categories.append(
            {
                "name": category_name,
                "count": count,
                "icon": meta["icon"],
                "accent": meta["accent"],
                "description": meta["description"],
                "is_active": category_name == active_category,
            }
        )

    top_rated = max((product["rating"] for product in serialized_products), default=0)
    avg_rating = round(
        sum(product["rating"] for product in serialized_products) / max(len(serialized_products), 1),
        1,
    ) if serialized_products else 0

    return {
        "products": filtered_products,
        "all_products": serialized_products,
        "categories": categories,
        "search_query": search_query,
        "active_category": active_category if active_category in STORE_CATEGORY_ORDER else "All",
        "sort_option": sort_option if sort_option in {"featured", "price_low", "price_high", "rating"} else "featured",
        "count": len(filtered_products),
        "total_count": len(serialized_products),
        "featured_product": featured_product,
        "recommended_product": recommended_product,
        "top_rated": f"{top_rated:.1f}" if top_rated else "0.0",
        "avg_rating": f"{avg_rating:.1f}" if avg_rating else "0.0",
        "tools_count": sum(1 for product in serialized_products if product["category"] == "Tools"),
    }


def get_related_store_products(current_product, limit=4):
    related = [
        product
        for product in get_all_store_products()
        if product.id != current_product.id and product.category == current_product.category
    ]
    if len(related) < limit:
        existing_ids = {product.id for product in related}
        for product in get_all_store_products():
            if product.id == current_product.id or product.id in existing_ids:
                continue
            related.append(product)
            existing_ids.add(product.id)
            if len(related) >= limit:
                break
    return [serialize_store_product(product) for product in related[:limit]]


FULFILLMENT_STATUS_ORDER = ["pending", "confirmed", "delivered"]


def get_order_notes(order):
    return safe_json_loads(getattr(order, "notes_json", None), {}) if order is not None else {}


def set_order_notes(order, notes):
    if order is None:
        return
    payload = notes if isinstance(notes, dict) else {}
    order.notes_json = json.dumps(payload, ensure_ascii=False)


def get_fulfillment_status(order):
    notes = get_order_notes(order)
    status = str(notes.get("fulfillment_status") or "pending").strip().lower()
    return status if status in FULFILLMENT_STATUS_ORDER else "pending"


def set_fulfillment_status(order, new_status):
    status = str(new_status or "").strip().lower()
    if status not in FULFILLMENT_STATUS_ORDER:
        raise ValueError("Invalid fulfillment status")

    notes = get_order_notes(order)
    notes["fulfillment_status"] = status
    set_order_notes(order, notes)


def get_admin_mapped_product_for_disease(disease_name):
    key = normalize_disease_key(disease_name)
    if not key:
        return None

    mapping = DiseaseProductMapping.query.filter_by(disease_key=key).first()
    if mapping is None or mapping.product is None:
        return None

    if not bool(mapping.product.is_active):
        return None

    return mapping.product


<<<<<<< HEAD
def infer_disease_type_from_text(*values):
    diagnostic_text = " ".join(str(value or "") for value in values).lower()
    if not diagnostic_text.strip():
        return "general"
    if "healthy" in diagnostic_text or "no disease" in diagnostic_text:
        return "healthy"
    if any(keyword in diagnostic_text for keyword in ("virus", "viral", "mosaic", "curl")):
        return "virus"
    if any(keyword in diagnostic_text for keyword in ("bacterial", "bacteria")):
        return "bacteria"
    if any(keyword in diagnostic_text for keyword in ("pest", "insect", "aphid", "whitefly", "thrips", "mite", "borer", "worm", "vector")):
        return "insect"
    if any(keyword in diagnostic_text for keyword in ("deficiency", "chlorosis", "yellowing", "stress", "wilt", "nutrient")):
        return "stress"
    if any(keyword in diagnostic_text for keyword in ("blight", "mold", "mildew", "rust", "fungal", "fungus", "rot", "spot", "lesion")):
        return "fungus"
    return "general"


def get_library_disease_item_by_name(disease_name="", crop_name=""):
    normalized_name = normalize_disease_key(disease_name)
    normalized_crop = normalize_crop_key(crop_name) if str(crop_name or "").strip() else ""
    if not normalized_name:
        return None

    items = get_library_disease_items()
    for item in items:
        if normalize_disease_key(item["name"]) == normalized_name and (
            not normalized_crop or item["crop_key"] in {normalized_crop, "generic"}
        ):
            return item

    for item in items:
        if normalize_disease_key(item["name"]) == normalized_name:
            return item

    name_tokens = set(re.findall(r"[a-z0-9]+", normalized_name))
    if not name_tokens:
        return None

    scored_matches = []
    for item in items:
        item_name_tokens = set(re.findall(r"[a-z0-9]+", normalize_disease_key(item["name"])))
        item_crop_tokens = set(re.findall(r"[a-z0-9]+", " ".join(item.get("crops", [])).lower()))
        overlap = len(name_tokens & item_name_tokens)
        if not overlap:
            continue
        score = overlap * 3
        if normalized_crop and item["crop_key"] == normalized_crop:
            score += 2
        if normalized_crop and normalized_crop in item_crop_tokens:
            score += 1
        scored_matches.append((score, len(item.get("symptoms", [])), len(item.get("prevention", [])), item))

    scored_matches.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return scored_matches[0][3] if scored_matches else None


def extract_guidance_points(*values, limit=4):
    points = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        normalized = re.sub(r"\s+", " ", text.replace("+", ". ").replace(";", ". ").replace("|", ". "))
        chunks = [chunk.strip(" -") for chunk in re.split(r"\.\s+|,\s+", normalized) if chunk.strip(" -")]
        if not chunks:
            chunks = [normalized]
        points.extend(chunks)
    return unique_crop_list(points)[:limit]


def build_confidence_label(confidence):
    score = int(confidence or 0)
    if score >= 90:
        return "High confidence visual match"
    if score >= 80:
        return "Strong pattern match"
    if score >= 70:
        return "Moderate match, verify nearby leaves"
    return "Low confidence, confirm in the field"


def build_consult_expert_note(confidence, risk_level):
    score = int(confidence or 0)
    normalized_risk = str(risk_level or "").strip().lower()
    if normalized_risk == "high" or score < 70:
        return "Field verification recommended today before full-block spraying."
    if normalized_risk == "medium" or score < 85:
        return "Compare 3-5 nearby leaves and monitor spread over the next 24 hours."
    return "Start treatment in the affected patch and recheck the canopy in 24-48 hours."


def build_do_now_checklist(payload, library_item=None):
    actions = ["Inspect nearby plants for the same pattern before treating the full area."]
    organic_solution = str(payload.get("organic_solution") or "").strip()
    chemical_solution = str(payload.get("chemical_solution") or "").strip()

    if organic_solution:
        actions.append(f"Organic option: {organic_solution}")
    if chemical_solution:
        actions.append(f"Chemical option: {chemical_solution}")
    if library_item is not None:
        actions.extend(library_item.get("prevention", [])[:1])

    risk_level = str(payload.get("risk_level") or "").strip().lower()
    if risk_level == "high":
        actions.append("Rescout this patch within the next 24 hours.")
    else:
        actions.append("Review this patch again within 48 hours.")
    return unique_crop_list(actions)[:4]


def score_store_product_for_diagnosis(product, disease_type, diagnostic_text, crop_name=""):
    serialized = serialize_store_product(product)
    search_text = serialized["search_text"]
    profile = DISEASE_PRODUCT_RECOMMENDATION_PROFILES.get(disease_type, DISEASE_PRODUCT_RECOMMENDATION_PROFILES["general"])
    score = float(product.rating or 0)
    score += profile["category_boosts"].get(product.category, 0)

    for keyword, weight in profile["keywords"].items():
        if keyword in search_text:
            score += weight

    crop_tokens = set(re.findall(r"[a-z0-9]+", str(crop_name or "").lower()))
    product_tokens = set(re.findall(r"[a-z0-9]+", search_text))
    if crop_tokens:
        score += len(crop_tokens & product_tokens) * 1.5

    if diagnostic_text:
        diagnostic_tokens = set(re.findall(r"[a-z0-9]+", diagnostic_text))
        score += len(diagnostic_tokens & product_tokens) * 0.25

    return score


def build_store_recommendation_reason(payload, serialized_product, disease_type):
    disease_name = str(payload.get("disease") or "the detected issue").strip()
    crop_name = str(payload.get("crop") or "your crop").strip()
    approach_map = {
        "healthy": "preventive crop care and closer monitoring",
        "fungus": "foliar protection and spread control",
        "bacteria": "surface protection and sanitation support",
        "virus": "vector management and crop stress reduction",
        "insect": "pest knockdown and repeat scouting",
        "stress": "crop recovery and root-zone support",
        "general": "broad crop-care support",
    }
    approach = approach_map.get(disease_type, approach_map["general"])
    return f"{serialized_product['name']} is the closest in-catalog match for {crop_name} because this diagnosis suggests {approach} for {disease_name}."


def enrich_disease_response_payload(payload):
    enriched = dict(payload)
    confidence = clamp(int(enriched.get("confidence") or 0), 0, 99)
    if confidence == 0:
        confidence = 65
    risk_level = str(enriched.get("risk_level") or "").strip().title()
    if risk_level not in {"Low", "Medium", "High"}:
        risk_level = "Low" if confidence >= 88 else "Medium" if confidence >= 72 else "High"

    library_item = get_library_disease_item_by_name(
        disease_name=enriched.get("disease"),
        crop_name=enriched.get("crop"),
    )
    matched_symptoms = list(library_item.get("symptoms", [])) if library_item is not None else extract_guidance_points(enriched.get("symptoms"))
    prevention = enriched.get("prevention")
    if not isinstance(prevention, list):
        prevention = extract_guidance_points(prevention)
    prevention = unique_crop_list(
        [
            *(prevention or []),
            *(library_item.get("prevention", []) if library_item is not None else []),
        ]
    )[:4]

    if not prevention:
        prevention = unique_crop_list(
            extract_guidance_points(enriched.get("organic_solution"), enriched.get("chemical_solution"), enriched.get("cause"), limit=4)
        )

    symptom_summary = str(enriched.get("symptoms") or "").strip()
    if not symptom_summary:
        symptom_summary = "; ".join(matched_symptoms) if matched_symptoms else str(enriched.get("cause") or "").strip()

    symptom_rule = match_disease_symptom_rule(
        enriched.get("disease"),
        enriched.get("symptoms"),
        enriched.get("cause"),
        enriched.get("organic_solution"),
        enriched.get("chemical_solution"),
    )
    if symptom_rule:
        rule_issue = str(symptom_rule.get("issue") or "").strip()
        rule_cause = str(symptom_rule.get("cause") or "").strip()
        rule_solution = str(symptom_rule.get("solution") or "").strip()
        rule_prevention = str(symptom_rule.get("prevention") or "").strip()
        if rule_issue and rule_issue.lower() not in symptom_summary.lower():
            symptom_summary = f"{symptom_summary}; {rule_issue}" if symptom_summary else rule_issue
        if rule_cause and str(enriched.get("cause") or "").strip().lower() in {"", "no disease", "requires expert field inspection"}:
            enriched["cause"] = rule_cause
        if rule_solution and not str(enriched.get("chemical_solution") or "").strip():
            enriched["chemical_solution"] = rule_solution
        if rule_solution and not str(enriched.get("organic_solution") or "").strip():
            enriched["organic_solution"] = rule_solution
        if rule_prevention:
            prevention = unique_crop_list([*(prevention or []), rule_prevention])[:4]
        if not matched_symptoms and symptom_rule.get("key"):
            matched_symptoms = [str(symptom_rule.get("key"))]
        if str(enriched.get("best_product") or "").strip().lower() in {"", "n/a", "na", "none"}:
            product_hints = symptom_rule.get("products")
            if isinstance(product_hints, list):
                for hint in product_hints:
                    hinted_product = find_store_product_by_asset_hint(hint)
                    if hinted_product is not None:
                        enriched["best_product"] = str(getattr(hinted_product, "name", "") or "").strip()
                        break

    enriched["confidence"] = confidence
    enriched["risk_level"] = risk_level
    enriched["analysis_source"] = str(enriched.get("analysis_source") or "AI diagnosis").strip()
    enriched["confidence_label"] = build_confidence_label(confidence)
    enriched["why_this_result"] = str(enriched.get("diagnostic_reason") or enriched.get("explanation_hinglish") or "Leaf pattern matched known disease signals.").strip()
    enriched["consult_expert"] = build_consult_expert_note(confidence, risk_level)
    enriched["matched_symptoms"] = matched_symptoms[:4]
    enriched["prevention"] = prevention
    enriched["symptoms"] = symptom_summary
    enriched["do_now_checklist"] = build_do_now_checklist(enriched, library_item=library_item)
    if library_item is not None:
        enriched["library_url"] = f"/library/disease/{library_item['slug']}"
    return enriched


def resolve_store_recommendation(disease_name="", cause="", organic_solution="", chemical_solution="", best_product_name="", crop_name=""):
    diagnostic_summary = " ".join(
        [
            str(disease_name or ""),
            str(cause or ""),
            str(organic_solution or ""),
            str(chemical_solution or ""),
        ]
    ).lower()
    if any(term in diagnostic_summary for term in ("healthy", "no disease", "routine care", "continue monitoring")):
        return None

=======
def resolve_store_recommendation(disease_name="", cause="", organic_solution="", chemical_solution="", best_product_name="", crop_name=""):
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    admin_mapped = get_admin_mapped_product_for_disease(disease_name)
    if admin_mapped is not None:
        return admin_mapped

    if best_product_name and str(best_product_name).strip().lower() not in {"n/a", "na", "none"}:
        direct_product = find_store_product_by_name(best_product_name)
        if direct_product:
            return direct_product

<<<<<<< HEAD
    diagnostic_text = diagnostic_summary
    disease_type = infer_disease_type_from_text(disease_name, cause, organic_solution, chemical_solution)
=======
    disease_key = normalize_disease_key(disease_name)
    preferred_names = DISEASE_PRODUCT_PREFERENCES.get(disease_key, [])
    for preferred_name in preferred_names:
        preferred_product = find_store_product_by_name(preferred_name)
        if preferred_product is not None:
            return preferred_product

    diagnostic_text = " ".join(
        [
            str(crop_name or ""),
            str(disease_name or ""),
            str(cause or ""),
            str(organic_solution or ""),
            str(chemical_solution or ""),
        ]
    ).lower()
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057

    for keywords, mapped_name in STORE_DISEASE_PRODUCT_RULES:
        if any(keyword in diagnostic_text for keyword in keywords):
            if not mapped_name:
                return None
            mapped_product = find_store_product_by_name(mapped_name)
            if mapped_product:
                return mapped_product

<<<<<<< HEAD
    scored_matches = []
    for product in get_all_store_products():
        score = score_store_product_for_diagnosis(product, disease_type, diagnostic_text, crop_name=crop_name)
        scored_matches.append((score, float(product.rating or 0), product))

    scored_matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored_matches[0][2] if scored_matches and scored_matches[0][0] > 0 else None


def attach_store_recommendation(payload, best_product_name=""):
    payload = enrich_disease_response_payload(payload)
    disease_type = infer_disease_type_from_text(
        payload.get("disease"),
        payload.get("cause"),
        payload.get("organic_solution"),
        payload.get("chemical_solution"),
    )
=======
    if "fungicide" in diagnostic_text or "copper" in diagnostic_text:
        return find_store_product_by_name("Bio Pesticide")
    if "pest" in diagnostic_text or "neem" in diagnostic_text or "vector" in diagnostic_text:
        return find_store_product_by_name("Neem Oil")

    products = get_all_store_products()
    if not products:
        return None

    scored_products = []
    for product in products:
        score = score_store_product_for_diagnosis(
            product,
            crop_name=crop_name,
            disease_name=disease_name,
            diagnostic_text=diagnostic_text,
        )
        scored_products.append((score, float(product.rating or 0), product))

    scored_products.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score, _, best_product = scored_products[0]
    return best_product if best_score > 0.4 else None


def attach_store_recommendation(payload, best_product_name=""):
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    recommended_product = resolve_store_recommendation(
        disease_name=payload.get("disease"),
        cause=payload.get("cause"),
        organic_solution=payload.get("organic_solution"),
        chemical_solution=payload.get("chemical_solution"),
        best_product_name=best_product_name,
        crop_name=payload.get("crop"),
    )

    if recommended_product is None:
        payload["recommended_product"] = None
        payload.setdefault("best_product", "")
        payload.setdefault("product_link", "")
        return payload

    serialized_product = serialize_store_product(recommended_product)
<<<<<<< HEAD
    serialized_product["reason"] = build_store_recommendation_reason(payload, serialized_product, disease_type)
=======
    serialized_product["reason"] = (
        f"Recommended for {payload.get('disease', 'the detected issue')} based on the AI diagnosis and treatment context."
    )
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    payload["recommended_product"] = serialized_product
    payload["best_product"] = serialized_product["name"]
    payload["product_link"] = serialized_product["detail_url"]
    return payload


def create_razorpay_order(product, user, source):
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return None, "Razorpay credentials are not configured."

    request_payload = {
        "amount": int(product.price) * 100,
        "currency": RAZORPAY_CURRENCY,
        "receipt": f"agv-{user.id}-{product.id}-{int(time.time())}",
        "notes": {
            "user_id": str(user.id),
            "product_id": str(product.id),
            "source": str(source or "store"),
        },
    }

    headers = {
        "Authorization": f"Basic {b64encode(f'{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}'.encode('utf-8')).decode('ascii')}",
        "Content-Type": "application/json",
    }
    request_data = json.dumps(request_payload).encode("utf-8")

    try:
        response = urlopen(
            Request(
                "https://api.razorpay.com/v1/orders",
                data=request_data,
                headers=headers,
                method="POST",
            ),
            timeout=12,
        )
        return json.loads(response.read().decode("utf-8")), None
    except (HTTPError, URLError, OSError, ValueError) as error:
        return None, str(error)


def verify_razorpay_signature(order_id, payment_id, signature):
    if not (order_id and payment_id and signature and RAZORPAY_KEY_SECRET):
        return False

    generated_signature = hmac.new(
        RAZORPAY_KEY_SECRET.encode("utf-8"),
        f"{order_id}|{payment_id}".encode("utf-8"),
        sha256,
    ).hexdigest()
    return hmac.compare_digest(generated_signature, signature)


<<<<<<< HEAD
=======
def verify_razorpay_webhook_signature(raw_body, signature):
    """Verify Razorpay webhook signature (X-Razorpay-Signature) using webhook secret."""
    if not (raw_body and signature and RAZORPAY_WEBHOOK_SECRET):
        return False

    generated = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        sha256,
    ).hexdigest()

    try:
        return hmac.compare_digest(generated, str(signature))
    except Exception:
        return False


def _wallet_debit_exists_for_subscription_payment(user_id, payment_id):
    """Best-effort idempotency: check if a wallet debit was already applied for a subscription payment."""
    if not (user_id and payment_id):
        return False
    try:
        recent = (
            WalletTransaction.query.filter_by(user_id=int(user_id), direction="debit", reason="subscription_wallet_applied")
            .order_by(WalletTransaction.created_at.desc())
            .limit(50)
            .all()
        )
    except Exception:
        return False

    for tx in recent:
        meta = safe_json_loads(getattr(tx, "meta_json", None), {}) if tx is not None else {}
        if str(meta.get("payment_id") or "") == str(payment_id):
            return True
    return False


@app.route("/webhooks/razorpay", methods=["POST"])
def razorpay_webhook():
    """Razorpay webhook receiver (best-effort). Keeps orders/subscriptions in sync even if client verify fails."""
    signature = request.headers.get("X-Razorpay-Signature") or ""
    raw_body = request.get_data(cache=False) or b""

    # If webhook secret isn't configured, do not accept webhooks silently.
    if not RAZORPAY_WEBHOOK_SECRET:
        return jsonify({"success": False, "error": "Webhook secret not configured."}), 400

    if not verify_razorpay_webhook_signature(raw_body, signature):
        return jsonify({"success": False, "error": "Invalid webhook signature."}), 400

    payload = request.get_json(silent=True) or {}
    event_type = str(payload.get("event") or "").strip().lower()

    payment_entity = (
        (((payload.get("payload") or {}).get("payment") or {}).get("entity") or {})
        if isinstance(payload, dict)
        else {}
    )
    order_id = str(payment_entity.get("order_id") or "").strip()
    payment_id = str(payment_entity.get("id") or "").strip()

    if not order_id:
        # Signature is valid but payload isn't useful. Ack to avoid retries.
        return jsonify({"success": True, "message": "No order_id in webhook."}), 200

    # Subscription payments
    sub_payment = SubscriptionPayment.query.filter_by(razorpay_order_id=order_id).first()
    if sub_payment is not None:
        if sub_payment.status == "paid":
            return jsonify({"success": True, "message": "Subscription already paid."}), 200

        user = db.session.get(User, sub_payment.user_id) if sub_payment.user_id else None
        sub_payment.razorpay_payment_id = payment_id or sub_payment.razorpay_payment_id

        wallet_use = int(sub_payment.wallet_used_inr or 0)
        if user is not None and wallet_use and not _wallet_debit_exists_for_subscription_payment(user.id, sub_payment.id):
            # If wallet debit fails (balance changed), proceed without wallet discount to avoid blocking paid access.
            if not wallet_debit(user, wallet_use, "subscription_wallet_applied", {"payment_id": sub_payment.id, "plan": sub_payment.plan}):
                sub_payment.wallet_used_inr = 0

        if user is not None:
            apply_user_subscription(user, sub_payment.plan)

        sub_payment.status = "paid" if event_type in {"payment.captured", "payment.authorized"} or payment_id else "paid"
        db.session.commit()
        return jsonify({"success": True, "message": "Subscription updated via webhook."}), 200

    # Store orders
    order_record = StoreOrder.query.filter_by(razorpay_order_id=order_id).first()
    if order_record is not None:
        if str(order_record.status or "").strip().lower() == "paid":
            return jsonify({"success": True, "message": "Store order already paid."}), 200

        order_record.razorpay_payment_id = payment_id or order_record.razorpay_payment_id
        order_record.status = "paid"
        notes = get_order_notes(order_record)
        notes["verified_by_webhook"] = True
        notes["webhook_event"] = event_type or "unknown"
        notes["webhook_seen_at"] = datetime.now(timezone.utc).isoformat()
        set_order_notes(order_record, notes)
        db.session.commit()
        return jsonify({"success": True, "message": "Store order updated via webhook."}), 200

    # Unknown order id: acknowledge to avoid retries.
    return jsonify({"success": True, "message": "No matching order found."}), 200


>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
def lookup_kisan_dost_knowledge(query_text, crop_name):
    query_lower = (query_text or "").lower()
    query_tokens = set(re.findall(r"[a-z0-9]+", query_lower))
    crop_lower = (crop_name or "").lower()
    knowledge_entries = load_kisan_dost_knowledge()
    all_known_crops = {
        str(crop).lower()
        for entry in knowledge_entries
        for crop in entry.get("crops", [])
    }
    explicit_query_crops = {crop for crop in all_known_crops if crop and crop in query_lower}

    best_entry = None
    best_score = 0

    for entry in knowledge_entries:
        keywords = [str(keyword).lower() for keyword in entry.get("keywords", [])]
        crops = [str(crop).lower() for crop in entry.get("crops", [])]
        score = 0

        for keyword in keywords:
            keyword_tokens = set(re.findall(r"[a-z0-9]+", keyword))
            if keyword in query_lower:
                score += 3
            elif keyword_tokens and keyword_tokens.issubset(query_tokens):
                score += 2
            elif keyword_tokens & query_tokens:
                score += 1

        if crops and crop_lower:
            if any(crop in explicit_query_crops for crop in crops):
                score += 3
            elif not explicit_query_crops and crop_lower in crops:
                score += 2

        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry and best_score >= 2:
        return str(best_entry.get("answer", "")).strip() or None
    return None


def normalize_kisan_dost_crop_name(crop_name):
    crop_key = (crop_name or "").strip().lower()
    aliases = {
        "paddy": "Rice",
        "peddy": "Rice",
        "dhan": "Rice",
        "rice": "Rice",
        "gehun": "Wheat",
        "wheat": "Wheat",
        "makka": "Maize",
        "maize": "Maize",
        "corn": "Maize",
        "tamatar": "Tomato",
        "tomato": "Tomato",
        "aloo": "Potato",
        "potato": "Potato",
    }
    return aliases.get(crop_key, crop_name or "crop")


def sanitize_ai_chat_history(raw_history):
    history = []
    if not isinstance(raw_history, list):
        return history

    for item in raw_history[-8:]:
        if not isinstance(item, dict):
            continue
        role = "assistant" if str(item.get("role") or "").strip().lower() == "assistant" else "user"
        content = re.sub(r"\s+", " ", str(item.get("content") or "").strip())
        if not content:
            continue
        history.append({"role": role, "content": content[:500]})
    return history


def build_ai_chat_context_query(query_text, history):
    clean_query = re.sub(r"\s+", " ", str(query_text or "").strip())
    if not clean_query:
        return ""

    user_messages = [str(item.get("content") or "").strip() for item in history if item.get("role") == "user"]
    if user_messages and user_messages[-1].lower() == clean_query.lower():
        user_messages = user_messages[:-1]

    if not user_messages:
        return clean_query

    query_tokens = re.findall(r"[a-z0-9]+", clean_query.lower())
    followup_terms = {
        "ye", "yeh", "is", "isko", "iska", "iske", "iss", "isme",
        "wo", "woh", "us", "usko", "uska", "uske", "usme",
        "kya", "kaise", "kab", "kitna", "kyu", "kyon", "kyun",
        "karu", "kru", "karna", "ab", "abhi", "fir", "phir",
        "detail", "details", "phir", "next", "then",
    }
    should_merge_context = len(query_tokens) <= 6 or any(token in followup_terms for token in query_tokens)
    if not should_merge_context:
        return clean_query

    context_parts = user_messages[-2:] + [clean_query]
    return " ".join(part for part in context_parts if part)


def format_ai_chat_history_for_prompt(history):
    lines = []
    for item in history[-6:]:
        role = "Farmer" if item.get("role") == "user" else "Assistant"
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def detect_ai_chat_language(query_text):
    text = str(query_text or "").strip()
    if any("\u0b00" <= char <= "\u0b7f" for char in text):
        return "Odia"
    if any("\u0900" <= char <= "\u097f" for char in text):
        return "Hindi"
    if re.search(r"\b(kya|kaise|kyun|kyu|mera|mere|meri|patta|fasal|barish|khad|dawai|spray)\b", text.lower()):
        return "Hinglish"
    return "English"


def ask_groq_ai_crop_doctor(user, query, history):
    if not GROQ_API_KEY:
        return None

    conversation_history = list(history or [])
    if (
        conversation_history
        and conversation_history[-1].get("role") == "user"
        and str(conversation_history[-1].get("content") or "").strip().lower() == str(query or "").strip().lower()
    ):
        conversation_history = conversation_history[:-1]

    recent_conversation = format_ai_chat_history_for_prompt(conversation_history)
    faq_reference = load_ai_crop_doctor_faq_reference()
    answer_language = detect_ai_chat_language(query)

    system_prompt = (
        "You are 'AI Crop Doctor', a practical agriculture assistant for farmers. "
        "Use the provided FAQ reference first when it is relevant, and otherwise answer using solid agriculture best practices. "
        f"Reply in {answer_language}. "
        "If the user writes in Hinglish, answer in simple Hinglish. "
        "Keep answers short, clear, and useful for a farmer. "
        "For follow-up questions, use the recent conversation context before answering."
    )

    messages = [{"role": "system", "content": system_prompt}]
    if faq_reference:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Project FAQ reference. Use it when relevant to the user question:\n"
                    f"{faq_reference[:40000]}"
                ),
            }
        )
    if recent_conversation:
        messages.append({"role": "system", "content": f"Recent conversation:\n{recent_conversation}"})
    messages.append(
        {
            "role": "user",
            "content": (
                f"Farmer context: location {user.location or 'India'}, crop {user.crop_type or 'General'}.\n"
                f"Question: {query}"
            ),
        }
    )

    response_data = fetch_json(
        "https://api.groq.com/openai/v1/chat/completions",
        method="POST",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json_body={
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": 0.3,
        },
    )
    if not isinstance(response_data, dict):
        return None

    choices = response_data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = str(message.get("content") or "").strip() if isinstance(message, dict) else ""
    return content or None


def build_kisan_dost_reply(user, query, history=None):
    crop_name = normalize_kisan_dost_crop_name(user.crop_type or "crop")
    location_name = user.location or "aapke area"
    history = sanitize_ai_chat_history(history)
    query_text = build_ai_chat_context_query(query, history)
    query_lower = query_text.lower()
    _, farms = ensure_user_farm_setup(user)
    task_summary = build_task_summary(user, limit=2)
    weather_intent_terms = [
        "weather",
        "mausam",
        "rain",
        "barish",
        "baarish",
        "temperature",
        "temp",
        "tapman",
        "taapman",
        "humidity",
        "garmi",
        "thand",
        "wind",
        "hawa",
    ]
    weather_value_terms = ["temp", "temperature", "tapman", "taapman", "humidity", "rain", "barish", "baarish", "wind", "hawa"]
    weather_advisory_terms = ["garmi", "heat", "barish", "baarish", "rain", "wind", "hawa", "thand", "drainage", "waterlogging"]
    weather_query = any(word in query_lower for word in weather_intent_terms)

    assistant_weather = {
        "city": location_name,
        "temp": 32,
        "humidity": 62,
        "rainfall_mm": 3,
        "wind_speed": 4.5,
        "wind_speed_kmh": 16.2,
        "clouds": 28,
        "pressure": 1009,
        "lat": None,
        "lon": None,
        "updated_at": datetime.now().strftime("%I:%M %p"),
        "chart": [],
        "chart_polyline": "",
        "slider_percent": 62,
        "wind_deg": 90,
        "feels_like": 33,
        "description": "clear sky",
        "icon_code": "01d",
        "icon_url": build_weather_icon_url("01d"),
    }
    if weather_query:
        assistant_weather = fetch_weather_bundle(location_name)
    soil = build_soil_profile(user, assistant_weather)
    crop_health = build_crop_health(user, assistant_weather, soil)
    mandi_rates = build_mock_mandi_rates(location_name)
    knowledge_answer = lookup_kisan_dost_knowledge(query_text, crop_name)
    local_qa_answer = lookup_ai_crop_doctor_local_qa(query_text)
    project_faq_answer = lookup_ai_crop_doctor_project_faq(query_text)

    if local_qa_answer:
        return local_qa_answer

    if any(word in query_lower for word in ["mandi", "market", "price", "rate", "bhav"]):
        lead_rate = mandi_rates[0]
        return (
            f"{location_name} mandi me {lead_rate['crop']} ka sample rate Rs. {lead_rate['price']} per {lead_rate['unit']} chal raha hai. "
            f"Wheat aur Maize bhi dashboard ke mandi card me compare kar lo. "
            f"Bechne se pehle morning trend dekhna best rahega."
        )

    if any(word in query_lower for word in ["task", "reminder", "schedule", "calendar", "plan"]):
        if task_summary["preview"]:
            lead_task = task_summary["preview"][0]
            return (
                f"Aapke paas abhi {task_summary['open_count']} open farm tasks hain aur {task_summary['overdue_count']} overdue hain. "
                f"Sabse important task '{lead_task['title']}' hai, jo {lead_task['due_label'].lower()} ke saath listed hai. "
                f"Farms page me task planner se aur reminders add ya complete kar sakte ho."
            )
        return (
            f"Abhi aapke planner me koi open task nahi hai. "
            f"Farms page me irrigation, spray, fertilizer ya harvest ke reminders add kar lo. "
            f"Isse daily planning kaafi easy ho jayegi."
        )

    if any(word in query_lower for word in ["farm", "plot", "field"]):
        return (
            f"Aapke account me abhi {len(farms)} farm record saved hain aur primary location {location_name} set hai. "
            f"Alag crop ya alag village ke liye Farms page se naya plot add kar sakte ho. "
            f"Primary farm ko switch karoge to dashboard bhi uske hisaab se sync ho jayega."
        )

    if any(word in query_lower for word in ["paani", "water", "irrigation", "sinchai", "moisture"]):
        if knowledge_answer:
            return knowledge_answer
        if soil["moisture"] < 45:
            return (
                f"Soil moisture abhi {soil['moisture']}% ke aas paas hai, isliye light irrigation dena useful rahega. "
                f"Subah ya shaam me paani do taaki evaporation kam ho. "
                f"IoT card se pump simulation bhi on karke check kar sakte ho."
            )
        return (
            f"Soil moisture abhi {soil['moisture']}% hai, to immediate irrigation ki zarurat nahi lag rahi. "
            f"Agle cycle se pehle field ko observe karte raho. "
            f"Overwatering se root stress ho sakta hai."
        )

    if any(word in query_lower for word in ["soil", "mitti", "nitrogen", "ph", "fertilizer", "khad"]):
        if knowledge_answer:
            return knowledge_answer
        return (
            f"Aapki soil profile me pH {soil['ph']} aur nitrogen {soil['nitrogen']}% estimate ho raha hai. "
            f"{crop_name} ke liye balanced khad aur organic compost helpful rahega. "
            f"Soil Health module me detailed recommendation dekh lo."
        )

    if any(word in query_lower for word in ["disease", "bimari", "leaf", "patta", "infection"]):
        if knowledge_answer:
            return (
                f"{knowledge_answer} "
                f"Aur confirm karne ke liye Disease Detection module me leaf image upload kar do."
            )
        return (
            f"Agar {crop_name} ke patton par spots ya discoloration dikh raha hai to Disease Detection module me image upload karo. "
            f"Early inspection se spread control karna easy hota hai. "
                f"Field me infected leaves ko alag rakhna mat bhoolna."
            )

    if any(word in query_lower for word in ["spray", "dawai", "medicine", "pesticide", "fungicide", "insecticide", "dose"]):
        if knowledge_answer:
            return (
                f"{knowledge_answer} "
                f"Spray label dose aur waiting period ko follow karo, aur तेज barish ke din spray avoid karo."
            )
        return (
            f"Agar aap {crop_name} ke liye spray pooch rahe ho to pehle issue confirm karo, phir hi fungicide ya pesticide choose karo. "
            f"Subah ya shaam spray karo aur hawa ya barish ka chance ho to postpone kar do. "
            f"Dose label ke hisaab se hi rakhna best rahega."
        )

    if any(word in query_lower for word in ["keeda", "pest", "insect", "sucking", "worm", "caterpillar"]):
        if knowledge_answer:
            return knowledge_answer
        return (
            f"{crop_name} me pest pressure ho to affected leaves aur shoot ko closely inspect karo. "
            f"Sticky traps ya early spray planning se spread control karna easy hota hai. "
            f"Agar chaho to symptom ya pest ka naam batao, main aur exact step bata dunga."
        )

    if weather_query:
        if any(word in query_lower for word in weather_value_terms):
            return (
                f"{assistant_weather['city']} me abhi temperature {assistant_weather['temp']} C hai aur feels like {assistant_weather['feels_like']} C jaisa lag raha hai. "
                f"Humidity {assistant_weather['humidity']}% hai aur weather {assistant_weather['description']} hai. "
                f"Agar chaho to main barish ya irrigation advice bhi bata sakta hoon."
            )
        if knowledge_answer and any(word in query_lower for word in weather_advisory_terms):
            return knowledge_answer
        return (
            f"{location_name} ke liye current advisory: temp lagbhag {assistant_weather['temp']} C aur humidity {assistant_weather['humidity']}% maan ke chalo. "
            f"Aise weather me {crop_name} ke liye subah monitoring aur controlled irrigation best rahegi. "
            f"Barish ka chance lage to drainage ready rakho."
        )

    if any(word in query_lower for word in ["yield", "upaj", "production", "harvest"]):
        if knowledge_answer:
            return knowledge_answer
        return (
            f"{crop_name} ka health score abhi {crop_health['score']}% estimate ho raha hai aur yield readiness {crop_health['yield_prediction']}% ke around hai. "
            f"Moisture aur nutrient balance maintain rakhoge to output better milega. "
            f"Dashboard ka crop health card daily monitor karte raho."
        )

    if knowledge_answer:
        return knowledge_answer

    if project_faq_answer:
        return project_faq_answer

    return (
        f"{crop_name} ke liye abhi best focus moisture, soil balance, aur timely monitoring par rakho. "
        f"Agar aap mandi, irrigation, disease, ya weather me se kisi specific cheez par sawal puchoge to main aur exact advice de sakta hoon. "
        f"Dashboard ke cards bhi live guidance ke liye ready hain."
    )


def fetch_json(url, params=None, method="GET", headers=None, json_body=None, form_body=None):
    request_headers = dict(headers or {})
    payload = None

    if params:
        url = f"{url}?{urlencode(params, doseq=True)}"

    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif form_body is not None:
        payload = urlencode(form_body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    try:
        req = Request(url, data=payload, headers=request_headers, method=method)
        with urlopen(req, timeout=API_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def fetch_bytes(url, params=None, method="GET", headers=None, json_body=None):
    request_headers = dict(headers or {})
    payload = None

    if params:
        url = f"{url}?{urlencode(params, doseq=True)}"

    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    try:
        req = Request(url, data=payload, headers=request_headers, method=method)
        with urlopen(req, timeout=API_TIMEOUT_SECONDS) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError):
        return None


def normalize_timestamp(value):
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def format_relative_time(value):
    timestamp = normalize_timestamp(value)
    if timestamp is None:
        return "Just now"

    delta = datetime.now(timezone.utc) - timestamp
    total_seconds = max(int(delta.total_seconds()), 0)
    days = total_seconds // 86400
    hours = total_seconds // 3600
    minutes = total_seconds // 60

    if days >= 30:
        return timestamp.strftime("%d %b %Y")
    if days >= 1:
        return f"{days}d ago"
    if hours >= 1:
        return f"{hours}h ago"
    if minutes >= 1:
        return f"{minutes}m ago"
    return "Just now"


def parse_due_date_input(raw_value):
    raw_text = (raw_value or "").strip()
    if not raw_text:
        return None
    try:
        return datetime.strptime(raw_text, "%Y-%m-%d").date()
    except ValueError:
        return None


def build_default_farm_name(user, index=1):
    crop_label = normalize_kisan_dost_crop_name(user.crop_type or "").strip()
    if crop_label and crop_label.lower() != "crop":
        return f"{crop_label} Plot {index}"
    owner_name = (user.name or "Farm").split()[0]
    return f"{owner_name}'s Farm {index}"


def get_or_create_user_preferences(user, commit=True):
    preferences = UserPreference.query.filter_by(user_id=user.id).first()
    changed = False

    if preferences is None:
        preferences = UserPreference(  # type: ignore
            user_id=user.id,
            alert_email=user.email or "",
            alert_phone=user.phone or "",
        )
        db.session.add(preferences)
        changed = True
    else:
        if not preferences.alert_email and user.email:
            preferences.alert_email = user.email
            changed = True
        if not preferences.alert_phone and user.phone:
            preferences.alert_phone = user.phone
            changed = True

    if changed and commit:
        db.session.commit()

    return preferences


def sync_user_from_primary_farm(user, primary_farm):
    changed = False
    for field_name in ("location", "crop_type", "farm_size"):
        user_value = getattr(user, field_name)
        farm_value = getattr(primary_farm, field_name)

        if not farm_value and user_value:
            setattr(primary_farm, field_name, user_value)
            farm_value = user_value
            changed = True

        if farm_value and farm_value != user_value:
            setattr(user, field_name, farm_value)
            changed = True

    if not primary_farm.name:
        primary_farm.name = build_default_farm_name(user)
        changed = True

    return changed


def ensure_user_farm_setup(user, commit=True):
    farms = Farm.query.filter_by(user_id=user.id).order_by(Farm.is_primary.desc(), Farm.created_at.asc()).all()
    changed = False

    if not farms:
        primary_farm = Farm(  # type: ignore
            user_id=user.id,
            name=build_default_farm_name(user),
            location=user.location or "",
            crop_type=user.crop_type or "",
            farm_size=user.farm_size or "",
            notes="Imported from your profile details.",
            is_primary=True,
        )
        db.session.add(primary_farm)
        farms = [primary_farm]
        changed = True

    primary = next((farm for farm in farms if farm.is_primary), None)
    if primary is None and farms:
        primary = farms[0]
        primary.is_primary = True
        changed = True

    if primary is not None:
        for farm in farms:
            if farm is not primary and farm.is_primary:
                farm.is_primary = False
                changed = True
        changed = sync_user_from_primary_farm(user, primary) or changed

    if changed and commit:
        db.session.commit()

    farms = Farm.query.filter_by(user_id=user.id).order_by(Farm.is_primary.desc(), Farm.created_at.asc()).all()
    primary = next((farm for farm in farms if farm.is_primary), farms[0] if farms else None)
    return primary, farms


def format_task_due_label(task):
    if task.due_date is None:
        return "No deadline"
    today = datetime.now().date()
    if task.status == "done":
        return f"Completed {format_relative_time(task.completed_at or task.created_at)}"
    if task.due_date < today:
        days_overdue = (today - task.due_date).days
        return f"Overdue by {days_overdue}d"
    if task.due_date == today:
        return "Due today"
    if task.due_date == today + timedelta(days=1):
        return "Due tomorrow"
    return task.due_date.strftime("%d %b %Y")


def serialize_task(task):
    status_label = {
        "todo": "To do",
        "in_progress": "In progress",
        "done": "Done",
    }.get(task.status, "To do")
    priority_label = {
        "low": "Low",
        "medium": "Medium",
        "high": "High",
    }.get(task.priority, "Medium")
    overdue = bool(task.due_date and task.status != "done" and task.due_date < datetime.now().date())

    return {
        "id": task.id,
        "title": task.title,
        "details": task.details or "No notes added yet.",
        "farm_id": task.farm_id,
        "farm_name": task.farm.name if task.farm else "All farms",
        "category": task.category or "General",
        "priority": task.priority or "medium",
        "priority_label": priority_label,
        "status": task.status or "todo",
        "status_label": status_label,
        "due_date": task.due_date,
        "due_label": format_task_due_label(task),
        "overdue": overdue,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    }


def build_task_summary(user, limit=4):
    tasks = FarmTask.query.filter_by(user_id=user.id).all()
    items = [serialize_task(task) for task in tasks]

    def sort_key(item):
        status_rank = 2 if item["status"] == "done" else 0
        due_date = item["due_date"] or date.max
        overdue_rank = 0 if item["overdue"] else 1
        return (status_rank, overdue_rank, due_date, item["title"].lower())

    items.sort(key=sort_key)

    open_items = [item for item in items if item["status"] != "done"]
    completed_items = [item for item in items if item["status"] == "done"]
    today = datetime.now().date()

    return {
        "all": items,
        "preview": open_items[:limit] if open_items else completed_items[:limit],
        "open_count": len(open_items),
        "completed_count": len(completed_items),
        "overdue_count": sum(1 for item in open_items if item["overdue"]),
        "due_today_count": sum(1 for item in open_items if item["due_date"] == today),
    }


def build_recent_activity(user, limit=8):
    activity_items: list[dict] = []

    for farm in Farm.query.filter_by(user_id=user.id).order_by(Farm.created_at.desc()).limit(4).all():
        activity_items.append(
            {
                "tone": "farm",
                "icon": "fa-tractor",
                "title": f"Farm saved: {farm.name}",
                "detail": f"{farm.location or 'Location pending'} | {farm.crop_type or 'Crop pending'}",
                "timestamp": farm.created_at,
            }
        )

    for task in FarmTask.query.filter_by(user_id=user.id).order_by(FarmTask.created_at.desc()).limit(6).all():
        activity_items.append(
            {
                "tone": "task" if task.status != "done" else "success",
                "icon": "fa-list-check",
                "title": f"Task {serialize_task(task)['status_label'].lower()}: {task.title}",
                "detail": f"{task.category or 'General'} | {format_task_due_label(task)}",
                "timestamp": task.completed_at or task.created_at,
            }
        )

    for record in DiseaseHistory.query.filter_by(user_id=user.id).order_by(DiseaseHistory.date.desc()).limit(4).all():
        activity_items.append(
            {
                "tone": "disease",
                "icon": "fa-bug",
                "title": f"Disease scan: {record.detected_disease}",
                "detail": f"{record.crop_type or 'Crop'} | Confidence {record.confidence}%",
                "timestamp": record.date,
            }
        )

    for post in CommunityPost.query.filter_by(user_id=user.id).order_by(CommunityPost.date.desc()).limit(3).all():
        activity_items.append(
            {
                "tone": "community",
                "icon": "fa-comments",
                "title": f"Community post: {post.title}",
                "detail": f"{post.category or 'General'} discussion shared by you",
                "timestamp": post.date,
            }
        )

    activity_items.sort(key=lambda item: normalize_timestamp(item["timestamp"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    for item in activity_items:
        item["time_ago"] = format_relative_time(item["timestamp"])

    return activity_items[:limit]


def build_dashboard_personalization(user, primary_farm, recommendations, task_summary):
    crop_name = (getattr(user, "crop_type", "") or "").strip() or "your crop"
    farm_name = getattr(primary_farm, "name", "") or "your farm"
    open_task_count = int(task_summary.get("open_count") or 0)

    if open_task_count > 0:
        detail = f"{open_task_count} farm task{'s' if open_task_count != 1 else ''} need attention for {farm_name}."
        action = {"label": "Open Planner", "href": "/farms#task-planner", "detail": "Review pending work"}
    elif recommendations:
        detail = f"Fresh AI guidance is ready for {crop_name} planning and crop care."
        action = {"label": "View AI Tips", "href": "#step2-ai-tip", "detail": "Read recommendations"}
    else:
        detail = f"Your dashboard is tuned for {crop_name} monitoring in {farm_name}."
        action = {"label": "Detect Disease", "href": "/disease-detection", "detail": "Run a new scan"}

    return {
        "headline": f"Farm plan ready for {crop_name}",
        "detail": detail,
        "primary_action": action,
    }


def build_dashboard_onboarding(user, farms, task_summary):
    has_crop = bool((getattr(user, "crop_type", "") or "").strip())
    has_location = bool((getattr(user, "location", "") or "").strip())
    has_farm = len(farms) > 0
    has_tasks = bool(task_summary.get("all"))

    steps = [
        {
            "title": "Complete profile",
            "detail": "Crop and location profile is ready." if has_crop and has_location else "Add crop and location to personalize insights.",
            "done": has_crop and has_location,
            "href": "/profile",
            "cta": "Update Profile",
        },
        {
            "title": "Set up farm",
            "detail": "Primary farm is connected." if has_farm else "Create your first farm workspace.",
            "done": has_farm,
            "href": "/farms",
            "cta": "Add Farm",
        },
        {
            "title": "Plan first task",
            "detail": "Task planner already has reminders." if has_tasks else "Create irrigation, spray, or harvest reminders.",
            "done": has_tasks,
            "href": "/farms#task-planner",
            "cta": "Add Task",
        },
        {
            "title": "Run AI scan",
            "detail": "Open disease detection to scan crop photos and get suggestions.",
            "done": False,
            "href": "/disease-detection",
            "cta": "Start Scan",
        },
    ]

    completed_count = sum(1 for step in steps if step["done"])
    total_count = len(steps)
    progress_pct = int(round((completed_count / total_count) * 100)) if total_count else 0

    return {
        "steps": steps,
        "completed_count": completed_count,
        "total_count": total_count,
        "progress_pct": progress_pct,
    }


def remember_notice(session_key, text, tone="success"):
    session[session_key] = {"text": text, "tone": tone}


def get_csrf_token():
    token = session.get("csrf_token")
    if token and isinstance(token, str):
        return token
    token = uuid.uuid4().hex
    session["csrf_token"] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token}


def csrf_matches(candidate):
    expected = session.get("csrf_token") or ""
    cand = str(candidate or "")
    if not expected or not cand:
        return False
    try:
        return hmac.compare_digest(str(expected), cand)
    except Exception:
        return False


def require_csrf():
    """Validate CSRF for form posts / JSON posts. Returns a response on failure, else None."""
    token = request.form.get("csrf_token") if request.form else None
    if not token:
        token = request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token")

    if csrf_matches(token):
        return None

    # JSON-friendly response for API callers.
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"success": False, "error": "CSRF validation failed. Please refresh and try again."}), 400
    return render_template("login.html", error="Security check failed. Please refresh the page and try again."), 400

def get_current_user():
    user = None
    if "user_id" in session:
        user = db.session.get(User, session["user_id"])

    if user is None and "user" in session:
        user = User.query.filter_by(name=session["user"]).first()

    if user is not None:
        # Backward compatible: if older users don't have a trial start date, start it now.
        if getattr(user, "trial_start_date", None) is None:
            try:
                user.trial_start_date = datetime.now(timezone.utc)
                db.session.commit()
            except Exception:
                db.session.rollback()
        ensure_user_farm_setup(user)
        get_or_create_user_preferences(user)

    return user


def build_chart_series(raw_points=None):
    if not raw_points:
        raw_points = [
            {"label": "Mon", "value": 29},
            {"label": "Tue", "value": 31},
            {"label": "Wed", "value": 34},
            {"label": "Thu", "value": 30},
            {"label": "Fri", "value": 33},
            {"label": "Sat", "value": 36},
        ]

    values = [float(point["value"]) for point in raw_points]
    low = min(values)
    high = max(values)
    spread = max(high - low, 1)

    chart = []
    polyline_points = []

    for index, point in enumerate(raw_points):
        normalized = (point["value"] - low) / spread
        bar_height = clamp(int(24 + normalized * 66), 24, 92)
        line_bottom = clamp(int(18 + normalized * 70), 18, 92)
        left = 0 if len(raw_points) == 1 else round(float(index * 100) / (len(raw_points) - 1), 2)  # type: ignore

        chart.append(
            {
                "label": point["label"],
                "value": round(point["value"], 1),
                "bar_height": bar_height,
                "line_bottom": line_bottom,
                "left": left,
            }
        )
        polyline_points.append(f"{left},{100 - line_bottom}")  # type: ignore

    return chart, " ".join(polyline_points)


def build_weather_icon_url(icon_code):
    if not icon_code:
        return "https://openweathermap.org/img/wn/01d@2x.png"
    return f"https://openweathermap.org/img/wn/{icon_code}@2x.png"


def degrees_to_compass(degrees):
    if degrees is None:
        return "N"

    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    index = round(degrees / 45) % len(directions)
    return directions[index]


def fetch_forecast_payload(lat, lon):
    if lat is None or lon is None or not OPENWEATHER_API_KEY:
        return None

    return fetch_json(
        "https://api.openweathermap.org/data/2.5/forecast",
        params={
            "lat": lat,
            "lon": lon,
            "appid": OPENWEATHER_API_KEY,
            "units": "metric",
        },
    )


def fetch_onecall_daily_payload(lat, lon):
    if lat is None or lon is None or not OPENWEATHER_API_KEY:
        return None

    return fetch_json(
        "https://api.openweathermap.org/data/3.0/onecall",
        params={
            "lat": lat,
            "lon": lon,
            "exclude": "minutely,hourly",
            "appid": OPENWEATHER_API_KEY,
            "units": "metric",
        },
    )


def fetch_weather_bundle(location):
    fallback_chart, fallback_polyline = build_chart_series()
    fallback = {
        "city": location or "Bhubaneswar",
        "country": "IN",
        "temp": 32,
        "description": "clear sky",
        "humidity": 62,
        "rainfall_mm": 3,
        "wind_speed": 4.5,
        "clouds": 28,
        "pressure": 1009,
        "lat": None,
        "lon": None,
        "updated_at": datetime.now().strftime("%I:%M %p"),
        "chart": fallback_chart,
        "chart_polyline": fallback_polyline,
        "slider_percent": 62,
        "wind_deg": 90,
        "wind_speed_kmh": 16.2,
        "feels_like": 33,
        "icon_code": "01d",
        "icon_url": build_weather_icon_url("01d"),
    }

    if not OPENWEATHER_API_KEY or not location:
        return fallback

    current_data = fetch_json(
        "https://api.openweathermap.org/data/2.5/weather",
        params={
            "q": location,
            "appid": OPENWEATHER_API_KEY,
            "units": "metric",
        },
    )

    if not current_data or str(current_data.get("cod", "200")) != "200":
        return fallback

    coord = current_data.get("coord") or {}  # type: ignore
    main = current_data.get("main") or {}  # type: ignore
    weather_items = current_data.get("weather") or [{}]  # type: ignore
    rain = current_data.get("rain") or {}  # type: ignore
    forecast_chart = fallback_chart
    forecast_polyline = fallback_polyline

    lat = coord.get("lat")
    lon = coord.get("lon")

    if lat is not None and lon is not None:
        forecast_data = fetch_forecast_payload(lat, lon)

        if forecast_data and forecast_data.get("list"):
            forecast_points = []
            for item in forecast_data["list"][:6]:
                label = datetime.fromtimestamp(item["dt"]).strftime("%a")
                forecast_points.append(
                    {
                        "label": label,
                        "value": float(item.get("main", {}).get("temp", 0)),
                    }
                )
            forecast_chart, forecast_polyline = build_chart_series(forecast_points)

    rainfall_mm = rain.get("1h") or rain.get("3h") or 0
    weather_icon = weather_items[0].get("icon", "01d")
    wind_speed = float((current_data.get("wind") or {}).get("speed", fallback["wind_speed"]))  # type: ignore

    return {
        "city": current_data.get("name", location),  # type: ignore
        "country": (current_data.get("sys") or {}).get("country", ""),  # type: ignore
        "temp": round(float(main.get("temp", fallback["temp"]))),  # type: ignore
        "description": weather_items[0].get("description", fallback["description"]).title(),  # type: ignore
        "humidity": int(main.get("humidity", fallback["humidity"])),  # type: ignore
        "rainfall_mm": round(float(rainfall_mm), 1),  # type: ignore
        "wind_speed": round(wind_speed, 1),  # type: ignore
        "wind_speed_kmh": round(wind_speed * 3.6, 1),  # type: ignore
        "wind_deg": int((current_data.get("wind") or {}).get("deg", fallback["wind_deg"])),  # type: ignore
        "clouds": int((current_data.get("clouds") or {}).get("all", fallback["clouds"])),  # type: ignore
        "pressure": int(main.get("pressure", fallback["pressure"])),  # type: ignore
        "lat": lat,
        "lon": lon,
        "updated_at": datetime.now().strftime("%I:%M %p"),
        "chart": forecast_chart,
        "chart_polyline": forecast_polyline,
        "slider_percent": clamp(int((current_data.get("clouds") or {}).get("all", 40)), 20, 96),  # type: ignore
        "feels_like": round(float(main.get("feels_like", fallback["feels_like"]))),  # type: ignore
        "icon_code": weather_icon,
        "icon_url": build_weather_icon_url(weather_icon),
    }


def build_soil_profile(user, weather):
    seed_source = f"{user.id}-{user.email}-{user.location or ''}-{user.crop_type or ''}"
    seed = sum((index + 1) * ord(char) for index, char in enumerate(seed_source))

    ph_value = round(clamp(5.6 + (int(seed) % 13) / 10, 5.5, 7.2), 1)
    nitrogen = clamp(int(42 + (int(seed) % 22) - float(weather["humidity"]) / 8), 24, 88)
    moisture = clamp(int(weather["humidity"] * 0.82 + weather["rainfall_mm"] * 2.4 + (int(seed) % 10)), 28, 96)

    return {
        "ph": ph_value,
        "nitrogen": nitrogen,
        "moisture": moisture,
        "metrics": [
            {
                "label": "PH",
                "value_display": f"{ph_value:.1f}",
                "fill": clamp(int(((ph_value - 5.2) / 2.0) * 100), 20, 100),
                "tone": "green",
            },
            {
                "label": "Nitrogen",
                "value_display": f"{nitrogen}%",
                "fill": nitrogen,
                "tone": "green",
            },
            {
                "label": "Moisture",
                "value_display": f"{moisture}%",
                "fill": moisture,
                "tone": "blue",
            },
        ],
    }


def build_crop_health(user, weather, soil):
    comfort_score = clamp(int(100 - abs(weather["temp"] - 29) * 5), 26, 100)
    ph_bonus = clamp(int(100 - abs(soil["ph"] - 6.4) * 24), 30, 100)
    score = clamp(
        int(
            float(soil["moisture"]) * 0.34
            + soil["nitrogen"] * 0.21
            + comfort_score * 0.26
            + ph_bonus * 0.19
        ),
        24,
        96,
    )

    if score >= 78:
        label = "Excellent"
    elif score >= 58:
        label = "Stable"
    else:
        label = "Needs Attention"

    yield_prediction = clamp(int(score * 0.88 + soil["nitrogen"] * 0.12), 26, 97)

    return {
        "score": score,
        "label": label,
        "yield_prediction": yield_prediction,
        "crop_name": user.crop_type or "Mixed Crop",
    }


def build_recommendations(user, weather, soil, crop_health):
    crop_name = user.crop_type or "your crop"
    location_name = user.location or "your farm"
    recommendations: list[dict] = []
    crop_key = normalize_crop_key(crop_name)
    seen_titles = set()

    def add_recommendation(title, detail):
        key = normalize_disease_key(title)
        if key in seen_titles:
            return
        seen_titles.add(key)
        recommendations.append({"title": title, "detail": detail})

    if weather["rainfall_mm"] < 4:
        add_recommendation(
            "Plan a light irrigation cycle",
            f"Rainfall near {location_name} is low, so maintain steady moisture for {crop_name} without sudden dry stress.",
        )

    if float(soil.get("ph", 0)) < 6.1:
        add_recommendation(
            "Correct acidic soil balance",
            "Add lime or mature organic compost to bring the root zone closer to a balanced pH range.",
        )

    if weather["temp"] >= 34:
        add_recommendation(
            "Protect plants from heat stress",
            f"Shift irrigation, scouting, and spray work for {crop_name} to early morning or evening hours.",
        )

    if float(weather.get("humidity", 0)) >= 78:
        humidity_detail = {
            "rice": "High humidity can quickly raise rice blast or blight pressure, so scout lower and middle leaves closely.",
            "wheat": "Cool humid air can push rust pressure higher, so inspect stripe or pustule development early.",
            "tomato": "Dense tomato canopy may trap moisture and trigger blight or mold spread, so improve airflow now.",
            "potato": "Potato foliage can hold late blight pressure after humid nights, so inspect wet patches first.",
            "maize": "Humid canopy conditions can accelerate blight and gray leaf spotting in denser sections.",
        }.get(crop_key, "High humidity can increase disease pressure, so inspect leaf wetness zones and improve airflow.")
        add_recommendation("Watch disease pressure this week", humidity_detail)

    if float(soil.get("nitrogen", 0)) < 45:
        nitrogen_detail = {
            "rice": "Rice tillers may weaken under low nitrogen, so plan a split nutrient top-up before the next irrigation cycle.",
            "wheat": "Wheat can lose canopy strength under low nitrogen, so a balanced feed may help maintain vegetative growth.",
            "tomato": "Tomato plants may lose vigor under low nitrogen, so support growth with a measured nutrient top-up.",
            "potato": "Balanced nitrogen can help potato foliage recover without pushing too much soft growth.",
            "maize": "Maize responds well to staged nitrogen support before visible pale-leaf stress expands.",
        }.get(crop_key, f"{crop_name} will benefit from a balanced nutrient top-up within the next few days.")
        add_recommendation("Boost nitrogen before the next cycle", nitrogen_detail)

    crop_specific = {
        "rice": (
            "Keep standing water uniform",
            "Avoid repeated dry-wet shock in paddy blocks and watch for patchy stress in low-lying zones.",
        ),
        "wheat": (
            "Scout rust-prone leaves early",
            "Check leaf strips on the cooler side of the field first, especially after dew-heavy mornings.",
        ),
        "tomato": (
            "Open the tomato canopy",
            "Prune crowded lower leaves and improve airflow so fungal pressure does not build up after irrigation.",
        ),
        "potato": (
            "Inspect the lower potato canopy",
            "Older leaves usually show spotting first, so remove infected foliage before lesions climb upward.",
        ),
        "maize": (
            "Check dense maize rows",
            "Walk humid inner rows first because disease and pest stress usually builds faster in shaded canopy sections.",
        ),
    }.get(crop_key)
    if crop_specific:
        add_recommendation(crop_specific[0], crop_specific[1])

    if float(crop_health.get("score", 0)) < 72:
        add_recommendation(
            "Review weaker crop zones",
            "Compare the live map and vegetation preview to inspect weak patches before the stress expands to nearby plants.",
        )
    else:
        add_recommendation(
            "Review satellite and NDVI zones",
            "Use the live map and vegetation preview to confirm that all field blocks are staying even and stable.",
        )

    return list(recommendations[:4])  # type: ignore


def build_forecast_cards(weather, forecast_payload, onecall_payload):
    cards = []

    if onecall_payload and onecall_payload.get("daily"):
        for item in onecall_payload["daily"][:7]:
            weather_info = (item.get("weather") or [{}])[0] # type: ignore
            rain_amount = item.get("rain", 0) or 0
            temp_day = item.get("temp", {}).get("day", weather["temp"])  # type: ignore
            cards.append(
                {
                    "label": datetime.fromtimestamp(item["dt"]).strftime("%A"),
                    "summary": weather_info.get("main", "Clear"),
                    "temp": round(float(temp_day)),
                    "rainfall_mm": round(float(rain_amount), 1),  # type: ignore
                    "icon_url": build_weather_icon_url(weather_info.get("icon", weather["icon_code"])),  # type: ignore
                    "_timestamp": item["dt"],
                }
            )

    if cards:
        return [{key: value for key, value in item.items() if not key.startswith("_")} for item in cards]

    if not forecast_payload or not forecast_payload.get("list"):
        return [
            {
                "label": point["label"],
                "summary": weather["description"],
                "temp": round(point["value"]),
                "rainfall_mm": weather["rainfall_mm"],
                "icon_url": weather["icon_url"],
            }
            for point in weather["chart"][:7]
        ]

    grouped = {}
    leftovers = []
    for item in forecast_payload["list"]:
        item_dt = datetime.fromtimestamp(item["dt"])
        date_key = item_dt.strftime("%Y-%m-%d")
        distance_from_noon = abs(item_dt.hour - 12)
        existing = grouped.get(date_key)

        simplified = {
            "label": item_dt.strftime("%A"),
            "summary": (item.get("weather") or [{}])[0].get("main", "Clear"),
            "temp": round(float(item.get("main", {}).get("temp", weather["temp"]))),
            "rainfall_mm": round(float(item.get("rain", {}) and item.get("rain", {}).get("3h", 0) or 0), 1),  # type: ignore
            "icon_url": build_weather_icon_url((item.get("weather") or [{}])[0].get("icon", weather["icon_code"])),
            "_distance": distance_from_noon,
            "_timestamp": item["dt"],
        }

        leftovers.append(simplified)
        if existing is None or distance_from_noon < existing.get("_distance", 999):  # type: ignore
            grouped[date_key] = simplified  # type: ignore

    cards = list(grouped.values())
    cards.sort(key=lambda item: item["_timestamp"])

    if len(cards) < 7:
        for item in leftovers:
            if len(cards) >= 7:
                break
            if item not in cards:
                cards.append(item)  # type: ignore

    result = []
    for item in cards[:7]:  # type: ignore
        cleaned = dict(item)
        cleaned.pop("_distance", None)
        cleaned.pop("_timestamp", None)
        result.append(cleaned)
    return result


def build_weather_history_context(weather, forecast_cards):
    points = []
    for item in forecast_cards[:7]:
        label = item["label"][:3]
        value = float(item["temp"])
        points.append({"label": label, "value": value})

    if not points:
        points = [{"label": point["label"], "value": point["value"]} for point in weather["chart"][:6]]

    chart, polyline = build_chart_series(points)
    labels = [point["label"] for point in chart]
    values = [point["value"] for point in chart]

    high = max(values) if values else weather["temp"]
    low = min(values) if values else weather["temp"]
    mid = round(float(high + low) / 2, 1)  # type: ignore

    y_axis = [f"{round(high)}Â°", f"{round(mid)}Â°", f"{round(low)}Â°"]

    return {
        "points": chart,
        "polyline": polyline,
        "labels": labels,
        "y_axis": y_axis,
    }


def build_weather_advisories(weather, forecast_cards, onecall_payload):
    advisories: list[dict] = []

    if onecall_payload and onecall_payload.get("alerts"):
        for item in onecall_payload["alerts"][:2]:
            advisories.append(
                {
                    "kind": "warning",
                    "title": item.get("event", "Weather Warning"),
                    "detail": item.get("description", "Regional weather conditions need attention.")[:180],
                }
            )

    if float(weather.get("temp", 0)) >= 35:
        advisories.append(
            {
                "kind": "heat",
                "title": "Heatwave warning",
                "detail": f"Current temperature is {weather['temp']} C. Schedule irrigation and field work for cooler hours.",
            }
        )

    heavy_rain_day = next((item for item in forecast_cards if item["rainfall_mm"] >= 8), None)
    if heavy_rain_day:
        advisories.append(
            {
                "kind": "rain",
                "title": "Rainfall alert",
                "detail": f"Expected rainfall near {heavy_rain_day['label']} may affect drainage. Inspect waterlogging risk early.",
            }
        )

    if weather["wind_speed_kmh"] >= 18:
        advisories.append(
            {
                "kind": "wind",
                "title": "Wind advisory",
                "detail": f"Winds are moving at {weather['wind_speed_kmh']} km/h. Protect young crops and loose field covers.",
            }
        )

    if not advisories:
        advisories.append(
            {
                "kind": "stable",
                "title": "Stable conditions",
                "detail": "No major climate alerts right now. Continue routine field monitoring and weather checks.",
            }
        )

    return list(advisories[:2])  # type: ignore


def build_weather_page_context(user):
    weather = fetch_weather_bundle(user.location or "Bhubaneswar")
    forecast_payload = fetch_forecast_payload(weather["lat"], weather["lon"])
    onecall_payload = fetch_onecall_daily_payload(weather["lat"], weather["lon"])
    forecast_cards = build_forecast_cards(weather, forecast_payload, onecall_payload)

    tomorrow_rain = 0
    if forecast_cards:
        tomorrow_rain = max(forecast_cards[1]["rainfall_mm"] if len(forecast_cards) > 1 else forecast_cards[0]["rainfall_mm"], 0)

    history = build_weather_history_context(weather, forecast_cards)
    advisories = build_weather_advisories(weather, forecast_cards, onecall_payload)
    map_embed_url = build_map_embed_url(user.location, weather["lat"], weather["lon"])

    return {
        "current": {
            "temp": weather["temp"],
            "description": weather["description"],
            "icon_url": weather["icon_url"],
            "location": weather["city"],
        },
        "rainfall": {
            "value": round(float(tomorrow_rain), 1),  # type: ignore
            "label": "Tomorrow",
        },
        "wind": {
            "direction": degrees_to_compass(weather["wind_deg"]),
            "speed_kmh": round(float(weather["wind_speed_kmh"]), 1),  # type: ignore
        },
        "location_card": {
            "map_embed_url": map_embed_url,
            "maps_search_url": "https://www.google.com/maps/search/?api=1&"
            + urlencode({"query": user.location or weather["city"]}),
            "summary": f"{weather['city']}, {weather['country']}".strip(", "),
            "coordinates": (
                f"{weather['lat']:.3f}, {weather['lon']:.3f}"
                if weather["lat"] is not None and weather["lon"] is not None
                else "Coordinates unavailable"
            ),
            "wind_speed_kmh": weather["wind_speed_kmh"],
        },
        "forecast_cards": forecast_cards,
        "history": history,
        "advisories": advisories,
        "weather": weather,
    }


def build_axis_labels(values, decimals=1, suffix=""):
    high = max(values)
    low = min(values)
    mid = round(float(high + low) / 2, decimals)

    def format_value(value):
        if decimals == 0:
            return f"{int(round(value))}{suffix}"
        return f"{value:.{decimals}f}{suffix}"

    return [format_value(high), format_value(mid), format_value(low)]


def build_metric_history_context(raw_points, decimals=1, suffix=""):
    chart, polyline = build_chart_series(raw_points)
    values = [float(point["value"]) for point in chart]

    return {
        "points": chart,
        "polyline": polyline,
        "labels": [point["label"] for point in chart],
        "y_axis": build_axis_labels(values, decimals=decimals, suffix=suffix),
    }


def build_seeded_history(seed, base_value, labels, lower, upper, step, decimals=1):
    points = []
    midpoint = (len(labels) - 1) / 2

    for index, label in enumerate(labels):
        wave = ((seed // (index + 2)) % 7) - 3
        drift = (index - midpoint) * step * 0.18
        value = clamp(base_value + wave * step + drift, lower, upper)

        if decimals == 0:
            value = int(round(value))
        else:
            value = round(value, decimals)

        points.append({"label": label, "value": value})

    return points


def describe_ph_balance(ph_value):
    if ph_value < 5.8:
        return "Strongly Acidic", "acidic"
    if ph_value < 6.4:
        return "Slightly Acidic", "mild"
    if ph_value <= 6.8:
        return "Balanced", "balanced"
    return "Slightly Alkaline", "alkaline"


def classify_nutrient_level(value, low_cutoff, high_cutoff):
    if value < low_cutoff:
        return "Low", "low"
    if value < high_cutoff:
        return "Medium", "medium"
    return "High", "high"


def build_soil_page_context(user):
    weather = fetch_weather_bundle(user.location or "Bhubaneswar")
    soil = build_soil_profile(user, weather)
    crop_health = build_crop_health(user, weather, soil)

    seed_source = f"{user.id}-{user.email}-{user.location or ''}-{user.crop_type or ''}-soil"
    seed = sum((index + 3) * ord(char) for index, char in enumerate(seed_source))

    phosphorus = clamp(
        int(34 + (int(seed) % 24) + float(soil["moisture"]) * 0.12 - float(weather["rainfall_mm"]) * 0.35),  # type: ignore
        20,
        88,
    )
    potassium = clamp(
        int(58 + (int(seed) % 40) + float(weather["temp"]) * 0.7 - abs(float(soil["ph"]) - 6.4) * 8),  # type: ignore
        35,
        128,
    )

    ph_label, ph_tone = describe_ph_balance(soil["ph"])
    nitrogen_label, nitrogen_tone = classify_nutrient_level(soil["nitrogen"], 45, 68)
    phosphorus_label, phosphorus_tone = classify_nutrient_level(phosphorus, 38, 62)
    potassium_label, potassium_tone = classify_nutrient_level(potassium, 62, 92)

    nutrient_cards = [
        {
            "title": "Nitrogen",
            "value": soil["nitrogen"],
            "unit": "kg/ha",
            "label": nitrogen_label,
            "tone": nitrogen_tone,
        },
        {
            "title": "Phosphorus",
            "value": phosphorus,
            "unit": "kg/ha",
            "label": phosphorus_label,
            "tone": phosphorus_tone,
        },
        {
            "title": "Potassium",
            "value": potassium,
            "unit": "kg/ha",
            "label": potassium_label,
            "tone": potassium_tone,
        },
    ]

    soil_recommendations: list[str] = []

    if float(soil.get("nitrogen", 0)) < 45:  # type: ignore
        soil_recommendations.append("Apply nitrogen fertilizer in split doses.")
    if float(phosphorus) < 45:
        soil_recommendations.append("Use phosphorus-rich input before the next irrigation cycle.")
    if float(soil.get("moisture", 0)) < 55:  # type: ignore
        soil_recommendations.append("Monitor soil moisture levels and plan a light irrigation.")
    if float(soil.get("ph", 0)) < 6.1:  # type: ignore
        soil_recommendations.append("Add lime or compost to improve acidic soil balance.")
    if float(weather.get("rainfall_mm", 0)) >= 8:  # type: ignore
        soil_recommendations.append("Inspect drainage lines to avoid root-zone waterlogging.")

    fallback_recommendations = [
        "Continue field sampling to validate nutrient hotspots.",
        "Track soil readings weekly to compare zone-by-zone variability.",
        "Review compost and residue management before the next field cycle.",
    ]
    for item in fallback_recommendations:
        if len(soil_recommendations) >= 3:
            break
        if item not in soil_recommendations:
            soil_recommendations.append(item)

    history_labels = [
        (datetime.now() - timedelta(days=6 - offset)).strftime("%a")
        for offset in range(7)
    ]
    ph_points = build_seeded_history(seed, soil["ph"], history_labels, 5.4, 7.4, 0.12, decimals=1)
    moisture_points = build_seeded_history(
        seed + 17,  # type: ignore
        soil["moisture"],
        history_labels,
        28,
        96,
        3.4,
        decimals=0,
    )

    ndvi_params = {}
    if weather["lat"] is not None and weather["lon"] is not None:  # type: ignore
        ndvi_params = {"lat": weather["lat"], "lon": weather["lon"]}  # type: ignore

    texture_options = ["Loam", "Clay Loam", "Silt Loam", "Sandy Loam"]
    texture_label = texture_options[seed % len(texture_options)]

    return {
        "weather": weather,
        "soil": soil,
        "toolbar_items": [
            {"label": "Nitrogen", "value": f"{soil['nitrogen']} kg/ha"},
            {"label": "Moisture", "value": f"{soil['moisture']}%"},
            {"label": "Weather", "value": weather["description"]},
        ],
        "location": {
            "name": user.location or weather["city"],
            "maps_search_url": "https://www.google.com/maps/search/?api=1&"
            + urlencode({"query": user.location or weather["city"]}),
        },
        "gauge": {
            "value": soil["ph"],
            "label": ph_label,
            "tone": ph_tone,
            "fill": clamp(int(((float(soil["ph"]) - 5.0) / 2.4) * 100), 10, 100),  # type: ignore
            "footnote": f"Field moisture {soil['moisture']}%",
        },
        "nutrient_cards": nutrient_cards,
        "health_panel": {
            "score": crop_health["score"],
            "recommendations": list(soil_recommendations[:3]),  # type: ignore
            "button_label": "View Recommendations",
        },
        "history_cards": [
            {
                "title": "Soil pH",
                "tone": "gold",
                "chart": build_metric_history_context(ph_points, decimals=1),
            },
            {
                "title": "Moisture",
                "tone": "blue",
                "chart": build_metric_history_context(moisture_points, decimals=0, suffix="%"),
            },
        ],
        "summary_metrics": [
            {"label": "Soil pH", "value": f"{soil['ph']:.1f}", "tone": "gold"},
            {"label": "Organic Matter", "value": f"{clamp(round(2.2 + (int(seed) % 9) * 0.18, 1), 2.2, 4.8):.1f}%", "tone": "coral"},  # type: ignore
            {"label": "Nitrogen", "value": f"{soil['nitrogen']} kg/ha", "tone": "blue"},
            {"label": "Moisture", "value": f"{soil['moisture']}%", "tone": "slate"},
            {"label": "Last Sync", "value": weather["updated_at"], "tone": "green"},
        ],
        "map_card": {
            "image_url": "/dashboard/ndvi-preview" + (f"?{urlencode(ndvi_params)}" if ndvi_params else ""),
            "legend": [
                {"label": "pH", "value": f"{soil['ph']:.1f}", "tone": "blue"},
                {"label": "Nitrogen", "value": nitrogen_label, "tone": "green"},
                {"label": "Phosphorus", "value": phosphorus_label, "tone": "yellow"},
                {"label": "Potassium", "value": potassium_label, "tone": "orange"},
                {"label": "Moisture", "value": f"{soil['moisture']}%", "tone": "sky"},
                {"label": "Texture", "value": texture_label, "tone": "earth"},
            ],
        },
    }


def build_crop_monitoring_context(user):
    weather = fetch_weather_bundle(user.location or "Bhubaneswar")
    soil = build_soil_profile(user, weather)
    crop_health = build_crop_health(user, weather, soil)

    seed_source = f"{user.id}-{user.email}-{user.location or ''}-{user.crop_type or ''}-crop"
    seed = sum((index + 5) * ord(char) for index, char in enumerate(seed_source))

    ndvi_index = round(
        clamp(
            0.22
            + float(crop_health["score"]) / 150
            + float(soil["moisture"]) / 260  # type: ignore
            - abs(float(soil["ph"]) - 6.4) * 0.1  # type: ignore
            - float(weather["clouds"]) / 550,  # type: ignore
            0.22,
            0.88,
        ),
        2,
    )

    high_stress = clamp(
        int((100 - float(crop_health["score"])) * 0.58 + max(0, 55 - float(soil["moisture"])) * 0.25),  # type: ignore
        8,
        44,
    )
    moderate_stress = clamp(
        int(18 + abs(float(weather["temp"]) - 30) * 1.5 + abs(float(soil["ph"]) - 6.4) * 12),  # type: ignore
        14,
        38,
    )
    healthy_zone = clamp(100 - high_stress - moderate_stress, 24, 78)

    days_labels = [
        (datetime.now() - timedelta(days=6 - offset)).strftime("%a")
        for offset in range(7)
    ]
    ph_points = build_seeded_history(seed, soil["ph"], days_labels, 5.5, 7.2, 0.11, decimals=1)
    moisture_points = build_seeded_history(
        seed + 29,
        soil["moisture"],
        days_labels,
        30,
        92,
        3.1,
        decimals=0,
    )

    moisture_chart, _ = build_chart_series(moisture_points)
    ph_history = build_metric_history_context(ph_points, decimals=1)
    moisture_axis = build_axis_labels([float(point["value"]) for point in moisture_chart], decimals=0, suffix="%")

    satellite_age_days = 2 + (int(seed) % 4)
    ndvi_snapshot_score = clamp(int(ndvi_index * 100), 22, 88)

    alert_history: list[dict] = []

    if high_stress >= 28:
        alert_history.append(
            {
                "tone": "high",
                "title": "High Stress Detected",
                "days_ago": 2,
                "detail": "Check red zones for water stress, pest pressure, and disease spread.",
            }
        )

    if moderate_stress >= 22:
        alert_history.append(
            {
                "tone": "medium",
                "title": "Moderate Stress Observed",
                "days_ago": satellite_age_days,
                "detail": "Monitor affected sections closely and compare with recent rainfall patterns.",
            }
        )

    alert_history.append(
        {
            "tone": "healthy",
            "title": "Healthy Growth",
            "days_ago": 1 + (int(seed) % 3),
            "detail": "Healthy crop growth detected across the stable vegetation zones.",
        }
    )
    fallback_alerts = [
        {
            "tone": "medium",
            "title": "Field Review Suggested",
            "days_ago": 4,
            "detail": "Compare satellite zones with on-ground scouting before the next spray cycle.",
        },
        {
            "tone": "healthy",
            "title": "Canopy Holding Stable",
            "days_ago": 5,
            "detail": "Vegetation response remains stable across the stronger production blocks.",
        },
    ]
    for item in fallback_alerts:
        if len(alert_history) >= 3:
            break
        alert_history.append(item)
    alert_history = list(alert_history[:3])  # type: ignore

    recommended_actions = [
        {
            "step": 1,
            "tone": "medium",
            "text": "Increase irrigation in high-stress zones if moisture keeps dropping.",
        },
        {
            "step": 2,
            "tone": "healthy",
            "text": "Inspect red zones for pests, disease patches, or nutrient lockout.",
        },
        {
            "step": 3,
            "tone": "high",
            "text": "Apply fertilizer selectively in moderate-stress areas after field review.",
        },
    ]

    analysis_footer = [
        {"label": "Growth Analysis", "tone": "slate"},
        {"label": f"NDVI {ndvi_index:.2f}", "tone": "high"},
        {"label": f"{user.crop_type or 'Mixed Crop'}", "tone": "medium"},
        {"label": f"Scan {satellite_age_days} days ago", "tone": "healthy"},
    ]

    ndvi_params = {}
    if weather["lat"] is not None and weather["lon"] is not None:  # type: ignore
        ndvi_params = {"lat": weather["lat"], "lon": weather["lon"]}  # type: ignore

    return {
        "weather": weather,
        "soil": soil,
        "crop_health": crop_health,
        "location_label": user.location or weather["city"],
        "satellite_age_days": satellite_age_days,
        "ndvi_index": ndvi_index,
        "ndvi_snapshot_score": ndvi_snapshot_score,
        "zones": {
            "healthy": healthy_zone,
            "moderate": moderate_stress,
            "high": high_stress,
        },
        "image_url": "/dashboard/ndvi-preview" + (f"?{urlencode(ndvi_params)}" if ndvi_params else ""),
        "alert_history": alert_history,
        "recommended_actions": recommended_actions,
        "ph_history": ph_history,
        "moisture_history": {
            "points": moisture_chart,
            "labels": [point["label"] for point in moisture_chart],
            "y_axis": moisture_axis,
        },
        "analysis_footer": analysis_footer,
    }


def build_farm_twin_context(user):
    weather = fetch_weather_bundle(user.location or "Bhubaneswar")
    soil = build_soil_profile(user, weather)
    crop_health = build_crop_health(user, weather, soil)
    disease_result = build_default_disease_result(user, weather)
    recommendations = build_recommendations(user, weather, soil, crop_health)

    seed_source = f"{user.id}-{user.email}-{user.location or ''}-{user.crop_type or ''}-farm-twin"
    seed = sum((index + 7) * ord(char) for index, char in enumerate(seed_source))

    crop_name = ((user.crop_type or "Rice").strip().title()) or "Rice"
    companion_crops = ["Soybean", "Maize", "Vegetable", "Pulse", "Groundnut", "Mustard"]
    companion_crop = companion_crops[seed % len(companion_crops)]
    if companion_crop.lower() == crop_name.lower():
        companion_crop = companion_crops[(seed + 2) % len(companion_crops)]

    ndvi_level = round(
        clamp(
            0.28
            + float(crop_health["score"]) / 148
            + float(soil["moisture"]) / 320  # type: ignore
            - abs(float(soil["ph"]) - 6.4) * 0.08  # type: ignore
            - float(weather["clouds"]) / 420,  # type: ignore
            0.34,
            0.86,
        ),
        2,
    )
    ndvi_percent = clamp(int(ndvi_level * 100), 34, 86)
    cluster_nodes = clamp(int(2600 + (int(seed) % 1400) + float(soil["moisture"]) * 4), 2400, 4880)  # type: ignore
    patches_visible = clamp(int(22 + (int(seed) % 12) + float(weather["humidity"]) / 8), 22, 44)  # type: ignore
    predicted_rainfall = round(clamp(float(weather["rainfall_mm"]) * 1.9 + float(weather["humidity"]) / 11, 4.0, 28.0), 1)  # type: ignore
    heat_stress = clamp(int(max(0.0, float(weather["temp"]) - 28) * 8 + max(0.0, 58 - float(soil["moisture"])) * 0.5), 12, 84)  # type: ignore
    drought_risk = clamp(int(max(0.0, 60 - float(soil["moisture"])) * 1.1 + max(0.0, 5 - float(weather["rainfall_mm"])) * 6), 8, 78)  # type: ignore
    disease_risk = clamp(int((100 - float(crop_health["score"])) * 0.7 + float(weather["humidity"]) * 0.18), 18, 82)  # type: ignore
    predicted_yield = round(clamp(1.8 + float(crop_health["yield_prediction"]) / 29 + float(soil["nitrogen"]) / 160, 2.1, 5.4), 1)  # type: ignore

    nitrogen_label, _ = classify_nutrient_level(soil["nitrogen"], 45, 68)
    ph_label, _ = describe_ph_balance(soil["ph"])

    location_label = user.location or weather["city"]

    field_labels = [
        {"name": f"{crop_name} Field 1", "top": 47, "left": 32},
        {"name": f"{crop_name} Field 2", "top": 66, "left": 48},
        {"name": f"{companion_crop} Field", "top": 56, "left": 70},
    ]

    ndvi_params = {}
    if weather["lat"] is not None and weather["lon"] is not None:
        ndvi_params = {"lat": weather["lat"], "lon": weather["lon"]}  # type: ignore

    twin_map_url = "/dashboard/ndvi-preview" + (f"?{urlencode(ndvi_params)}" if ndvi_params else "")
    weather_card_image = (
        "https://images.unsplash.com/photo-1500382017468-9049fed747ef?auto=format&fit=crop&w=1200&q=80"
    )

    soil_insight = (
        f"Low nitrogen detected in {field_labels[1]['name']}"
        if soil["nitrogen"] < 45  # type: ignore
        else f"{ph_label} soil trend holding steady in {field_labels[0]['name']}"
    )
    disease_insight = f"{disease_result['disease']} risk detected in {field_labels[2]['name']}"

    return {
        "weather": weather,
        "soil": soil,
        "crop_health": crop_health,
        "location_label": location_label,
        "selector_label": f"Your Farm, {location_label}",
        "refresh_token": int(time.time()),
        "overview_cards": [
            {
                "title": "Satellite Monitoring",
                "summary": f"NDVI {ndvi_level:.2f} | {crop_health['label']}",
                "icon": "fa-solid fa-satellite-dish",
                "image_url": twin_map_url,
                "tone": "satellite",
            },
            {
                "title": "Weather Forecasting",
                "summary": f"Rain {predicted_rainfall:.1f} mm | {degrees_to_compass(weather['wind_deg'])} wind",
                "icon": "fa-solid fa-cloud-sun-rain",
                "image_url": weather_card_image,
                "tone": "weather",
            },
        ],
        "analysis": {
            "cluster_nodes": cluster_nodes,
            "patches_visible": patches_visible,
            "ndvi_level": ndvi_level,
            "ndvi_percent": ndvi_percent,
            "status_label": crop_health["label"],
            "metrics": [
                {"label": "Predicted Yield", "value": f"{predicted_yield:.1f} tons/acre"},
                {"label": "Heat Stress", "value": f"{heat_stress}%"},
                {"label": "Drought Risk", "value": f"{drought_risk}%"},
            ],
        },
        "map": {
            "image_url": twin_map_url,
            "labels": field_labels,
            "metrics": [
                {"label": "Yield", "value": f"{predicted_yield:.1f} t/acre"},
                {"label": "Rainfall", "value": f"{predicted_rainfall:.1f} mm"},
                {"label": "Disease Risk", "value": f"{disease_risk}%"},
            ],
        },
        "insights": [
            {
                "title": "Soil Health",
                "detail": soil_insight,
                "icon": "fa-solid fa-seedling",
                "tone": "soil",
                "support": f"Nitrogen {nitrogen_label} | Moisture {soil['moisture']}%",
            },
            {
                "title": "Disease Detection",
                "detail": disease_insight,
                "icon": "fa-solid fa-bug",
                "tone": "disease",
                "support": recommendations[0]["detail"] if recommendations else "Continue routine scouting across all field blocks.",
            },
        ],
    }


def normalize_crop_key(crop_name):
    crop_value = (crop_name or "").strip().lower()
    if not crop_value:
        return "generic"
    if "paddy" in crop_value or "rice" in crop_value:
        return "rice"
    if "wheat" in crop_value:
        return "wheat"
    if "tomato" in crop_value:
        return "tomato"
    if "potato" in crop_value:
        return "potato"
    if "maize" in crop_value or "corn" in crop_value:
        return "maize"
    return "generic"


def load_crop_disease_model():
    if DISEASE_MODEL_CACHE["attempted"]:
        return DISEASE_MODEL_CACHE["model"], DISEASE_MODEL_CACHE["labels"]

    DISEASE_MODEL_CACHE["attempted"] = True

    if torch is None or not CROP_DISEASE_MODEL_PATH.exists():
        return None, None

    try:
        device = torch.device('cpu') # type: ignore
        model = torch.load( # type: ignore
            str(CROP_DISEASE_MODEL_PATH),
            map_location=device,
            weights_only=False,
        )
        model.eval()
        DISEASE_MODEL_CACHE["model"] = model

        if CROP_DISEASE_LABELS_PATH.exists():
            with CROP_DISEASE_LABELS_PATH.open("r", encoding="utf-8") as handle:
                label_data = json.load(handle)

            if isinstance(label_data, list):
                DISEASE_MODEL_CACHE["labels"] = label_data  # type: ignore
            elif isinstance(label_data, dict):
                DISEASE_MODEL_CACHE["labels"] = [  # type: ignore
                    value for _, value in sorted(label_data.items(), key=lambda item: int(item[0]))
                ]
    except Exception as e:
        print(f"Failed to load crop disease model: {e}")
        DISEASE_MODEL_CACHE["model"] = None
        DISEASE_MODEL_CACHE["labels"] = None

    return DISEASE_MODEL_CACHE["model"], DISEASE_MODEL_CACHE["labels"]

def predict_with_pytorch(image):
    model, labels = load_crop_disease_model()
    if not model or not labels:
        return None, 0, None, 0
    
    transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])
    
    try:
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        img_t = transform(image)
        batch_t = torch.unsqueeze(img_t, 0) # type: ignore
        
        with torch.no_grad(): # type: ignore
            output = model(batch_t) # type: ignore
            
        probabilities = torch.nn.functional.softmax(output[0], dim=0) # type: ignore
        best_prob, best_idx = torch.max(probabilities, 0) # type: ignore
        
        class_index = str(best_idx.item())
        conf_float = best_prob.item()
        confidence = int(conf_float * 100) # type: ignore
        predicted_class_name = labels[best_idx.item()] # type: ignore
        
        return class_index, conf_float, predicted_class_name, confidence
    except Exception as e:
        print(f"Error predicting with model: {e}")
        return None, 0, None, 0

from disease_knowledge import get_disease_info

def select_disease_entry(crop_name, signals, seed=0):
    crop_key = normalize_crop_key(crop_name)
    library = CROP_DISEASE_LIBRARY.get(crop_key, CROP_DISEASE_LIBRARY["generic"])
    signal_set = set(signals or ())

    scored_entries = []
    for index, entry in enumerate(library):
        score = len(signal_set & set(entry["signals"]))
        score += 0.3 if score else 0
        tie_break = -abs((int(seed) % len(library)) - index)
        scored_entries.append((score, tie_break, entry))

    scored_entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score, _, best_entry = scored_entries[0]

    if best_score <= 0:
        best_entry = library[seed % len(library)]

    crop_display = (crop_name or str(crop_key).title() or "Crop").strip()
    if crop_display.lower() == "generic":
        crop_display = "Crop"

    return best_entry, crop_display

    return best_entry, crop_display


def build_disease_result(entry, crop_display, confidence, analysis_source):
    return {
        "crop": crop_display,
        "disease": entry["name"],
        "cause": entry["cause"],
        "solution": entry["solution"],
        "prevention_tips": entry["prevention_tips"],
        "confidence": clamp(int(confidence), 55, 99),
        "analysis_source": analysis_source,
    }


def build_disease_sample_data_uri(disease_name):
    disease_lower = (disease_name or "").lower()
    spot_color = "#d97a36"
    ring_color = "#915021"

    if "blight" in disease_lower:
        spot_color = "#8b5b30"
        ring_color = "#55301a"
    elif "mildew" in disease_lower:
        spot_color = "#eff1e4"
        ring_color = "#c7d0bf"
    elif "mosaic" in disease_lower or "yellow" in disease_lower:
        spot_color = "#e8c64e"
        ring_color = "#bc9226"

    svg = f"""
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 520 360">
  <defs>
    <linearGradient id="leafBg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#8fcd67"/>
      <stop offset="50%" stop-color="#58a24b"/>
      <stop offset="100%" stop-color="#2f6d38"/>
    </linearGradient>
    <linearGradient id="leafVein" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="rgba(255,255,255,0.34)"/>
      <stop offset="100%" stop-color="rgba(255,255,255,0.06)"/>
    </linearGradient>
  </defs>
  <rect width="520" height="360" rx="28" fill="#dce8d2"/>
  <g transform="translate(30 10) rotate(-10 230 160)">
    <path d="M55 330 C120 86 328 24 458 40 C390 120 360 262 250 330 Z" fill="url(#leafBg)"/>
    <path d="M252 52 L252 320" stroke="rgba(255,255,255,0.36)" stroke-width="7" stroke-linecap="round"/>
    <path d="M180 110 L252 140" stroke="rgba(255,255,255,0.24)" stroke-width="4" stroke-linecap="round"/>
    <path d="M325 128 L252 164" stroke="rgba(255,255,255,0.24)" stroke-width="4" stroke-linecap="round"/>
    <path d="M160 202 L252 220" stroke="rgba(255,255,255,0.2)" stroke-width="4" stroke-linecap="round"/>
    <path d="M318 228 L252 252" stroke="rgba(255,255,255,0.2)" stroke-width="4" stroke-linecap="round"/>
    <g fill="{spot_color}" stroke="{ring_color}" stroke-width="4" opacity="0.94">
      <circle cx="160" cy="110" r="18"/>
      <circle cx="222" cy="136" r="14"/>
      <circle cx="308" cy="108" r="22"/>
      <circle cx="350" cy="154" r="16"/>
      <circle cx="208" cy="214" r="19"/>
      <circle cx="275" cy="236" r="15"/>
      <circle cx="144" cy="260" r="17"/>
      <circle cx="352" cy="248" r="18"/>
    </g>
  </g>
</svg>
""".strip()
    return "data:image/svg+xml;utf8," + quote(svg)


def rgb_to_hsv_channels(image_array):
    rgb = np.clip(image_array / 255.0, 0.0, 1.0)
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]

    max_channel = np.max(rgb, axis=2)
    min_channel = np.min(rgb, axis=2)
    delta = max_channel - min_channel

    hue = np.zeros_like(max_channel)
    non_zero_delta = delta > 1e-6

    red_mask = non_zero_delta & (max_channel == red)
    green_mask = non_zero_delta & (max_channel == green)
    blue_mask = non_zero_delta & (max_channel == blue)

    hue[red_mask] = ((green[red_mask] - blue[red_mask]) / delta[red_mask]) % 6
    hue[green_mask] = ((blue[green_mask] - red[green_mask]) / delta[green_mask]) + 2
    hue[blue_mask] = ((red[blue_mask] - green[blue_mask]) / delta[blue_mask]) + 4
    hue = (hue / 6.0) % 1.0

    saturation = np.zeros_like(max_channel)
    non_zero_max = max_channel > 1e-6
    saturation[non_zero_max] = delta[non_zero_max] / max_channel[non_zero_max]

    return hue, saturation, max_channel


def normalize_feature_score(value, peak):
    if peak <= 0:
        return 0.0
    return clamp(float(value) / peak, 0.0, 1.45)


def build_leaf_mask(image_array):
    red = image_array[:, :, 0]
    green = image_array[:, :, 1]
    blue = image_array[:, :, 2]

    near_white = (red > 244) & (green > 244) & (blue > 244)
    near_black = (red < 10) & (green < 10) & (blue < 10)
    return ~(near_white | near_black)


def extract_leaf_features(image, weather):
    analysis_image = ImageOps.fit(image, (256, 256))
    image_array = np.asarray(analysis_image, dtype=np.float32)
    hue, saturation, value = rgb_to_hsv_channels(image_array)
    leaf_mask = build_leaf_mask(image_array)
    leaf_pixels = max(int(np.count_nonzero(leaf_mask)), 1)

    def masked_ratio(mask):
        return float(np.count_nonzero(mask & leaf_mask)) / leaf_pixels

    green_mask = (
        (hue > 0.20)
        & (hue < 0.45)
        & (saturation > 0.16)
        & (value > 0.18)
    )
    brown_mask = (
        (hue > 0.04)
        & (hue < 0.15)
        & (saturation > 0.22)
        & (value > 0.15)
        & (value < 0.78)
    )
    yellow_mask = (
        (hue > 0.11)
        & (hue < 0.19)
        & (saturation > 0.18)
        & (value > 0.42)
    )
    white_mask = (saturation < 0.16) & (value > 0.74)
    dark_mask = value < 0.28
    gray_mask = (saturation < 0.12) & (value > 0.34) & (value < 0.72)
    warm_spot_mask = brown_mask & (image_array[:, :, 0] > image_array[:, :, 1] + 10)

    lesion_mask = brown_mask | yellow_mask | white_mask | dark_mask | gray_mask

    edge_band = np.zeros_like(leaf_mask, dtype=bool)
    edge_band[:22, :] = True
    edge_band[-22:, :] = True
    edge_band[:, :22] = True
    edge_band[:, -22:] = True

    stripe_ratio = float(np.mean((np.sum(lesion_mask, axis=0) / lesion_mask.shape[0]) > 0.18))
    mottled_ratio = float(np.std(hue[leaf_mask])) if np.any(leaf_mask) else 0.0
    texture_value = float(image_array[leaf_mask].std()) if np.any(leaf_mask) else float(image_array.std())

    features = {
        "brown_ratio": masked_ratio(brown_mask),
        "yellow_ratio": masked_ratio(yellow_mask),
        "white_ratio": masked_ratio(white_mask),
        "dark_ratio": masked_ratio(dark_mask),
        "gray_ratio": masked_ratio(gray_mask),
        "green_ratio": masked_ratio(green_mask),
        "warm_spot_ratio": masked_ratio(warm_spot_mask),
        "lesion_ratio": masked_ratio(lesion_mask),
        "edge_damage": masked_ratio(lesion_mask & edge_band),
        "stripe_ratio": stripe_ratio,
        "mottled_ratio": mottled_ratio,
        "texture_value": texture_value,
    }

    signals = set()
    if features["brown_ratio"] > 0.045 or features["warm_spot_ratio"] > 0.035:
        signals.add("brown")
    if features["yellow_ratio"] > 0.07 or features["mottled_ratio"] > 0.11:
        signals.add("yellow")
    if features["white_ratio"] > 0.04 or features["gray_ratio"] > 0.09:
        signals.add("white")
    if features["dark_ratio"] > 0.06 or features["edge_damage"] > 0.06:
        signals.add("dark")
    if (
        features["lesion_ratio"] > 0.14
        or features["green_ratio"] < 0.42
        or features["texture_value"] > 45
        or features["mottled_ratio"] > 0.12
    ):
        signals.add("stress")
    if weather["humidity"] >= 75 or features["stripe_ratio"] > 0.24:
        signals.add("humid")
    if weather["temp"] >= 34:
        signals.add("heat")

    if not signals:
        signals.add("stress")

    dominant_ratio = max(
        features["brown_ratio"],
        features["yellow_ratio"],
        features["white_ratio"],
        features["dark_ratio"],
        features["gray_ratio"],
        0.12,
    )
    confidence = clamp(
        int(
            57
            + dominant_ratio * 180
            + features["lesion_ratio"] * 110
            + max(0.0, 0.58 - float(features["green_ratio"])) * 34
            + max(0.0, float(features["texture_value"]) - 28) * 0.28
        ),
        58,
        95,
    )

    return features, signals, confidence


def score_disease_entry_from_features(entry, features, weather, signals):
    normalized = {
        "brown": normalize_feature_score(features["brown_ratio"], 0.11),
        "yellow": normalize_feature_score(features["yellow_ratio"], 0.13),
        "white": normalize_feature_score(features["white_ratio"], 0.08),
        "dark": normalize_feature_score(features["dark_ratio"], 0.10),
        "gray": normalize_feature_score(features["gray_ratio"], 0.08),
        "warm": normalize_feature_score(features["warm_spot_ratio"], 0.09),
        "edge": normalize_feature_score(features["edge_damage"], 0.12),
        "stripe": normalize_feature_score(features["stripe_ratio"], 0.35),
        "mottle": normalize_feature_score(features["mottled_ratio"], 0.16),
        "green_loss": clamp(1.0 - float(features["green_ratio"]), 0.0, 1.2),
        "stress": clamp(float(features["texture_value"]) / 56.0, 0.0, 1.2),
        "humid": clamp(weather["humidity"] / 100.0, 0.0, 1.0),
        "heat": clamp((weather["temp"] - 27.0) / 10.0, 0.0, 1.0),
        "cool": clamp((29.0 - weather["temp"]) / 10.0, 0.0, 1.0),
    }

    score = len(set(entry["signals"]) & set(signals)) * 2.9
    profile = DISEASE_VISUAL_PROFILES.get(entry["name"], {})

    for feature_name, weight in profile.items():
        score += weight * normalized.get(feature_name, 0.0)

    score += normalize_feature_score(features["lesion_ratio"], 0.18) * 1.4

    if "humid" in entry["signals"] and weather["humidity"] >= 72:
        score += 0.9
    if "yellow" in entry["signals"] and normalized["yellow"] < 0.15:
        score -= 0.7
    if "white" in entry["signals"] and normalized["white"] < 0.14:
        score -= 0.9
    if "dark" in entry["signals"] and normalized["dark"] < 0.14:
        score -= 0.6

    return score


def select_visual_disease_entry(crop_name, features, weather, signals, seed=0):
    crop_key = normalize_crop_key(crop_name)
    candidates = []

    if crop_key == "generic":
        for library_key, entries in CROP_DISEASE_LIBRARY.items():
            if library_key == "generic":
                continue
            for entry in entries:
                candidates.append((library_key, entry))
        for entry in CROP_DISEASE_LIBRARY["generic"]:
            candidates.append(("generic", entry))
    else:
        library = CROP_DISEASE_LIBRARY.get(crop_key, CROP_DISEASE_LIBRARY["generic"])
        candidates = [(crop_key, entry) for entry in library]

    scored_entries = []
    total_candidates = max(len(candidates), 1)

    for index, (candidate_crop_key, entry) in enumerate(candidates):
        score = score_disease_entry_from_features(entry, features, weather, signals)
        tie_break = -abs((int(seed) % total_candidates) - index) * 0.01
        scored_entries.append((score, tie_break, candidate_crop_key, entry))

    scored_entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score, _, best_crop_key, best_entry = scored_entries[0]
    second_score = scored_entries[1][0] if len(scored_entries) > 1 else best_score - 0.6

    crop_display = (crop_name or "").strip()
    if not crop_display or crop_display.lower() == "generic":
        crop_display = "Crop" if best_crop_key == "generic" else best_crop_key.title()

    confidence = clamp(
        int(
            60
            + best_score * 7.5
            + max(0.0, best_score - second_score) * 6.0
            + normalize_feature_score(features["lesion_ratio"], 0.16) * 4.0
        ),
        60,
        97,
    )

    return best_entry, crop_display, confidence


def save_uploaded_leaf_image(image, original_name):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    safe_stem = Path(secure_filename(original_name or "leaf")).stem or "leaf"
    digest = sha1(image.tobytes()).hexdigest()[:12] # type: ignore
    file_name = f"disease_{str(safe_stem)[:24]}_{digest}.jpg"
    output_path = UPLOADS_DIR / file_name

    save_image = image.copy()
    save_image.thumbnail((960, 960))
    save_image.save(output_path, format="JPEG", quality=90)

    return f"/static/uploads/{file_name}"


def extract_leaf_signals(image, weather):
    _, signals, confidence = extract_leaf_features(image, weather)
    return signals, confidence


def resolve_model_label_to_entry(model_label, crop_name):
    if not model_label:
        return None, crop_name or "Crop"

    label_text = str(model_label).replace("___", " ").replace("__", " ").replace("_", " ").lower()
    search_crops = [normalize_crop_key(crop_name)]
    for key in CROP_DISEASE_LIBRARY:
        if key not in search_crops and key in label_text:
            search_crops.insert(0, key)

    for crop_key in search_crops:
        library = CROP_DISEASE_LIBRARY.get(crop_key, CROP_DISEASE_LIBRARY["generic"])
        for entry in library:
            if str(entry["name"]).lower() in label_text:
                crop_display = crop_name or str(crop_key).title()
                return entry, crop_display

    return None, crop_name or "Crop"


def predict_disease_with_features(image, crop_name, weather):
    features, signals, confidence = extract_leaf_features(image, weather)
    
    # Simple rule-based prediction
    if features["brown_ratio"] > 0.05:
        disease_name = "Leaf Rust"
    elif features["yellow_ratio"] > 0.05:
        disease_name = "Mosaic Virus"
    elif features["white_ratio"] > 0.05:
        disease_name = "Powdery Mildew"
    elif features["lesion_ratio"] > 0.1:
        disease_name = "Bacterial Blight"
    else:
        disease_name = "Healthy"
    
    # Find the entry in the library
    crop_key = normalize_crop_key(crop_name)
    library = CROP_DISEASE_LIBRARY.get(crop_key, CROP_DISEASE_LIBRARY["generic"])
    for entry in library:
        if disease_name.lower() in str(entry["name"]).lower():  # type: ignore
            crop_display = crop_name or str(crop_key).title()
            return build_disease_result(entry, crop_display, confidence, "Feature analysis")
    
    # Fallback
    return None


def build_default_disease_result(user, weather):
    seed_value = sum((i + 1) * ord(c) for i, c in enumerate(f"{user.crop_type or ''}-{user.location or ''}"))

    # Build varied signals from weather to get different diseases
    default_signals = set()
    if float(weather.get("humidity", 0)) >= 75:
        default_signals.add("humid")
    if weather["temp"] >= 34:
        default_signals.add("heat")
    if weather["temp"] < 26:
        default_signals.add("cool")

    # Use seed to rotate through different signal combos for variety
    signal_options = [
        {"brown", "warm"},
        {"yellow", "humid"},
        {"dark", "humid"},
        {"white"},
        {"brown", "stress"},
        {"yellow", "stress"},
    ]
    base_signals = signal_options[seed_value % len(signal_options)]
    default_signals.update(base_signals)

    crop_key = normalize_crop_key(user.crop_type)
    library = CROP_DISEASE_LIBRARY.get(crop_key, CROP_DISEASE_LIBRARY["generic"])

    # Pick different entry based on seed to avoid always getting same one
    entry_index = seed_value % len(library)
    entry = library[entry_index]

    # But still prefer signal-matched entry if available
    best_match = None
    best_score = -1
    for idx, candidate in enumerate(library):
        score = len(set(candidate["signals"]) & default_signals)
        tie = 1 if idx == entry_index else 0
        if score > best_score or (score == best_score and tie > 0):
            best_score = score
            best_match = candidate

    if best_match is not None:
        entry = best_match

    crop_display = (user.crop_type or crop_key.title() or "Crop").strip()  # type: ignore
    if crop_display.lower() == "generic":
        crop_display = "Crop"

    return build_disease_result(entry, crop_display, 82, "Field knowledge")


def analyze_uploaded_leaf(file_storage, user, weather):
    file_name = secure_filename(file_storage.filename or "")
    if not file_name:
        raise ValueError("Upload a crop image before analysis.")

    suffix = Path(file_name).suffix.lower()
    if suffix and suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("Please upload a PNG, JPG, JPEG, or WEBP image.")

    image_bytes = file_storage.read()
    if not image_bytes:
        raise ValueError("Uploaded image is empty.")

    try:
        image = Image.open(BytesIO(image_bytes))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValueError("The uploaded file could not be read as an image.")

    preview_url = save_uploaded_leaf_image(image, file_name)

    try:
        if not GEMINI_API_KEY:
            raise ValueError("Gemini API key is not configured.")

        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
        Analyze this leaf image of a {user.crop_type or 'crop'} plant for diseases.
        Respond ONLY with a valid JSON object containing exactly these keys:
        - "disease_name": string (name of the disease, or "Healthy" if none)
        - "confidence_score": integer (between 0 and 100)
        - "cause": string
        - "solution": string
        - "prevention_tips": list of strings
        """
        response = model.generate_content([prompt, image])
        response_text = response.text.strip()

        if response_text.startswith("```json"):
            response_text = response_text[7:-3].strip()
        elif response_text.startswith("```"):
            response_text = response_text[3:-3].strip()

        result_json = json.loads(response_text)

        entry = {
            "name": result_json.get("disease_name", "Unknown Disease"),
            "cause": result_json.get("cause", "Analysis pending"),
            "solution": result_json.get("solution", "Consult local expert"),
            "prevention_tips": result_json.get("prevention_tips", ["Maintain good field hygiene"]),
        }
        confidence = int(result_json.get("confidence_score", 85))
        diagnosis = build_disease_result(entry, user.crop_type or "Crop", confidence, "Gemini AI")
        return diagnosis, preview_url, file_name
    except Exception as e:
        print(f"Gemini API error: {e}")
        features, signals, base_confidence = extract_leaf_features(image, weather)
        seed_value = int(sha1(image_bytes[:512]).hexdigest(), 16) % 65537
        best_entry, crop_display, confidence = select_visual_disease_entry(
            user.crop_type, features, weather, signals, seed=seed_value
        )
        diagnosis = build_disease_result(best_entry, crop_display, confidence, "Vision analysis (Fallback)")
        return diagnosis, preview_url, file_name



def build_disease_page_context(
    user,
    weather,
    diagnosis=None,
    preview_url=None,
    upload_name=None,
    error_message=None,
):
    result = diagnosis or build_default_disease_result(user, weather)
    matched_entry = get_best_disease_library_entry(result.get("disease"), result.get("crop") or getattr(user, "crop_type", ""))
    image_url = preview_url or (matched_entry.get("image") if matched_entry else "") or build_disease_sample_data_uri(result["disease"])

    return {
        "location_label": user.location or weather["city"],
        "weather": weather,
        "upload_name": upload_name,
        "error_message": error_message,
        "has_upload": bool(preview_url),
        "result": {
            **result,
            "image_url": image_url,
        },
    }


def build_alerts(weather, soil, crop_health):
    alerts = []

    if float(weather.get("temp", 0)) >= 35:
        alerts.append(
            {
                "severity": "high",
                "title": "High temperature alert",
                "detail": f"Current field temperature is {weather['temp']} C. Watch for crop heat stress today.",
            }
        )

    if soil["moisture"] < 45:
        alerts.append(
            {
                "severity": "medium",
                "title": "Moisture is dropping",
                "detail": "Root-zone moisture is below the preferred level for stable crop development.",
            }
        )

    if crop_health["score"] < 58:
        alerts.append(
            {
                "severity": "medium",
                "title": "Crop health requires review",
                "detail": "Inspect the NDVI preview and field sections showing weaker vegetation response.",
            }
        )

    if not alerts:
        alerts.append(
            {
                "severity": "low",
                "title": "Conditions are stable",
                "detail": "No critical warnings right now. Continue monitoring weather and soil trends.",
            }
        )

    return list(alerts[:2])  # type: ignore


def build_map_embed_url(location, lat=None, lon=None):
    if not GOOGLE_MAPS_API_KEY:
        return None

    if lat is not None and lon is not None:
        return (
            "https://www.google.com/maps/embed/v1/view?"
            + urlencode(
                {
                    "key": GOOGLE_MAPS_API_KEY,
                    "center": f"{lat},{lon}",
                    "zoom": 13,
                    "maptype": "satellite",
                }
            )
        )

    return (
        "https://www.google.com/maps/embed/v1/place?"
        + urlencode(
            {
                "key": GOOGLE_MAPS_API_KEY,
                "q": location or "Bhubaneswar",
                "zoom": 12,
                "maptype": "satellite",
            }
        )
    )


def get_cdse_access_token():
    if not CDSE_CLIENT_ID or not CDSE_CLIENT_SECRET:
        return None

    if CDSE_TOKEN_CACHE["access_token"] and float(CDSE_TOKEN_CACHE.get("expires_at", 0) or 0) > time.time():
        return CDSE_TOKEN_CACHE["access_token"]

    token_data = fetch_json(
        "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
        method="POST",
        form_body={
            "grant_type": "client_credentials",
            "client_id": CDSE_CLIENT_ID,
            "client_secret": CDSE_CLIENT_SECRET,
        },
    )

    if not token_data or not token_data.get("access_token"):
        return None

    expires_in = int(token_data.get("expires_in", 3600))  # type: ignore
    CDSE_TOKEN_CACHE["access_token"] = token_data["access_token"] # type: ignore
    CDSE_TOKEN_CACHE["expires_at"] = time.time() + max(60, expires_in - 60) # type: ignore
    return CDSE_TOKEN_CACHE["access_token"]


def fetch_ndvi_preview(lat, lon):
    if lat is None or lon is None:
        return None

    token = get_cdse_access_token()
    if not token:
        return None

    now = datetime.now(timezone.utc)
    time_to = now.replace(hour=23, minute=59, second=59, microsecond=0)
    time_from = (now - timedelta(days=21)).replace(hour=0, minute=0, second=0, microsecond=0)

    lat_span = 0.025
    lon_span = 0.03

    payload = {
        "input": {
            "bounds": {
                "bbox": [
                    lon - lon_span,
                    lat - lat_span,
                    lon + lon_span,
                    lat + lat_span,
                ]
            },
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {
                            "from": time_from.isoformat().replace("+00:00", "Z"),
                            "to": time_to.isoformat().replace("+00:00", "Z"),
                        },
                        "maxCloudCoverage": 35,
                    },
                }
            ],
        },
        "output": {
            "width": 900,
            "height": 320,
            "responses": [{"identifier": "default", "format": {"type": "image/png"}}],
        },
        "evalscript": NDVI_EVALSCRIPT,
    }

    return fetch_bytes(
        "https://sh.dataspace.copernicus.eu/api/v1/process",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "image/png",
        },
        json_body=payload,
    )


def build_ndvi_fallback_svg(user):
    crop_name = escape(user.crop_type or "Farm")
    location_name = escape(user.location or "Location")

    svg = f"""
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 320" preserveAspectRatio="none">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#d4e6ff"/>
      <stop offset="60%" stop-color="#cde0b2"/>
      <stop offset="100%" stop-color="#4a7d2a"/>
    </linearGradient>
  </defs>
  <rect width="900" height="320" fill="url(#sky)"/>
  <path d="M0 232 C140 180 260 250 410 210 C570 166 680 236 900 186 L900 320 L0 320 Z" fill="#83b64a"/>
  <path d="M0 264 C160 198 326 272 510 228 C660 190 748 258 900 222 L900 320 L0 320 Z" fill="#4f8b27"/>
  <path d="M0 300 L180 214 L360 296 L520 206 L740 298 L900 228 L900 320 L0 320 Z" fill="#2f6323" opacity="0.65"/>
  <text x="34" y="58" fill="#18365f" font-family="Manrope, Arial, sans-serif" font-size="30" font-weight="800">Live NDVI Preview</text>
  <text x="34" y="94" fill="#24406a" font-family="Manrope, Arial, sans-serif" font-size="18" font-weight="700">{crop_name} - {location_name}</text>
  <text x="34" y="286" fill="#eef7ff" font-family="Manrope, Arial, sans-serif" font-size="16" font-weight="700">Sentinel imagery unavailable right now, showing local fallback preview.</text>
</svg>
""".strip()

    return svg.encode("utf-8")


def build_dashboard_context(user):
    primary_farm, farms = ensure_user_farm_setup(user)
    task_summary = build_task_summary(user)
    weather = fetch_weather_bundle(user.location or "Bhubaneswar")
    soil = build_soil_profile(user, weather)
    crop_health = build_crop_health(user, weather, soil)
    recommendations = build_recommendations(user, weather, soil, crop_health)
    alerts = build_alerts(weather, soil, crop_health)

    ndvi_params = {}
    if weather["lat"] is not None and weather["lon"] is not None:
        ndvi_params = {"lat": weather["lat"], "lon": weather["lon"]}  # type: ignore

    return {
        "weather": weather,
        "soil": soil,
        "crop_health": crop_health,
        "recommendations": recommendations,
        "alerts": alerts,
        "yield_prediction": crop_health["yield_prediction"],
        "lat": weather["lat"],
        "lon": weather["lon"],
        "map_embed_url": build_map_embed_url(user.location, weather["lat"], weather["lon"]),
        "ndvi_preview_url": "/dashboard/ndvi-preview"
        + (f"?{urlencode(ndvi_params)}" if ndvi_params else ""),
        "primary_farm_name": primary_farm.name if primary_farm else "Primary Farm",
        "farm_stats": {
            "count": len(farms),
            "secondary_count": max(len(farms) - 1, 0),
        },
        "task_summary": task_summary,
        "personalization": build_dashboard_personalization(user, primary_farm, recommendations, task_summary),
        "onboarding": build_dashboard_onboarding(user, farms, task_summary),
    }


def build_farms_page_context(user):
    primary_farm, farms = ensure_user_farm_setup(user)
    task_summary = build_task_summary(user, limit=12)
    weather = fetch_weather_bundle((primary_farm.location if primary_farm else user.location) or "Bhubaneswar")

    farm_cards = []
    for index, farm in enumerate(farms, start=1):
        farm_tasks = [item for item in task_summary["all"] if item["farm_id"] == farm.id]
        open_count = sum(1 for item in farm_tasks if item["status"] != "done")
        completed_count = sum(1 for item in farm_tasks if item["status"] == "done")
        upcoming_task = next((item for item in farm_tasks if item["status"] != "done"), None)

        farm_cards.append(
            {
                "id": farm.id,
                "name": farm.name or build_default_farm_name(user, index),
                "location": farm.location or "Location pending",
                "crop_type": farm.crop_type or "Crop pending",
                "farm_size": farm.farm_size or "Size pending",
                "notes": farm.notes or "No notes added yet.",
                "is_primary": bool(farm.is_primary),
                "open_tasks": open_count,
                "completed_tasks": completed_count,
                "upcoming_task": upcoming_task,
                "created_label": format_relative_time(farm.created_at),
            }
        )

    return {
        "weather": weather,
        "primary_farm": primary_farm,
        "farm_cards": farm_cards,
        "task_summary": task_summary,
        "recent_activity": build_recent_activity(user, limit=6),
    }
@app.route("/")
def index():
    user = get_current_user()
    if user:
        return redirect("/dashboard")
    return render_template("index.html")


@app.route("/signup", methods=["GET"])
def signup_alias_page():
    # Convenience alias for referral links like /signup?ref=CODE
    ref = (request.args.get("ref") or "").strip()
    suffix = f"?ref={quote(ref)}" if ref else ""
    return redirect(f"/register{suffix}")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        csrf_resp = require_csrf()
        if csrf_resp is not None:
<<<<<<< HEAD
            return render_template("login.html", error="Security check failed. Please refresh and try again.")

        email = (request.form.get("email") or "").strip().lower()
=======
            return csrf_resp

        if rate_limit_exceeded(f"login:{_client_ip()}", max_hits=10, window_seconds=5 * 60):
            return render_template("login.html", error="Too many login attempts. Please wait a few minutes and try again.")

        email = (request.form.get("email") or "").strip()
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
        password = request.form.get("password") or ""

        # Shortcut: allow admin credentials on the normal user login form.
        if email.strip().lower() == ADMIN_EMAIL and check_admin_password(password):
            session["admin_authed"] = True
            session["admin_email"] = ADMIN_EMAIL
<<<<<<< HEAD
            return redirect("/admin")

        user = User.query.filter(User.email.ilike(email)).first()
=======
            try:
                admin_row = AdminUser.query.filter_by(email=ADMIN_EMAIL).first()  # type: ignore
                if admin_row is not None:
                    session["admin_id"] = int(admin_row.id)
            except Exception:
                pass
            return redirect("/admin")

        user = User.query.filter_by(email=email).first()
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
        password_ok, upgraded = check_user_password(user, password)

        if user and password_ok:
            if upgraded:
                db.session.commit()
            clear_otp_session_state()
            session["user_id"] = user.id
            session["user"] = user.name
            return redirect("/dashboard")
        else:
            return render_template("login.html", error="Invalid email or password.")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        csrf_resp = require_csrf()
        if csrf_resp is not None:
<<<<<<< HEAD
            return render_template("register.html", error="Security check failed. Please refresh and try again.")
=======
            return csrf_resp

        if rate_limit_exceeded(f"register:{_client_ip()}", max_hits=8, window_seconds=10 * 60):
            return render_template("register.html", error="Too many attempts. Please wait and try again.")
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057

        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        location = (request.form.get("location") or "").strip()
        crop = (request.form.get("crop") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        referral_code = (request.form.get("referral_code") or "").strip()

        if not name:
            return render_template("register.html", error="Full name is required.")

        if not email or not phone:
            return render_template("register.html", error="Email and Phone Number are mandatory for registration.")

        if len(password) < 8:
            return render_template("register.html", error="Password must be at least 8 characters long.")

        existing_user = User.query.filter(User.email.ilike(email)).first()
        if existing_user:
            return render_template("register.html", error="An account already exists with this email address.")

        profile_photo = ""
        if "profile_photo" in request.files:
            file = request.files["profile_photo"]
            if file and file.filename:
                try:
                    profile_photo = save_profile_photo_upload(file, "profile_new")
                except ValueError as exc:
                    return render_template("register.html", error=str(exc))

        # Store in session for OTP verification
        password_hash = hash_password(password)
        session["pending_user"] = {
            "name": name,
            "email": email,
<<<<<<< HEAD
            "password_hash": hash_password(password),
=======
            "password_hash": password_hash,
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
            "location": location,
            "crop": crop,
            "phone": phone,
            "profile_photo": profile_photo,
            "referred_by": referral_code
        }
        
        otp = generate_otp()
<<<<<<< HEAD
        email_sent, failure_reason = send_otp_email(email, otp)
        update_otp_session_state(
            otp,
            email,
            "register",
            email_sent=email_sent,
            notice=build_otp_notice(email_sent, failure_reason),
        )
=======
        session["otp_sig"] = compute_otp_signature(otp, email, "register")
        session["otp_target"] = email
        session["otp_type"] = "register"
        session["otp_expiry"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()
        session["otp_attempts"] = 0
        
        # Send Real Email
        email_sent = send_otp_email(email, otp)
        if not email_sent:
            # Developer-friendly fallback: show OTP on-screen when SMTP isn't configured.
            # Never enable this in production.
            if (os.getenv("FLASK_ENV") or "").strip().lower() != "production":
                session["otp_dev_code"] = otp
                session["otp_notice"] = "Email service is not configured. Use the on-screen development code to verify."
        
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
        return redirect("/verify-otp")

    return render_template("register.html")


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
<<<<<<< HEAD
    if (
        "otp" not in session
        or session.get("otp_type") != "register"
        or "pending_user" not in session
    ):
        clear_otp_session_state()
=======
    if "otp_sig" not in session:
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
        return redirect("/login")
        
    error = None
    notice = session.pop("otp_notice", None)
    if request.method == "POST":
        csrf_resp = require_csrf()
        if csrf_resp is not None:
<<<<<<< HEAD
            return render_template("verify_otp.html", **get_otp_page_context(error="Security check failed. Please refresh and try again."))

        user_otp = re.sub(r"\D", "", request.form.get("otp", ""))
        
        # Check expiry
        if datetime.now(timezone.utc).timestamp() > session.get("otp_expiry", 0):
            error = "OTP has expired. Please try again."
        elif user_otp == session["otp"]:
            if "pending_user" in session:
=======
            # verify_otp has its own UI; show toast-like error.
            return render_template(
                "verify_otp.html",
                error="Security check failed. Please refresh and try again.",
                notice=notice,
                dev_otp=session.get("otp_dev_code"),
                target=session.get("otp_target"),
            )

        if rate_limit_exceeded(f"otp:{_client_ip()}:{session.get('otp_target')}", max_hits=10, window_seconds=5 * 60):
            return render_template(
                "verify_otp.html",
                error="Too many OTP attempts. Please request a new OTP and try again.",
                notice=notice,
                dev_otp=session.get("otp_dev_code"),
                target=session.get("otp_target"),
            )

        user_otp = str(request.form.get("otp") or "").strip()
        if not user_otp.isdigit() or len(user_otp) != 6:
            error = "Enter the 6-digit code."
            return render_template(
                "verify_otp.html",
                target=session.get("otp_target"),
                error=error,
                notice=notice,
                dev_otp=session.get("otp_dev_code"),
            )

        try:
            attempts = int(session.get("otp_attempts") or 0)
        except (TypeError, ValueError):
            attempts = 0

        if attempts >= 5:
            clear_otp_session_state()
            return render_template(
                "verify_otp.html",
                error="Too many failed attempts. Please request a new OTP.",
                notice=None,
                dev_otp=None,
                target=None,
            )

        if datetime.now(timezone.utc).timestamp() > session.get("otp_expiry", 0):
            clear_otp_session_state()
            return render_template(
                "verify_otp.html",
                error="OTP has expired. Please request a new OTP.",
                notice=None,
                dev_otp=None,
                target=None,
            )

        if verify_otp_signature(user_otp, session.get("otp_sig"), session.get("otp_target"), session.get("otp_type")):
            otp_type = session["otp_type"]
            
            if otp_type == "register" and "pending_user" in session:
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
                data = session["pending_user"]
                new_user = User( # type: ignore
                    name=data["name"],
                    email=data["email"],
<<<<<<< HEAD
                    password=data.get("password_hash") or hash_password(data.get("password", "")),
=======
                    password=data.get("password_hash") or hash_password(data.get("password") or ""),
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
                    location=data["location"],
                    crop_type=data["crop"],
                    phone=data["phone"],
                    profile_photo=data["profile_photo"],
                    referral_code=generate_unique_referral_code(),
                    referred_by=data.get("referred_by")
                )
                db.session.add(new_user)
                db.session.commit()
                
                # Reward Referrer
                if new_user.referred_by:
                    referrer = User.query.filter_by(referral_code=new_user.referred_by).first()
                    if referrer:
                        # Referral rewards:
<<<<<<< HEAD
                        # - Referrer wallet +₹20
                        # - New user wallet +₹10 (can be used for subscription discount)
=======
                        # - Referrer wallet +INR 20
                        # - New user wallet +INR 10 (can be used for subscription discount)
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
                        if ReferralReward.query.filter_by(new_user_id=new_user.id).first() is None:
                            wallet_credit(referrer, 20, "referral_bonus", {"new_user_id": new_user.id})
                            wallet_credit(new_user, 10, "referral_signup_bonus", {"referrer_id": referrer.id})
                            db.session.add(
                                ReferralReward(  # type: ignore
                                    referrer_id=referrer.id,
                                    new_user_id=new_user.id,
                                    referrer_reward_inr=20,
                                    new_user_bonus_inr=10,
                                )
                            )
                            db.session.commit()
                
                # Setup farm & preferences
                primary_farm = Farm( # type: ignore
                    user_id=new_user.id,
                    name=build_default_farm_name(new_user),
                    location=data["location"],
                    crop_type=data["crop"],
                    farm_size="",
                    is_primary=True
                )
                db.session.add(primary_farm)
                db.session.add(UserPreference(user_id=new_user.id, alert_email=data["email"], alert_phone=data["phone"])) # type: ignore
                db.session.commit()

                clear_otp_session_state()
                session["user_id"] = new_user.id
                session["user"] = new_user.name
                return redirect("/dashboard")
        else:
            session["otp_attempts"] = attempts + 1
            error = "Invalid OTP. Please check and try again."
            
<<<<<<< HEAD
    return render_template("verify_otp.html", **get_otp_page_context(error=error))
=======
    return render_template(
        "verify_otp.html",
        target=session.get("otp_target"),
        error=error,
        notice=notice,
        dev_otp=session.get("otp_dev_code"),
    )
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057


@app.route("/resend-otp", methods=["POST"])
def resend_otp():
<<<<<<< HEAD
    if (
        "otp_target" not in session
        or session.get("otp_type") != "register"
        or "pending_user" not in session
    ):
        clear_otp_session_state()
        return redirect("/login")

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return render_template("verify_otp.html", **get_otp_page_context(error="Security check failed. Please refresh and try again."))

    last_sent_at = float(session.get("otp_sent_at") or 0)
    seconds_since_last_send = max(0, int(time.time() - last_sent_at))
    if last_sent_at and seconds_since_last_send < OTP_RESEND_INTERVAL_SECONDS:
        wait_seconds = OTP_RESEND_INTERVAL_SECONDS - seconds_since_last_send
        return render_template(
            "verify_otp.html",
            **get_otp_page_context(error=f"Please wait {wait_seconds} seconds before requesting a new OTP."),
        )

    otp = generate_otp()
    target_email = session.get("otp_target")
    otp_type = session.get("otp_type")

    email_sent, failure_reason = send_otp_email(target_email, otp)
    update_otp_session_state(
        otp,
        target_email,
        otp_type,
        email_sent=email_sent,
        notice=build_otp_notice(email_sent, failure_reason),
    )
    return render_template("verify_otp.html", **get_otp_page_context())
=======
    csrf_resp = require_csrf()
    if csrf_resp is not None:
        session["otp_notice"] = "Security check failed. Please refresh and try again."
        return redirect("/verify-otp")

    otp_target = session.get("otp_target")
    otp_type = session.get("otp_type") or "register"
    if not otp_target or "otp_sig" not in session:
        return redirect("/login")

    if rate_limit_exceeded(f"otp_resend:{_client_ip()}:{otp_target}", max_hits=3, window_seconds=10 * 60):
        session["otp_notice"] = "Too many resend requests. Please wait and try again."
        return redirect("/verify-otp")

    otp = generate_otp()
    session["otp_sig"] = compute_otp_signature(otp, otp_target, otp_type)
    session["otp_expiry"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()
    session["otp_attempts"] = 0
    session.pop("otp_dev_code", None)

    email_sent = send_otp_email(otp_target, otp)
    if email_sent:
        session["otp_notice"] = "A new verification code has been sent."
    else:
        if (os.getenv("FLASK_ENV") or "").strip().lower() != "production":
            session["otp_dev_code"] = otp
            session["otp_notice"] = "Email service is not configured. Use the on-screen development code to verify."
        else:
            session["otp_notice"] = "Email delivery failed. Please try again later."

    return redirect("/verify-otp")
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057


@app.route("/api/mandi-rates")
def get_mandi_rates():
    user = get_current_user()
    location = user.location if user else "India"
    rates = build_mock_mandi_rates(location)
    return jsonify({"success": True, "location": location, "rates": rates})


@app.route("/dashboard")
def dashboard():
    user = get_current_user()
    if not user:
        return redirect("/login")

    dashboard_data = build_dashboard_context(user)
    carbon_impact = calculate_carbon_credits(user)
    return render_template("dashboard.html", user=user, dashboard=dashboard_data, carbon=carbon_impact)


@app.route("/farms")
def farms():
    user = get_current_user()
    if not user:
        return redirect("/login")

    farms_page = build_farms_page_context(user)
    notice = session.pop("farms_notice", None)
    return render_template("farms.html", user=user, farms_page=farms_page, notice=notice)


@app.route("/farms/add", methods=["POST"])
def add_farm():
    user = get_current_user()
    if not user:
        return redirect("/login")

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp

    _, farms = ensure_user_farm_setup(user)
    name = (request.form.get("name") or "").strip() or build_default_farm_name(user, len(farms) + 1)
    location = (request.form.get("location") or "").strip() or user.location or ""
    crop_type = (request.form.get("crop_type") or "").strip() or user.crop_type or ""
    farm_size = (request.form.get("farm_size") or "").strip() or user.farm_size or ""
    notes = (request.form.get("notes") or "").strip()
    make_primary = request.form.get("is_primary") == "on"

    if make_primary:
        for farm in farms:
            farm.is_primary = False

    new_farm = Farm(  # type: ignore
        user_id=user.id,
        name=name,
        location=location,
        crop_type=crop_type,
        farm_size=farm_size,
        notes=notes,
        is_primary=make_primary or not farms,
    )
    db.session.add(new_farm)

    if new_farm.is_primary:
        user.location = location or user.location
        user.crop_type = crop_type or user.crop_type
        user.farm_size = farm_size or user.farm_size

    db.session.commit()
    remember_notice("farms_notice", f"{name} added to your farms.")
    return redirect("/farms")


@app.route("/farms/set-primary/<int:farm_id>", methods=["POST"])
def set_primary_farm(farm_id):
    user = get_current_user()
    if not user:
        return redirect("/login")

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp

    farm = Farm.query.filter_by(id=farm_id, user_id=user.id).first()
    if farm is None:
        remember_notice("farms_notice", "Farm not found.", tone="warning")
        return redirect("/farms")

    for item in Farm.query.filter_by(user_id=user.id).all():
        item.is_primary = item.id == farm.id

    user.location = farm.location or user.location
    user.crop_type = farm.crop_type or user.crop_type
    user.farm_size = farm.farm_size or user.farm_size
    db.session.commit()

    remember_notice("farms_notice", f"{farm.name} is now your primary farm.")
    return redirect("/farms")


@app.route("/farms/delete/<int:farm_id>", methods=["POST"])
def delete_farm(farm_id):
    user = get_current_user()
    if not user:
        return redirect("/login")

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp

    farms = Farm.query.filter_by(user_id=user.id).order_by(Farm.created_at.asc()).all()
    farm = next((item for item in farms if item.id == farm_id), None)
    if farm is None:
        remember_notice("farms_notice", "Farm not found.", tone="warning")
        return redirect("/farms")

    if len(farms) <= 1:
        remember_notice("farms_notice", "At least one farm record must remain available.", tone="warning")
        return redirect("/farms")

    fallback_primary = next((item for item in farms if item.id != farm.id), None)
    deleted_name = farm.name
    was_primary = bool(farm.is_primary)
    db.session.delete(farm)

    if was_primary and fallback_primary is not None:
        fallback_primary.is_primary = True
        user.location = fallback_primary.location or user.location
        user.crop_type = fallback_primary.crop_type or user.crop_type
        user.farm_size = fallback_primary.farm_size or user.farm_size

    db.session.commit()
    remember_notice("farms_notice", f"{deleted_name} removed from your farm list.")
    return redirect("/farms")


@app.route("/farms/tasks/add", methods=["POST"])
def add_farm_task():
    user = get_current_user()
    if not user:
        return redirect("/login")

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp

    title = (request.form.get("title") or "").strip()
    if not title:
        remember_notice("farms_notice", "Task title is required.", tone="warning")
        return redirect("/farms")

    farm_id_text = (request.form.get("farm_id") or "").strip()
    farm = None
    if farm_id_text.isdigit():
        farm = Farm.query.filter_by(id=int(farm_id_text), user_id=user.id).first()

    task = FarmTask(  # type: ignore
        user_id=user.id,
        farm_id=farm.id if farm else None,
        title=title,
        details=(request.form.get("details") or "").strip(),
        category=(request.form.get("category") or "General").strip() or "General",
        priority=(request.form.get("priority") or "medium").strip() or "medium",
        status="todo",
        due_date=parse_due_date_input(request.form.get("due_date")),
    )
    db.session.add(task)
    db.session.commit()

    remember_notice("farms_notice", f"Task '{title}' added to your planner.")
    return redirect("/farms#task-planner")


@app.route("/tasks/<int:task_id>/status", methods=["POST"])
def update_task_status(task_id):
    user = get_current_user()
    if not user:
        return redirect("/login")

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp

    task = FarmTask.query.filter_by(id=task_id, user_id=user.id).first()
    if task is None:
        remember_notice("farms_notice", "Task not found.", tone="warning")
        return redirect("/farms")

    new_status = (request.form.get("status") or "todo").strip()
    if new_status not in {"todo", "in_progress", "done"}:
        new_status = "todo"

    task.status = new_status
    task.completed_at = datetime.now(timezone.utc) if new_status == "done" else None
    db.session.commit()

    remember_notice("farms_notice", f"Task '{task.title}' moved to {new_status.replace('_', ' ')}.")
    return redirect("/farms#task-planner")


@app.route("/tasks/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id):
    user = get_current_user()
    if not user:
        return redirect("/login")

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp

    task = FarmTask.query.filter_by(id=task_id, user_id=user.id).first()
    if task is None:
        remember_notice("farms_notice", "Task not found.", tone="warning")
        return redirect("/farms")

    deleted_title = task.title
    db.session.delete(task)
    db.session.commit()
    remember_notice("farms_notice", f"Task '{deleted_title}' deleted.")
    return redirect("/farms#task-planner")


@app.route("/weather")
def weather_monitoring():
    user = get_current_user()
    if not user:
        return redirect("/login")

    weather_page = build_weather_page_context(user)
    return render_template("weather.html", user=user, weather_page=weather_page)


@app.route("/soil-health")
def soil_health_monitoring():
    user = get_current_user()
    if not user:
        return redirect("/login")

    soil_page = build_soil_page_context(user)
    return render_template("soil.html", user=user, soil_page=soil_page)


@app.route("/crop-monitoring")
def crop_monitoring():
    user = get_current_user()
    if not user:
        return redirect("/login")

    crop_page = build_crop_monitoring_context(user)
    return render_template("crop_monitoring.html", user=user, crop_page=crop_page)


@app.route("/crop-library")
def crop_library():
    user = get_current_user()
    if not user:
        return redirect("/login")

    crop_library_page = build_crop_library_context()
    return render_template("crop_library.html", user=user, crop_library=crop_library_page)


<<<<<<< HEAD
@app.route("/library")
def library_home():
    user = get_current_user()
    if not user:
        return redirect("/login")

    return render_template("library_home.html", user=user, **build_library_home_context())


@app.route("/library/crops")
def library_crops():
    return redirect("/crop-library")


@app.route("/library/diseases")
def library_diseases():
    user = get_current_user()
    if not user:
        return redirect("/login")

    active_crop = (request.args.get("crop") or "All").strip() or "All"
    query = (request.args.get("q") or "").strip()
    active_type = (request.args.get("type") or "all").strip().lower() or "all"
    all_items = get_library_disease_items()
    crops = get_library_crop_options()
    if active_crop not in crops:
        active_crop = "All"

    items = all_items
    if active_crop != "All":
        items = [item for item in items if active_crop in item["crops"]]
    if active_type in {"insect", "fungus", "virus", "bacteria"}:
        items = [item for item in items if item["type_key"] == active_type]
    if query:
        query_lower = query.lower()
        items = [item for item in items if query_lower in item["search_text"]]

    no_results = not items and bool(query or active_crop != "All" or active_type != "all")
    suggested_items = all_items[:6]
    if no_results:
        items = suggested_items

    return render_template(
        "library_diseases.html",
        user=user,
        crops=crops,
        active_crop=active_crop,
        active_type=active_type,
        query=query,
        no_results=no_results,
        items=items,
        suggested_items=suggested_items,
        stage_sections=build_library_stage_sections(items),
        fallback_image=build_disease_sample_data_uri("leaf disease"),
    )


@app.route("/library/disease/<disease_slug>")
def library_disease_detail(disease_slug):
    user = get_current_user()
    if not user:
        return redirect("/login")

    disease = get_library_disease_item(disease_slug)
    if disease is None:
        abort(404)

    payload = build_library_disease_detail_payload(user, disease)
    return render_template(
        "library_disease_detail.html",
        user=user,
        fallback_image=build_disease_sample_data_uri(disease["name"]),
        **payload,
    )


@app.route("/library/tips")
def library_tips():
    user = get_current_user()
    if not user:
        return redirect("/login")

    crops = get_library_crop_options()
    active_crop = (request.args.get("crop") or "All").strip() or "All"
    if active_crop not in crops:
        active_crop = "All"

    return render_template(
        "library_tips.html",
        user=user,
        crops=crops,
        active_crop=active_crop,
        tips=build_library_tips_data(active_crop),
    )


@app.route("/library/alerts")
def library_alerts():
    user = get_current_user()
    if not user:
        return redirect("/login")

    crops = get_library_crop_options()
    active_crop = (request.args.get("crop") or "All").strip() or "All"
    if active_crop not in crops:
        active_crop = "All"

    return render_template(
        "library_alerts.html",
        user=user,
        crops=crops,
        active_crop=active_crop,
        alerts=build_library_alert_items(active_crop),
        fallback_image=build_disease_sample_data_uri("field alert"),
    )


=======
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
@app.route("/crop/<crop_slug>")
def crop_detail(crop_slug):
    user = get_current_user()
    if not user:
        return redirect("/login")

    crop = get_crop_library_entry(crop_slug)
    if crop is None:
        abort(404)

    related_crops = pick_related_crops(crop["slug"], crop["category"], crop["life_cycle"], crop["soil_type"])
    return render_template("crop_detail.html", user=user, crop=crop, related_crops=related_crops)


<<<<<<< HEAD
=======
@app.route("/library")
def library_home():
    user = get_current_user()
    if not user:
        return redirect("/login")

    crops = load_crop_library()
    diseases = load_disease_library()
    tips_payload = load_cultivation_tips()
    return render_template(
        "library_home.html",
        user=user,
        crop_count=len(crops),
        disease_count=len(diseases),
        tip_task_count=len((tips_payload or {}).get("tasks") or []),
        tip_stage_count=len((tips_payload or {}).get("stages") or []),
    )


@app.route("/library/crops")
def library_crops():
    return redirect("/crop-library")


@app.route("/library/diseases")
def library_diseases():
    user = get_current_user()
    if not user:
        return redirect("/login")

    crop_filter = (request.args.get("crop") or "All").strip()
    query = (request.args.get("q") or "").strip().lower()
    kind = (request.args.get("type") or "all").strip().lower()

    all_items = load_disease_library()
    items = list(all_items)
    if crop_filter and crop_filter != "All":
        items = [item for item in items if crop_filter in (item.get("crops") or [])]
    if kind in {"insect", "virus", "fungus", "bacteria", "disease", "pest"}:
        items = [item for item in items if str(item.get("type") or "").strip().lower() == kind]
    if query:
        def _matches(item):
            hay = " ".join(
                [
                    str(item.get("name") or ""),
                    str(item.get("type") or ""),
                    " ".join(item.get("crops") or []),
                    " ".join(item.get("tags") or []),
                ]
            ).lower()
            return query in hay
        items = [item for item in items if _matches(item)]

    no_results = False
    suggested_items = []
    if not items and (query or kind != "all" or (crop_filter and crop_filter != "All")):
        no_results = True
        # Provide a helpful fallback so the page never looks empty.
        suggested_items = all_items[:12]

    # Group by stage for the Plantix-like sections.
    stage_order = ["seedling", "vegetative", "flowering", "harvesting", "post_harvest"]
    stage_labels = {
        "seedling": "Seedling Stage",
        "vegetative": "Vegetative Stage",
        "flowering": "Flowering Stage",
        "harvesting": "Harvesting Stage",
        "post_harvest": "Post Harvest",
    }
    stage_sections = []
    for stage_key in stage_order:
        stage_items = [item for item in items if stage_key in (item.get("stages") or [])]
        if stage_items:
            stage_sections.append({"key": stage_key, "label": stage_labels.get(stage_key, stage_key.title()), "items": stage_items[:10]})

    return render_template(
        "library_diseases.html",
        user=user,
        crops=build_library_crop_options(user),
        active_crop=crop_filter if crop_filter else "All",
        query=query,
        active_type=kind,
        stage_sections=stage_sections,
        items=items,
        no_results=no_results,
        suggested_items=suggested_items,
        fallback_image=DISEASE_LIBRARY_DEFAULT_IMAGES["disease"],
    )


@app.route("/library/disease/<disease_slug>")
def library_disease_detail(disease_slug):
    user = get_current_user()
    if not user:
        return redirect("/login")

    entry = get_disease_library_entry(disease_slug)
    if entry is None:
        abort(404)

    recommended = resolve_store_recommendation(
        disease_name=entry["name"],
        cause=entry.get("cause"),
        chemical_solution=entry.get("solution"),
        crop_name=(entry.get("crops") or [""])[0],
    )
    recommended_payload = serialize_store_product(recommended) if recommended is not None else None

    last_diag = session.get("last_library_diagnosis") if isinstance(session.get("last_library_diagnosis"), dict) else {}
    if last_diag.get("slug") != entry["slug"]:
        last_diag = {}

    return render_template(
        "library_disease_detail.html",
        user=user,
        disease=entry,
        recommended_product=recommended_payload,
        fallback_image=DISEASE_LIBRARY_DEFAULT_IMAGES["disease"],
        last_diagnosis=last_diag,
    )


@app.route("/library/tips")
def library_tips():
    user = get_current_user()
    if not user:
        return redirect("/login")

    crop_filter = (request.args.get("crop") or "All").strip()
    payload = load_cultivation_tips()
    return render_template(
        "library_tips.html",
        user=user,
        crops=build_library_crop_options(user),
        active_crop=crop_filter if crop_filter else "All",
        tips=payload,
    )


@app.route("/library/alerts")
def library_alerts():
    user = get_current_user()
    if not user:
        return redirect("/login")

    crop_filter = (request.args.get("crop") or "All").strip()
    items = load_disease_library()
    if crop_filter and crop_filter != "All":
        items = [item for item in items if crop_filter in (item.get("crops") or [])]

    # Mock risk: stable by user id + crop to keep it consistent per user.
    rng = random.Random(str(user.id) + "|" + crop_filter)
    picks = items[:]
    rng.shuffle(picks)
    picks = picks[:6]

    alerts = []
    for idx, item in enumerate(picks):
        alerts.append(
            {
                "severity": "high" if idx < 2 else "medium" if idx < 4 else "low",
                "crop": (item.get("crops") or ["Crop"])[0] if item.get("crops") else "Crop",
                "name": item.get("name") or "",
                "type": item.get("type") or "",
                "slug": item.get("slug") or "",
                "image": item.get("image") or "",
            }
        )

    return render_template(
        "library_alerts.html",
        user=user,
        crops=build_library_crop_options(user),
        active_crop=crop_filter if crop_filter else "All",
        alerts=alerts,
        fallback_image=DISEASE_LIBRARY_DEFAULT_IMAGES["disease"],
    )


>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
@app.route("/farm-twin")
@require_plan("premium")
def farm_twin():
    user = get_current_user()
    if not user:
        return redirect("/login")

    farm_twin_page = build_farm_twin_context(user)
    return render_template("farm_twin.html", user=user, farm_twin=farm_twin_page)


@app.route("/disease-detection", methods=["GET"])
@check_subscription
def disease_detection():
    user = get_current_user()
    if not user:
        return redirect("/login")

    weather = fetch_weather_bundle(user.location or "Bhubaneswar")
    history_records = DiseaseHistory.query.filter_by(user_id=user.id).order_by(DiseaseHistory.date.desc()).limit(10).all()
    
    disease_page = build_disease_page_context(
        user,
        weather,
        diagnosis=None,
        preview_url=None,
        upload_name=None,
        error_message=None,
    )
    return render_template("disease_detection.html", user=user, disease_page=disease_page, history=history_records)


@app.route("/market", methods=["GET"])
def market():
    user = get_current_user()
    if not user:
        return redirect("/login")

    category_lookup = {name.lower(): name for name in STORE_CATEGORY_ORDER}
    active_category = category_lookup.get((request.args.get("category") or "All").strip().lower(), "All")
    search_query = (request.args.get("q") or "").strip()
    sort_option = (request.args.get("sort") or "featured").strip().lower()
    recommended_slug = slugify_crop_name(request.args.get("recommended") or "") if request.args.get("recommended") else None

    store_page = build_store_page_context(
        search_query=search_query,
        active_category=active_category,
        sort_option=sort_option,
        recommended_slug=recommended_slug,
    )
    return render_template("market.html", user=user, store_page=store_page)


@app.route("/market/product/<product_slug>", methods=["GET"])
def market_product_detail(product_slug):
    user = get_current_user()
    if not user:
        return redirect("/login")

    product = get_store_product_by_slug(product_slug)
    if product is None:
        abort(404)

    product_data = serialize_store_product(product)
    related_products = get_related_store_products(product)
    return render_template(
        "market_product_detail.html",
        user=user,
        product=product_data,
        related_products=related_products,
    )


@app.route("/api/store/checkout", methods=["POST"])
def store_checkout():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

<<<<<<< HEAD
=======
    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp

>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    payload = request.get_json(silent=True) or {}
    product = get_store_product_by_id(payload.get("product_id"))
    if product is None:
        return jsonify({"success": False, "error": "Product not found"}), 404

    source = str(payload.get("source") or "store").strip() or "store"
    checkout_order, order_error = create_razorpay_order(product, user, source)
    checkout_mode = "razorpay" if checkout_order else "demo"

    order_record = StoreOrder(
        user_id=user.id,
        product_id=product.id,
        amount=int(product.price) * 100,
        currency=RAZORPAY_CURRENCY,
        status="created",
        checkout_mode=checkout_mode,
        source=source,
        razorpay_order_id=(checkout_order or {}).get("id"),
        notes_json=json.dumps(
            {
                "source": source,
                "product_name": product.name,
                "order_error": order_error,
                "fulfillment_status": "pending",
            },
            ensure_ascii=False,
        ),
    )
    db.session.add(order_record)
    db.session.commit()

    product_data = serialize_store_product(product)
    checkout_payload = {
        "key": RAZORPAY_KEY_ID,
        "amount": int(product.price) * 100,
        "currency": RAZORPAY_CURRENCY,
        "name": RAZORPAY_CHECKOUT_NAME,
        "description": f"{product.name} | {product.category}",
        "image": "/static/brand/agrovision-email-logo.png",
        "order_id": (checkout_order or {}).get("id"),
        "prefill": {
            "name": user.name or "AgroVision User",
            "email": user.email or "",
            "contact": user.phone or "",
        },
        "notes": {
            "product_id": str(product.id),
            "product_name": product.name,
            "source": source,
        },
        "theme": {"color": "#1fa36d"},
    }

    return jsonify(
        {
            "success": True,
            "checkout_mode": checkout_mode,
            "message": (
                "Razorpay order created successfully."
                if checkout_order
                else "Server could not create a Razorpay order, so demo checkout mode will be used."
            ),
            "product": product_data,
            "checkout": checkout_payload,
            "order_record_id": order_record.id,
            "order_error": order_error,
        }
    )


@app.route("/api/store/payment-success", methods=["POST"])
def store_payment_success():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

<<<<<<< HEAD
=======
    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp

>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    payload = request.get_json(silent=True) or {}
    product = get_store_product_by_id(payload.get("product_id"))
    if product is None:
        return jsonify({"success": False, "error": "Product not found"}), 404

    order_record = None
    try:
        order_record_id = int(payload.get("order_record_id") or 0)
    except (TypeError, ValueError):
        order_record_id = 0

    if order_record_id:
        order_record = StoreOrder.query.filter_by(id=order_record_id, user_id=user.id).first()

    if order_record is None:
        order_record = StoreOrder(
            user_id=user.id,
            product_id=product.id,
            amount=int(product.price) * 100,
            currency=RAZORPAY_CURRENCY,
            status="created",
            checkout_mode=str(payload.get("checkout_mode") or "demo"),
            source=str(payload.get("source") or "store"),
        )
        db.session.add(order_record)

    notes = get_order_notes(order_record)
    notes.setdefault("fulfillment_status", "pending")

    razorpay_order_id = str(payload.get("razorpay_order_id") or order_record.razorpay_order_id or "")
    razorpay_payment_id = str(payload.get("razorpay_payment_id") or f"demo_pay_{uuid.uuid4().hex[:10]}")
    razorpay_signature = str(payload.get("razorpay_signature") or "")
    signature_verified = verify_razorpay_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature)

<<<<<<< HEAD
=======
    is_demo = str(payload.get("checkout_mode") or order_record.checkout_mode or "demo").strip().lower() == "demo"
    if not is_demo and not signature_verified:
        order_record.status = "failed"
        notes.update({"verified": False, "failure": "signature_failed"})
        set_order_notes(order_record, notes)
        db.session.commit()
        return jsonify({"success": False, "error": "Payment verification failed."}), 400

>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    order_record.status = "paid"
    order_record.checkout_mode = str(payload.get("checkout_mode") or order_record.checkout_mode or "demo")
    order_record.source = str(payload.get("source") or order_record.source or "store")
    order_record.razorpay_order_id = razorpay_order_id or f"demo_order_{uuid.uuid4().hex[:10]}"
    order_record.razorpay_payment_id = razorpay_payment_id
    order_record.razorpay_signature = razorpay_signature
    notes.update(
        {
            "verified": signature_verified,
            "source": order_record.source,
            "product_name": product.name,
        }
    )
    set_order_notes(order_record, notes)

    user.loyalty_points = int(user.loyalty_points or 0) + max(5, int(product.price / 40))
    db.session.commit()

    send_admin_order_email(order_record, user, product)

    return jsonify(
        {
            "success": True,
            "verified": signature_verified,
            "message": (
                f"Payment received for {product.name}. "
                + ("Signature verified." if signature_verified else "Demo payment saved successfully.")
            ),
            "points_earned": max(5, int(product.price / 40)),
        }
    )


<<<<<<< HEAD
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if is_admin_authenticated():
        return redirect("/admin")

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = (request.form.get("password") or "").strip()
        if email == ADMIN_EMAIL and check_admin_password(password):
            session["admin_authed"] = True
            session["admin_email"] = ADMIN_EMAIL
            return redirect("/admin")

        return render_template("admin/login.html", error="Invalid admin credentials.", admin_email=ADMIN_EMAIL)

    return render_template("admin/login.html", error=None, admin_email=ADMIN_EMAIL)


@app.route("/admin/logout", methods=["GET"])
def admin_logout():
    session.pop("admin_authed", None)
    session.pop("admin_email", None)
    return redirect("/admin/login")


@app.route("/admin", methods=["GET"])
@admin_required
def admin_dashboard():
    products = StoreProduct.query.all()
    paid_orders = StoreOrder.query.filter_by(status="paid").order_by(StoreOrder.created_at.desc()).all()

    total_products = len(products)
    total_orders = len(paid_orders)
    pending_orders = sum(1 for order in paid_orders if get_fulfillment_status(order) == "pending")
    revenue = sum(int(order.amount or 0) for order in paid_orders) / 100.0
    audit = build_admin_audit_context()

    return render_template(
        "admin/dashboard.html",
        total_products=total_products,
        total_orders=total_orders,
        pending_orders=pending_orders,
        revenue=revenue,
        audit=audit,
    )


@app.route("/admin/products", methods=["GET", "POST"])
@admin_required
def admin_products():
    error = None
    success = None

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        category = (request.form.get("category") or "Organic").strip() or "Organic"
        image_url = (request.form.get("image_url") or "").strip()
        description = (request.form.get("description") or "").strip()
        is_active = request.form.get("is_active") == "on"
        image_file = request.files.get("image_file")

        try:
            price = int(request.form.get("price") or 0)
        except (TypeError, ValueError):
            price = 0

        try:
            stock = int(request.form.get("stock") or 0)
        except (TypeError, ValueError):
            stock = 0

        if not name or price <= 0:
            error = "Product name and price are required."
        else:
            slug_base = slugify_crop_name(name)
            slug = slug_base
            if StoreProduct.query.filter_by(slug=slug).first() is not None:
                slug = f"{slug_base}-{uuid.uuid4().hex[:6]}"

            if image_file and getattr(image_file, "filename", ""):
                try:
                    image_url = save_product_image_upload(image_file, slug_hint=slug_base)
                except ValueError as exc:
                    error = str(exc)

        if not error:
            product = StoreProduct(
                slug=slug,
                name=name,
                category=category if category in STORE_CATEGORY_ORDER else "Organic",
                price=price,
                mrp=max(int(estimate_store_mrp(price, category)), price),
                discount_pct=compute_store_discount(price, max(int(estimate_store_mrp(price, category)), price)),
                rating=4.2,
                image_url=image_url,
                description=description,
                seller=default_store_seller(category),
                unit="Pack",
                stock=max(0, stock),
                is_active=bool(is_active),
            )
            db.session.add(product)
            db.session.commit()
            success = "Product added."

    products = StoreProduct.query.order_by(StoreProduct.updated_at.desc(), StoreProduct.created_at.desc()).all()
    return render_template(
        "admin/products.html",
        products=products,
        categories=[c for c in STORE_CATEGORY_ORDER if c != "All"],
        error=error,
        success=success,
    )


@app.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_product(product_id):
    product = db.session.get(StoreProduct, product_id)
    if product is None:
        abort(404)

    error = None
    success = None

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        category = (request.form.get("category") or product.category or "Organic").strip() or "Organic"
        image_url = (request.form.get("image_url") or "").strip()
        description = (request.form.get("description") or "").strip()
        is_active = request.form.get("is_active") == "on"
        image_file = request.files.get("image_file")

        try:
            price = int(request.form.get("price") or 0)
        except (TypeError, ValueError):
            price = 0

        try:
            stock = int(request.form.get("stock") or 0)
        except (TypeError, ValueError):
            stock = int(product.stock or 0)

        if not name or price <= 0:
            error = "Product name and price are required."
        else:
            if image_file and getattr(image_file, "filename", ""):
                try:
                    image_url = save_product_image_upload(image_file, slug_hint=product.slug or name)
                except ValueError as exc:
                    error = str(exc)

            product.name = name
            product.category = category if category in STORE_CATEGORY_ORDER else "Organic"
            product.price = price
            product.mrp = max(int(estimate_store_mrp(price, category)), price)
            product.discount_pct = compute_store_discount(product.price, product.mrp)
            # Only overwrite image_url if admin provided a new URL or uploaded a file.
            if image_url:
                product.image_url = image_url
            product.description = description
            product.stock = max(0, stock)
            product.is_active = bool(is_active)
            product.slug = product.slug or slugify_crop_name(name)

            if not error:
                db.session.commit()
                success = "Product updated."

    return render_template(
        "admin/product_edit.html",
        product=product,
        categories=[c for c in STORE_CATEGORY_ORDER if c != "All"],
        error=error,
        success=success,
    )


@app.route("/admin/products/<int:product_id>/delete", methods=["POST"])
@admin_required
def admin_delete_product(product_id):
    product = db.session.get(StoreProduct, product_id)
    if product is None:
        abort(404)
    product.is_active = False
    db.session.commit()
    return redirect("/admin/products")


@app.route("/admin/orders", methods=["GET"])
@admin_required
def admin_orders():
    status_filter = (request.args.get("status") or "").strip().lower()

    orders = StoreOrder.query.order_by(StoreOrder.created_at.desc()).limit(300).all()
    if status_filter in FULFILLMENT_STATUS_ORDER:
        orders = [order for order in orders if get_fulfillment_status(order) == status_filter]

    order_rows = []
    for order in orders:
        product = getattr(order, "product", None)
        buyer = getattr(order, "buyer", None)
        order_rows.append(
            {
                "id": order.id,
                "product_name": getattr(product, "name", "") or "",
                "user_name": getattr(buyer, "name", "") or "",
                "user_email": getattr(buyer, "email", "") or "",
                "amount_inr": (int(order.amount or 0) / 100.0),
                "payment_status": str(order.status or ""),
                "fulfillment_status": get_fulfillment_status(order),
                "created_at": order.created_at,
            }
        )

    return render_template(
        "admin/orders.html",
        orders=order_rows,
        status_filter=status_filter,
        statuses=FULFILLMENT_STATUS_ORDER,
    )


@app.route("/admin/orders/<int:order_id>/fulfillment", methods=["POST"])
@admin_required
def admin_update_order_fulfillment(order_id):
    order = db.session.get(StoreOrder, order_id)
    if order is None:
        abort(404)

    new_status = (request.form.get("fulfillment_status") or "").strip().lower()
    try:
        set_fulfillment_status(order, new_status)
    except ValueError:
        return redirect("/admin/orders")

    db.session.commit()
    return redirect("/admin/orders")


@app.route("/admin/mappings", methods=["GET", "POST"])
@admin_required
def admin_mappings():
    error = None
    success = None

    if request.method == "POST":
        disease_label = (request.form.get("disease") or "").strip()
        disease_key = normalize_disease_key(disease_label)
        try:
            product_id = int(request.form.get("product_id") or 0)
        except (TypeError, ValueError):
            product_id = 0

        product = db.session.get(StoreProduct, product_id) if product_id else None
        if not disease_key or product is None:
            error = "Disease name and a valid product are required."
        else:
            existing = DiseaseProductMapping.query.filter_by(disease_key=disease_key).first()
            if existing is None:
                existing = DiseaseProductMapping(disease_key=disease_key, disease_label=disease_label, product_id=product.id)
                db.session.add(existing)
            else:
                existing.disease_label = disease_label
                existing.product_id = product.id
            db.session.commit()
            success = "Mapping saved."

    mappings = DiseaseProductMapping.query.order_by(DiseaseProductMapping.updated_at.desc()).all()
    products = StoreProduct.query.filter_by(is_active=True).order_by(StoreProduct.name.asc()).all()

    mapping_rows = []
    for mapping in mappings:
        mapping_rows.append(
            {
                "id": mapping.id,
                "disease": mapping.disease_label,
                "disease_key": mapping.disease_key,
                "product_id": mapping.product_id,
                "product_name": getattr(mapping.product, "name", "") if mapping.product else "",
                "updated_at": mapping.updated_at,
            }
        )

    return render_template(
        "admin/mappings.html",
        mappings=mapping_rows,
        products=products,
        error=error,
        success=success,
    )


@app.route("/admin/mappings/<int:mapping_id>/delete", methods=["POST"])
@admin_required
def admin_delete_mapping(mapping_id):
    mapping = db.session.get(DiseaseProductMapping, mapping_id)
    if mapping is None:
        abort(404)
    db.session.delete(mapping)
    db.session.commit()
    return redirect("/admin/mappings")
=======
from routes.admin_routes import register_admin_routes

register_admin_routes(
    app,
    deps={
        "db": db,
        "AdminUser": AdminUser,
        "StoreProduct": StoreProduct,
        "StoreOrder": StoreOrder,
        "DiseaseProductMapping": DiseaseProductMapping,
        "ADMIN_EMAIL": ADMIN_EMAIL,
        "STORE_CATEGORY_ORDER": STORE_CATEGORY_ORDER,
        "FULFILLMENT_STATUS_ORDER": FULFILLMENT_STATUS_ORDER,
        "is_admin_authenticated": is_admin_authenticated,
        "admin_required": admin_required,
        "require_csrf": require_csrf,
        "rate_limit_exceeded": rate_limit_exceeded,
        "_client_ip": _client_ip,
        "check_admin_password": check_admin_password,
        "get_fulfillment_status": get_fulfillment_status,
        "set_fulfillment_status": set_fulfillment_status,
        "normalize_disease_key": normalize_disease_key,
        "slugify_crop_name": slugify_crop_name,
        "estimate_store_mrp": estimate_store_mrp,
        "compute_store_discount": compute_store_discount,
        "save_product_image_upload": save_product_image_upload,
        "default_store_seller": default_store_seller,
    },
)
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057


@app.route("/predict-disease", methods=["POST"])
def predict_disease():
    import json
    from pathlib import Path
    from PIL import Image, ImageOps, UnidentifiedImageError # type: ignore
    import io
    
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        # Return JSON because this endpoint is always called via fetch().
        return jsonify({"success": False, "error": "Security check failed. Please refresh and try again."}), 400

    uploaded_file = request.files.get("crop_image")
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Upload a crop leaf image before starting analysis."}), 400

    suffix = Path(uploaded_file.filename).suffix.lower()
    if suffix and suffix not in ALLOWED_IMAGE_SUFFIXES:
        return jsonify({"error": "Please upload a PNG, JPG, JPEG, or WEBP image."}), 400

    try:
        image_bytes = read_upload_bytes(uploaded_file, MAX_DISEASE_IMAGE_BYTES, label="Image")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return jsonify({"error": "The uploaded file could not be read as an image."}), 400

    weather = fetch_weather_bundle(user.location or "Bhubaneswar")
    preview_url = save_uploaded_leaf_image(image, uploaded_file.filename)
    crop_key = normalize_crop_key(user.crop_type or "generic")

    def build_library_context(payload):
        disease_name = str(payload.get("disease") or "").strip()
        crop_name = str(payload.get("crop") or user.crop_type or crop_key.title()).strip()
        if not disease_name:
            return None

        if normalize_disease_key(disease_name) in {"healthy", "no disease", "no-disease"}:
            payload["library_url"] = "/library/tips"
            session["last_library_diagnosis"] = {
                "slug": "healthy",
                "confidence": int(payload.get("confidence") or 0),
                "risk_level": str(payload.get("risk_level") or ""),
                "note": "No major pest or disease detected. Keep monitoring regularly.",
                "image_url": preview_url,
            }
            return None

        entry = get_best_disease_library_entry(disease_name, crop_name)
        slug = slugify_crop_name(disease_name)
        payload["library_url"] = (
            f"/library/disease/{entry['slug']}" if entry is not None else f"/library/diseases?q={quote(disease_name)}"
        )
        session["last_library_diagnosis"] = {
            "slug": entry["slug"] if entry is not None else slug,
            "confidence": int(payload.get("confidence") or 0),
            "risk_level": str(payload.get("risk_level") or ""),
            "note": "Nearby crop conditions suggest this issue should be checked quickly in the field.",
            "image_url": preview_url,
        }
        return entry
    
    # Integrate PyTorch model + disease_knowledge as primary detection
    class_index, conf_float, class_name, confidence_pct = predict_with_pytorch(image)
<<<<<<< HEAD
    if class_name is not None and conf_float is not None:
=======
    model_label = str(class_name or "").strip()
    if model_label and conf_float is not None and float(conf_float) >= 0.46 and "plantvillage" not in model_label.lower():
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
        disease_info = get_disease_info(class_index, conf_float)
        crop_display = model_label.split("___")[0] if "___" in model_label else (user.crop_type or crop_key.title())
        crop_display = crop_display.replace("_", " ").strip() or (user.crop_type or crop_key.title())
        library_entry = get_best_disease_library_entry(disease_info["disease"], crop_display)
        prevention_tips = list((library_entry or {}).get("prevention") or [])
        if not prevention_tips:
            prevention_tips = unique_crop_list(
                [
                    *([str(tip).strip() for tip in disease_info.get("prevention_tips", []) if str(tip).strip()] if isinstance(disease_info.get("prevention_tips"), list) else []),
                    str(disease_info.get("recommendation") or "").strip(),
                ]
            )[:3]

        new_history = DiseaseHistory(
            user_id=user.id,
            crop_type=crop_display,
            detected_disease=disease_info["disease"],
            confidence=confidence_pct,
        )
        db.session.add(new_history)
        db.session.commit()

        response_payload = {
            "success": True,
            "disease": disease_info["disease"],
            "confidence": confidence_pct,
<<<<<<< HEAD
            "cause": disease_info["cause"],
            "symptoms": disease_info.get("symptoms", ""),
            "organic_solution": disease_info.get("recommendation", ""),
            "chemical_solution": disease_info["solution"],
            "prevention": [disease_info.get("recommendation", "")],
=======
            "cause": (library_entry or {}).get("cause") or disease_info["cause"],
            "symptoms": summarize_disease_symptoms(library_entry, disease_info.get("symptoms") or disease_info.get("recommendation", disease_info["cause"])),
            "organic_solution": disease_info.get("organic_solution") or derive_organic_solution(disease_info["disease"], library_entry, crop_display),
            "chemical_solution": (library_entry or {}).get("solution") or disease_info["solution"],
            "prevention": prevention_tips,
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
            "explanation_hinglish": disease_info["explanation_hinglish"],
            "diagnostic_reason": f"PyTorch model detected {model_label} ({confidence_pct}%)",
            "risk_level": "Low" if int(confidence_pct) > 85 else "Medium" if int(confidence_pct) > 70 else "High",
            "best_product": disease_info.get("best_product", ""),
            "product_link": disease_info.get("product_link", ""),
            "image_url": preview_url,
<<<<<<< HEAD
            "crop": crop_display,
            "analysis_source": "Vision model",
        }
        return jsonify(attach_store_recommendation(response_payload, disease_info.get("best_product", "")))
    
    crop_input = (user.crop_type or "generic").lower().strip()
    if crop_input in ["paddy", "peddy", "dhan", "paddi"]:
        crop_key = "rice"
    elif crop_input in ["corn"]:
        crop_key = "maize"
    else:
        crop_key = crop_input
=======
            "crop": crop_display
        }
        build_library_context(response_payload)

        return jsonify(attach_store_recommendation(response_payload, disease_info.get("best_product", "")))
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057

    # Advanced Prompt for Expert Analysis
    prompt = f"""
    As a Plant Pathology Expert specializing in {crop_key}, analyze this leaf image.
    Use diagnostic criteria comparable to Kaggle/PlantVillage datasets to provide a precise diagnosis.
    
    Location Context: {user.location or 'India'}
    Anticipated Crop: {crop_key}
    
    Provide the analysis in the following strict JSON format:
    {{
      "disease": "Specific Scientific/Common Name",
      "confidence": integer 0-100,
      "symptoms": "Description of visible markers",
      "cause": "Specific biological or environmental cause",
      "organic_solution": "Non-chemical treatment",
      "chemical_solution": "Recommended fungicide/pesticide",
      "prevention": ["tip 1", "tip 2", "tip 3"],
      "explanation_hinglish": "A simple 2-sentence explanation in Hinglish (Hindi + English) for the farmer",
      "diagnostic_reason": "Provide a brief explainability note on why this diagnosis was reached.",
      "risk_level": "Low/Medium/High",
      "crop": "Detected crop name"
    }}
    Return ONLY raw JSON. No markdown.
    """
    
    diagnosis = {}
    success = False
    try:
        if not GEMINI_API_KEY:
            raise ValueError("Gemini API key is not configured.")

        for model_name in ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-pro-vision"]:
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content([prompt, image])
                text_resp = response.text.strip()
                if "```json" in text_resp:
                    text_resp = text_resp.split("```json")[1].split("```")[0].strip()
                elif "```" in text_resp:
                    text_resp = text_resp.split("```")[1].split("```")[0].strip()
                diagnosis = json.loads(text_resp)
                if diagnosis.get("disease"):
                     success = True
                     break
            except Exception:
                continue
        if not success:
             raise ValueError("All models failed")
    except Exception as e:
        print(f"AI Detection failed: {e}")
<<<<<<< HEAD
        library = CROP_DISEASE_LIBRARY.get(crop_key, CROP_DISEASE_LIBRARY.get("generic", []))
        if library:
            # Diverse fallback: Use a deterministic index based on the image size & content hash
            import hashlib
            img_hash = int(hashlib.md5(image_bytes).hexdigest(), 16)
            idx = img_hash % len(library)
            fallback_entry = library[idx]
            
            diagnosis = {
                "disease": fallback_entry["name"],
                "confidence": 65, # Higher confidence for "detected" fallback
                "symptoms": "Visible spots and pattern stress observed on leaf.",
                "cause": fallback_entry["cause"],
                "organic_solution": "Apply organic neem oil spray.",
                "chemical_solution": fallback_entry["solution"],
                "prevention": fallback_entry["prevention_tips"],
                "explanation_hinglish": f"Ye scan aapke crop '{crop_key}' ke liye '{fallback_entry['name']}' ki sambhavna dikha raha hai.",
                "diagnostic_reason": "Pattern recognition fallback (Visual Analysis).",
                "risk_level": "Medium",
                "crop": crop_key.capitalize()
            }
=======
        features, signals, base_confidence = extract_leaf_features(image, weather)
        seed_value = int(sha1(image_bytes[:2048]).hexdigest(), 16) % 65537
        fallback_entry, fallback_crop_display, confidence = select_visual_disease_entry(
            user.crop_type or crop_key,
            features,
            weather,
            signals,
            seed=seed_value,
        )
        library_entry = get_best_disease_library_entry(fallback_entry["name"], fallback_crop_display)

        diagnosis = {
            "disease": fallback_entry["name"],
            "confidence": max(int(confidence), int(base_confidence)),
            "symptoms": summarize_disease_symptoms(library_entry, "Visible lesion and stress patterns detected on the leaf."),
            "cause": (library_entry or {}).get("cause") or fallback_entry["cause"],
            "organic_solution": derive_organic_solution(fallback_entry["name"], library_entry, fallback_crop_display),
            "chemical_solution": (library_entry or {}).get("solution") or fallback_entry["solution"],
            "prevention": list((library_entry or {}).get("prevention") or fallback_entry["prevention_tips"]),
            "explanation_hinglish": f"Ye scan {fallback_crop_display} mein '{fallback_entry['name']}' jaisa stress pattern dikha raha hai. Field me same symptoms compare karke jaldi action lo.",
            "diagnostic_reason": "Visual symptom scoring used leaf color, lesion pattern, humidity context, and crop-specific disease profiles.",
            "risk_level": "Low" if int(confidence) >= 88 else "Medium" if int(confidence) >= 72 else "High",
            "crop": fallback_crop_display,
        }

    diagnosis_crop = str(diagnosis.get("crop") or user.crop_type or crop_key.title()).strip() or (user.crop_type or "Crop")
    diagnosis_entry = get_best_disease_library_entry(diagnosis.get("disease"), diagnosis_crop)
    diagnosis["crop"] = diagnosis_crop
    diagnosis["symptoms"] = summarize_disease_symptoms(diagnosis_entry, diagnosis.get("symptoms"))
    diagnosis["cause"] = str(diagnosis.get("cause") or (diagnosis_entry or {}).get("cause") or "").strip()
    diagnosis["chemical_solution"] = str(
        diagnosis.get("chemical_solution") or (diagnosis_entry or {}).get("solution") or "Consult a local expert for a confirmed spray schedule."
    ).strip()
    diagnosis["organic_solution"] = str(
        diagnosis.get("organic_solution") or derive_organic_solution(diagnosis.get("disease"), diagnosis_entry, diagnosis_crop)
    ).strip()
    prevention_items = diagnosis.get("prevention", [])
    if not isinstance(prevention_items, list):
        prevention_items = [prevention_items]
    diagnosis["prevention"] = unique_crop_list(
        [
            *[str(item).strip() for item in prevention_items if str(item).strip()],
            *([str(item).strip() for item in (diagnosis_entry or {}).get("prevention", []) if str(item).strip()] if diagnosis_entry else []),
        ]
    )[:4]
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057

    new_history = DiseaseHistory( # type: ignore
        user_id=user.id,
        crop_type=diagnosis.get("crop", user.crop_type or "Crop"),  # type: ignore
        detected_disease=diagnosis.get("disease", "Unknown"),  # type: ignore
        confidence=int(diagnosis.get("confidence", 80)),  # type: ignore
    )
    db.session.add(new_history)
    db.session.commit()

    response_payload = {
        "success": True,
        "disease": diagnosis.get("disease"),
        "confidence": diagnosis.get("confidence"),
        "cause": diagnosis.get("cause"),
        "symptoms": diagnosis.get("symptoms"),
        "organic_solution": diagnosis.get("organic_solution"),
        "chemical_solution": diagnosis.get("chemical_solution"),
        "prevention": diagnosis.get("prevention", []),
        "explanation_hinglish": diagnosis.get("explanation_hinglish"),
        "diagnostic_reason": diagnosis.get("diagnostic_reason", "Visual cues identified."),
        "risk_level": diagnosis.get("risk_level"),
        "image_url": preview_url,
<<<<<<< HEAD
        "crop": diagnosis.get("crop", user.crop_type or "Crop"),
        "analysis_source": "Expert AI" if success else "Visual fallback",
    }
    return jsonify(attach_store_recommendation(response_payload))
=======
        "crop": diagnosis.get("crop", user.crop_type or "Crop")
    }
    build_library_context(response_payload)
    return jsonify(attach_store_recommendation(response_payload, diagnosis.get("best_product", "")))
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057


@app.route("/profile", methods=["GET", "POST"])
def profile():
    user = get_current_user()
    if not user:
        return redirect("/login")

    error = None
    success = None

    if request.method == "POST":
        csrf_resp = require_csrf()
        if csrf_resp is not None:
            return csrf_resp

        form_type = request.form.get("form_type", "")

        if form_type == "user_info":
            old_email = user.email
            new_name = request.form.get("name", "").strip()
            new_email = request.form.get("email", "").strip()
            current_pw = request.form.get("current_password", "").strip()
            new_pw = request.form.get("new_password", "").strip()
            confirm_pw = request.form.get("confirm_password", "").strip()

            if new_name:
                user.name = new_name
                session["user"] = new_name
            if new_email:
                existing_user = User.query.filter(User.email == new_email, User.id != user.id).first()
                if existing_user is not None:
                    error = "Another account already uses this email address."
                else:
                    user.email = new_email

            # Handle photo upload
            photo_file = request.files.get("profile_photo")
            if not error and photo_file and photo_file.filename:
                try:
                    user.profile_photo = save_profile_photo_upload(photo_file, f"profile_{user.id}")
                except ValueError as exc:
                    error = str(exc)

            # Password change
            if not error and (new_pw or confirm_pw or current_pw):
                password_ok, _ = check_user_password(user, current_pw, upgrade_legacy=False)
                if not current_pw:
                    error = "Enter your current password to set a new one."
                elif not password_ok:
                    error = "Current password is incorrect."
                elif len(new_pw) < 8:
                    error = "New password must be at least 8 characters."
                elif new_pw != confirm_pw:
                    error = "New passwords do not match."
                else:
                    user.password = hash_password(new_pw)
                    if not error:
                        success = "Password updated successfully."

            if not error:
                preferences = get_or_create_user_preferences(user, commit=False)
                if preferences.alert_email in {"", None, old_email} and new_email:
                    preferences.alert_email = new_email
                db.session.commit()
                if not success:
                    success = "Profile info saved successfully."
            else:
                db.session.rollback()

        elif form_type == "farm_info":
            user.location = request.form.get("location", "").strip() or user.location
            user.farm_size = request.form.get("farm_size", "").strip() or user.farm_size
            user.crop_type = request.form.get("crop_type", "").strip() or user.crop_type
            primary_farm, _ = ensure_user_farm_setup(user, commit=False)
            if primary_farm is not None:
                primary_farm.location = user.location
                primary_farm.farm_size = user.farm_size
                primary_farm.crop_type = user.crop_type
            db.session.commit()
            success = "Farm info saved successfully."

    weather = fetch_weather_bundle(user.location or "Bhubaneswar")
    soil = build_soil_profile(user, weather)
    soil_score = min(int(float(soil["moisture"]) * 0.34 + float(soil["nitrogen"]) * 0.34 + (100 - abs(float(soil["ph"]) - 6.4) * 20) * 0.32), 99)  # type: ignore
    soil_score_display = f"{soil_score / 100:.2f}"
    disease_status = "No active diseases detected in your region."
    
    profile_data = {
        "success": success,
        "error": error,
        "soil_score": soil_score,
        "soil_score_display": soil_score_display,
        "disease_status": disease_status,
    }

    return render_template(
        "profile.html",
        user=user,
        success=success,
        error=error,
        soil_score=soil_score,
        soil_score_display=soil_score_display,
        disease_status=disease_status,
    )


@app.route("/api/farm-details")
def get_farm_details():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    primary_farm, _ = ensure_user_farm_setup(user)
    weather = fetch_weather_bundle(user.location or "Bhubaneswar")
    soil = build_soil_profile(user, weather)
    crop_health = build_crop_health(user, weather, soil)
    
    # Safe data parsing
    health_score = int(crop_health.get('score', 80))
    soil_ph = float(soil.get('ph', 6.5))
    soil_n = int(soil.get('nitrogen', 50))
    lat = float(weather.get('lat', 20.3))
    lon = float(weather.get('lon', 85.8))
    
    fields = [
        {
            "id": 1,
            "name": f"{user.crop_type or 'Rice'} Field - North",
            "health": f"{health_score}%",
            "soil": f"pH {soil_ph}, N {soil_n}%",
            "alerts": "No critical alerts" if health_score > 70 else "Low nitrogen detected",
            "coords": [lat, lon]
        },
        {
            "id": 2,
            "name": f"{user.crop_type or 'Rice'} Field - South",
            "health": f"{max(0, health_score - 5)}%",
            "soil": f"pH {round(soil_ph + 0.2, 1)}, N {max(0, soil_n - 4)}%",
            "alerts": "Check for moisture stress",
            "coords": [lat - 0.002, lon + 0.002]
        }
    ]
    return jsonify({"success": True, "location": user.location, "fields": fields})


@app.route("/dashboard/ndvi-preview")
def dashboard_ndvi_preview():
    user = get_current_user()
    if not user:
        return redirect("/login")
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    if lat is None or lon is None:
        weather = fetch_weather_bundle(user.location or "Bhubaneswar")
        lat = weather["lat"]
        lon = weather["lon"]
    image_bytes = fetch_ndvi_preview(lat, lon)
    if image_bytes:
        return Response(image_bytes, mimetype="image/png")
    return Response(build_ndvi_fallback_svg(user), mimetype="image/svg+xml")


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    user = get_current_user()
    if not user:
        return redirect("/login")

    preferences = get_or_create_user_preferences(user)
    success = None

    if request.method == "POST":
        csrf_resp = require_csrf()
        if csrf_resp is not None:
            return csrf_resp

        preferences.crop_alerts = request.form.get("crop_alerts") == "on"
        preferences.disease_alerts = request.form.get("disease_alerts") == "on"
        preferences.weather_alerts = request.form.get("weather_alerts") == "on"
        preferences.data_updates = request.form.get("data_updates") == "on"
        preferences.email_alerts = request.form.get("email_alerts") == "on"
        preferences.sms_alerts = request.form.get("sms_alerts") == "on"
        preferences.daily_briefing = request.form.get("daily_briefing") == "on"
        preferences.alert_email = (request.form.get("alert_email") or user.email or "").strip()
        preferences.alert_phone = (request.form.get("alert_phone") or user.phone or "").strip()
        if preferences.alert_phone:
            user.phone = preferences.alert_phone
        db.session.commit()
        success = "Settings saved successfully."

    primary_farm, farms = ensure_user_farm_setup(user)
    task_summary = build_task_summary(user, limit=12)
    history_items = build_recent_activity(user, limit=10)

    settings_data = {
        "preferences": preferences,
        "history": history_items,
        "primary_farm": primary_farm,
        "farm_count": len(farms),
        "task_summary": task_summary,
        "enabled_alert_count": sum(
            [
                1 if preferences.crop_alerts else 0,
                1 if preferences.disease_alerts else 0,
                1 if preferences.weather_alerts else 0,
                1 if preferences.data_updates else 0,
            ]
        ),
        "enabled_channel_count": sum(
            [
                1 if preferences.email_alerts else 0,
                1 if preferences.sms_alerts else 0,
                1 if preferences.daily_briefing else 0,
            ]
        ),
        "security_items": [
            {"label": "Email verified path", "value": user.email or "Missing"},
            {"label": "Primary farm", "value": primary_farm.name if primary_farm else "Not set"},
            {"label": "Open tasks", "value": str(task_summary["open_count"])},
        ],
    }

    return render_template("settings.html", user=user, settings_page=settings_data, success=success)


@app.route("/alerts")
def alerts_page():
    user = get_current_user()
    if not user:
        return redirect("/login")

    weather = fetch_weather_bundle(user.location or "Bhubaneswar")
    soil = build_soil_profile(user, weather)
    crop_health = build_crop_health(user, weather, soil)
    recommendations = build_recommendations(user, weather, soil, crop_health)

    alert_cards: list[dict] = []

    if float(weather.get("temp", 0)) >= 33:  # type: ignore
        alert_cards.append({
            "severity": "heat",
            "title": "Heatwave Warning",
            "detail": f"High temperatures over the next three days. Plan irrigation adjustments.",
            "time_ago": "1d ago",
        })

    alert_cards.append({
        "severity": "rain",
        "title": "Rainfall Alert",
        "detail": "Heavy rainfall expected tomorrow. Check for waterlogging risks.",
        "time_ago": "2d ago",
    })

    alert_cards.append({
        "severity": "disease",
        "title": "Disease Detected",
        "detail": f"Blight detected in your {user.crop_type or 'crop'} field. Inspect crops for affected plants.",
        "time_ago": "4d ago",
    })

    history_items = [
        {"severity": "heat", "title": "Heatwave Warning", "time_ago": "1 day ago"},
        {"severity": "disease", "title": f"Blight detected in {user.crop_type or 'Crop'} Field", "time_ago": "1 day ago"},
        {"severity": "rain", "title": "Heavy Rainfall Expected", "time_ago": "2 days ago"},
        {"severity": "soil", "title": "Low Nitrogen in Rice Field 2", "time_ago": "1 week ago"},
    ]

    alerts_data = {
        "alert_cards": list(alert_cards[:3]),  # type: ignore
        "recommendations": recommendations[:2],  # type: ignore
        "history": history_items,
    }

    return render_template("alerts.html", user=user, alerts_page=alerts_data)


@app.route("/api/ai-chat", methods=["POST"])
def ai_chat():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query") or "").strip()
    if not query:
        return jsonify({"success": False, "error": "No query provided"}), 400

    history = sanitize_ai_chat_history(payload.get("history"))
    fallback_reply = build_kisan_dost_reply(user, query, history=history)
    groq_reply = ask_groq_ai_crop_doctor(user, query, history)
    if groq_reply:
        return jsonify({"success": True, "response": groq_reply, "provider": "groq"})

    use_gemini_chat = os.getenv("USE_GEMINI_CHAT", "").strip().lower() in {"1", "true", "yes"}

    if use_gemini_chat and GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            recent_conversation = format_ai_chat_history_for_prompt(history)
            persona = f"""
            You are 'Kisan Dost', a friendly AI Agriculture Expert assistant.
            The farmer is asking you questions via voice or text.
            Understand follow-up questions using the recent conversation.
            Keep your answers concise (max 4 sentences), practical, helpful, and in simple Hinglish (Hindi + English).
            If the user asks a vague follow-up, infer the topic from the recent conversation before replying.
            Farmer context: Location: {user.location or 'India'}, Crop: {user.crop_type or 'General'}.
            """
            prompt_parts = [persona.strip()]
            if recent_conversation:
                prompt_parts.append(f"Recent conversation:\n{recent_conversation}")
            prompt_parts.append(f"Farmer Query: {query}")
            response = model.generate_content("\n\n".join(prompt_parts))
            return jsonify({"success": True, "response": response.text.strip(), "provider": "gemini"})
        except Exception as e:
            print(f"Chat API Error: {e}")

    return jsonify({"success": True, "response": fallback_reply, "provider": "fallback"})


@app.route("/ai-insights")
def ai_insights_page():
    user = get_current_user()
    if not user:
        return redirect("/login")

    weather = fetch_weather_bundle(user.location or "Bhubaneswar")
    soil = build_soil_profile(user, weather)
    crop_health = build_crop_health(user, weather, soil)

    crop_name = user.crop_type or "Rice"
    yield_prediction = round(float(crop_health["yield_prediction"]) * 0.042, 1)  # type: ignore

    if float(weather.get("humidity", 0)) >= 75:  # type: ignore
        advice = "High humidity detected, suggested preventive fungicide application"
    elif float(weather.get("temp", 0)) >= 34:  # type: ignore
        advice = "High temperature detected, increase irrigation frequency"
    else:
        advice = f"Optimal growing conditions for {crop_name}. Maintain current practices."

    if float(weather.get("temp", 0)) >= 35:  # type: ignore
        risk_summary = "Risk Alert: Heatwave forecasted"
    elif float(weather.get("rainfall_mm", 0)) >= 8:  # type: ignore
        risk_summary = "Risk Alert: Heavy rainfall expected"
    else:
        risk_summary = "Low risk. Weather conditions stable for current crop cycle."

    seed_source = f"{user.id}-{user.email}-yield"
    seed = sum((i + 1) * ord(c) for i, c in enumerate(seed_source))
    yield_data = [
        round(yield_prediction * 0.7 + (int(seed) % 5) * 0.1, 1),
        round(yield_prediction * 0.85 + (int(seed) % 3) * 0.1, 1),
        round(yield_prediction * 0.95, 1),
        round(yield_prediction, 1),
    ]

    ai_recommendations = [
        {"icon": "water", "title": "Irrigation Scheduling", "detail": "Optimal irrigation times based on weather data"},
        {"icon": "fertilizer", "title": "Fertilizer Management", "detail": f"Customized fertilizer recommendations for nitrogen deficiency"},
        {"icon": "rotation", "title": "Crop Rotation", "detail": "Recommend alternate crops to improve soil health."},
        {"icon": "rotation", "title": "Crop Rotation", "detail": "Recommend alternate growing schedule for better yield."},
    ]

    ai_data = {
        "advice": advice,
        "crop_name": crop_name,
        "yield_prediction": yield_prediction,
        "risk_summary": risk_summary,
        "recommendations": ai_recommendations,
        "yield_chart_data": yield_data,
    }

    return render_template("ai_insights.html", user=user, ai_page=ai_data)


@app.route("/community")
def community():
    user = get_current_user()
    if not user:
        return redirect("/login")
    
    posts = CommunityPost.query.order_by(CommunityPost.date.desc()).all()
    return render_template("community.html", user=user, posts=posts)


@app.route("/community/post", methods=["POST"])
def community_post():
    user = get_current_user()
    if not user:
        return redirect("/login")

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp
    
    title = request.form.get("title")
    content = request.form.get("content")
    category = request.form.get("category", "General")
    
    if title and content:
        new_post = CommunityPost(user_id=user.id, title=title, content=content, category=category) # type: ignore
        db.session.add(new_post)
        db.session.commit()
        
    return redirect("/community")


@app.route("/community/comment/<int:post_id>", methods=["POST"])
def community_comment(post_id):
    user = get_current_user()
    if not user:
        return redirect("/login")

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp
    
    content = request.form.get("content")
    if content:
        new_comment = CommunityComment(post_id=post_id, user_id=user.id, content=content) # type: ignore
        db.session.add(new_comment)
        db.session.commit()
    
    return redirect("/community")


@app.route("/tools")
def tools_page():
    user = get_current_user()
    if not user:
        return redirect("/login")
    
    # We might want to pass some context data if needed
    weather = fetch_weather_bundle(user.location or "Bhubaneswar")
    return render_template("tools.html", user=user, weather=weather)


@app.route("/api/tool-advisor", methods=["POST"])
def tool_advisor():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp
    
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400
    
    # Contextual prompt for Gemini
    calc_type = data.get("type", "farming")
    results = data.get("results", {})
    inputs = data.get("inputs", {})
    
    prompt = f"""
    You are an AI Farming Advisor. Based on these {calc_type} calculation results:
    Inputs: {inputs}
    Results: {results}
    
    Provide a professional but friendly 2-3 sentence suggestion in Hinglish (Hindi + English) to help the farmer optimize their {calc_type} strategy.
    Farmer Context: Location: {user.location or 'India'}, Crop: {user.crop_type or 'General'}.
    """
    
    try:
        if not GEMINI_API_KEY:
            raise ValueError("Gemini API key is not configured.")
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        return jsonify({"success": True, "suggestion": response.text.strip()})
    except Exception as e:
        print(f"Tool Advisor Error: {e}")
        # Fallback suggestion
        return jsonify({"success": True, "suggestion": "Aapka profit margin improve ho sakta hai. Fertilizer usage optimize karein aur market rates check karein."})


@app.route("/api/predict-yield", methods=["POST"])
def predict_yield():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp
    
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400
    
    crop = data.get("crop", "Rice")
    area = data.get("area", 1)
    rainfall = data.get("rainfall", 100)
    soil_type = data.get("soil_type", "Loamy")
    
    prompt = f"""
    Analyze the following for crop yield prediction:
    Crop: {crop}
    Area: {area} acres
    Rainfall: {rainfall} mm
    Soil Type: {soil_type}
    
    Provide a JSON response with:
    {{
      "estimated_yield_kg": number,
      "estimated_profit_inr": number,
      "ai_confidence": 0-100,
      "ai_note": "A short note on why this prediction was made in Hinglish."
    }}
    Return ONLY raw JSON.
    """
    
    try:
        if not GEMINI_API_KEY:
            raise ValueError("Gemini API key is not configured.")
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        text = response.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
            
        prediction = json.loads(text)
        return jsonify({"success": True, "prediction": prediction})
    except Exception as e:
        print(f"Yield Prediction Error: {e}")
        # Static fallback for demo
        import random
        base_yield = area * 1800 if crop == "Rice" else area * 1400
        return jsonify({
            "success": True, 
            "prediction": {
                "estimated_yield_kg": round(base_yield + random.randint(-200, 200)),
                "estimated_profit_inr": round(base_yield * 20),
                "ai_confidence": 75,
                "ai_note": "Based on moderate rainfall and soil type, yield is expected to be stable. Weather conditions support good growth."
            }
        })


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/subscription-required")
def subscription_required():
    # Backward-compatible route: send users to the new plans page.
    user = get_current_user()
    if not user:
        return redirect("/login")
    return redirect("/subscriptions?required=1")


@app.route("/subscriptions")
def subscription_plans():
    user = get_current_user()
    if not user:
        return redirect("/login")
    ensure_user_subscription_state(user, commit=True)
<<<<<<< HEAD
=======
    expired = bool(request.args.get("expired")) or bool(request.args.get("required"))
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    return render_template(
        "subscriptions.html",
        user=user,
        plans=SUBSCRIPTION_PLANS,
        trial_active=is_trial_active(user),
<<<<<<< HEAD
=======
        expired=expired,
        trial_days=SUBSCRIPTION_TRIAL_DAYS,
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    )


def create_razorpay_order_amount_inr(amount_inr, receipt, notes=None):
    """Create a Razorpay order for a raw INR amount (subscription, wallet topups, etc.)."""
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return None, "Razorpay credentials are not configured."

    try:
        amount_paise = int(round(float(amount_inr) * 100))
    except (TypeError, ValueError):
        return None, "Invalid amount."

    payload = {
        "amount": max(amount_paise, 0),
        "currency": RAZORPAY_CURRENCY,
        "receipt": str(receipt or f"sub_{uuid.uuid4().hex[:10]}"),
        "notes": notes or {},
    }

    try:
        req = Request(
            "https://api.razorpay.com/v1/orders",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {b64encode(f'{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}'.encode('utf-8')).decode('ascii')}",
            },
            method="POST",
        )
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except (HTTPError, URLError, ValueError) as exc:
        return None, str(exc)


def apply_user_subscription(user, plan_name):
    plan = normalize_plan_name(plan_name)
    if plan == "free":
        user.plan = "free"
        user.subscription_start_date = None
        user.subscription_end_date = None
        sync_legacy_pro_flag(user)
        return

    now = datetime.now(timezone.utc)
    days = int(SUBSCRIPTION_PLANS[plan]["duration_days"] or 30)
    user.plan = plan
    user.subscription_start_date = now
    user.subscription_end_date = now + timedelta(days=days)
    sync_legacy_pro_flag(user)


@app.route("/api/subscription/create-order", methods=["POST"])
def api_subscription_create_order():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

<<<<<<< HEAD
=======
    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp

>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    payload = request.get_json(silent=True) or {}
    plan = normalize_plan_name(payload.get("plan"))
    if plan == "free":
        return jsonify({"success": False, "error": "Free plan does not require payment."}), 400

    price_inr = int(SUBSCRIPTION_PLANS[plan]["price_inr"])
    try:
        requested_wallet_use = int(payload.get("wallet_use_inr") or 0)
    except (TypeError, ValueError):
        requested_wallet_use = 0

    wallet_balance = int(user.wallet_balance or 0)
    wallet_use = max(0, min(requested_wallet_use if requested_wallet_use else wallet_balance, wallet_balance, price_inr))
    amount_due = price_inr - wallet_use

    payment = SubscriptionPayment(  # type: ignore
        user_id=user.id,
        plan=plan,
        amount_inr=price_inr,
        wallet_used_inr=wallet_use,
        status="created",
    )
    db.session.add(payment)
    db.session.commit()

    # Fully covered by wallet.
    if amount_due <= 0:
        if wallet_use:
            if not wallet_debit(user, wallet_use, "subscription_wallet_applied", {"payment_id": payment.id, "plan": plan}):
                payment.status = "failed"
                db.session.commit()
                return jsonify({"success": False, "error": "Insufficient wallet balance."}), 400

        apply_user_subscription(user, plan)
        payment.status = "paid"
        db.session.commit()
        return jsonify({"success": True, "checkout_mode": "wallet", "payment_id": payment.id})

    receipt = f"sub_{payment.id}_{user.id}"
    notes = {"user_id": str(user.id), "plan": plan, "wallet_used_inr": str(wallet_use)}
    order, err = create_razorpay_order_amount_inr(amount_due, receipt, notes=notes)
    if not order:
        payment.status = "demo"
        db.session.commit()
        return jsonify(
            {
                "success": True,
                "checkout_mode": "demo",
                "payment_id": payment.id,
                "amount_inr": amount_due,
                "message": "Razorpay order could not be created; demo mode enabled.",
            }
        )

    payment.status = "pending"
    payment.razorpay_order_id = str(order.get("id") or "")
    db.session.commit()

    return jsonify(
        {
            "success": True,
            "checkout_mode": "razorpay",
            "payment_id": payment.id,
            "key": RAZORPAY_KEY_ID,
            "currency": RAZORPAY_CURRENCY,
            "order_id": payment.razorpay_order_id,
            "amount_paise": int(order.get("amount") or 0),
            "name": "AgroVision AI Subscription",
            "description": f"{SUBSCRIPTION_PLANS[plan]['label']} plan (30 days)",
            "prefill": {"name": user.name or "", "email": user.email or "", "contact": user.phone or ""},
        }
    )


@app.route("/api/subscription/verify-payment", methods=["POST"])
def api_subscription_verify_payment():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

<<<<<<< HEAD
=======
    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp

>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    payload = request.get_json(silent=True) or {}
    try:
        payment_id = int(payload.get("payment_id") or 0)
    except (TypeError, ValueError):
        payment_id = 0

    payment = SubscriptionPayment.query.filter_by(id=payment_id, user_id=user.id).first()
    if not payment:
        return jsonify({"success": False, "error": "Payment record not found."}), 404

    if payment.status == "paid":
        ensure_user_subscription_state(user, commit=True)
        return jsonify({"success": True, "message": "Already paid."})

    razorpay_order_id = str(payload.get("razorpay_order_id") or payment.razorpay_order_id or "")
    razorpay_payment_id = str(payload.get("razorpay_payment_id") or payment.razorpay_payment_id or "")
    razorpay_signature = str(payload.get("razorpay_signature") or payment.razorpay_signature or "")

    signature_ok = True
    if payment.status != "demo":
        signature_ok = verify_razorpay_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature)

    if not signature_ok:
        payment.status = "failed"
        db.session.commit()
        return jsonify({"success": False, "error": "Payment verification failed."}), 400

    payment.razorpay_order_id = razorpay_order_id or payment.razorpay_order_id
    payment.razorpay_payment_id = razorpay_payment_id or payment.razorpay_payment_id
    payment.razorpay_signature = razorpay_signature or payment.razorpay_signature

    wallet_use = int(payment.wallet_used_inr or 0)
    if wallet_use:
        if not wallet_debit(user, wallet_use, "subscription_wallet_applied", {"payment_id": payment.id, "plan": payment.plan}):
            payment.status = "failed"
            db.session.commit()
            return jsonify({"success": False, "error": "Insufficient wallet balance."}), 400

    apply_user_subscription(user, payment.plan)
    payment.status = "paid"
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/user", methods=["GET"])
def api_user_profile():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    ensure_user_subscription_state(user, commit=True)
    expiry = user.subscription_end_date.isoformat() if user.subscription_end_date else None
    start = user.subscription_start_date.isoformat() if user.subscription_start_date else None
    return jsonify(
        {
            "success": True,
            "user": {
                "id": int(user.id),
                "name": user.name,
                "email": user.email,
                "plan": normalize_plan_name(user.plan),
                "subscriptionStartDate": start,
                "expiryDate": expiry,
                "referralCode": user.referral_code,
                "walletBalance": int(user.wallet_balance or 0),
            },
        }
    )


@app.route("/api/apply-wallet", methods=["POST"])
def api_apply_wallet():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

<<<<<<< HEAD
=======
    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp

>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    payload = request.get_json(silent=True) or {}
    plan = normalize_plan_name(payload.get("plan"))
    if plan == "free":
        return jsonify({"success": True, "price_inr": 0, "wallet_use_inr": 0, "amount_due_inr": 0})

    price_inr = int(SUBSCRIPTION_PLANS[plan]["price_inr"])
    try:
        wallet_use = int(payload.get("wallet_use_inr") or 0)
    except (TypeError, ValueError):
        wallet_use = 0

    balance = int(user.wallet_balance or 0)
    applied = max(0, min(wallet_use if wallet_use else balance, balance, price_inr))
    return jsonify(
        {
            "success": True,
            "price_inr": price_inr,
            "wallet_use_inr": applied,
            "amount_due_inr": max(0, price_inr - applied),
        }
    )


# --- Compatibility REST aliases (beginner-friendly endpoints) ---

@app.route("/user", methods=["GET"])
def user_endpoint_alias():
    return api_user_profile()


@app.route("/apply-wallet", methods=["POST"])
def apply_wallet_alias():
    return api_apply_wallet()


@app.route("/create-order", methods=["POST"])
def create_order_alias():
    payload = request.get_json(silent=True) or {}
    kind = str(payload.get("type") or "subscription").strip().lower()
    if kind == "subscription":
        return api_subscription_create_order()
    if kind == "store":
        return store_checkout()
    return jsonify({"success": False, "error": "Unsupported order type."}), 400


@app.route("/verify-payment", methods=["POST"])
def verify_payment_alias():
    payload = request.get_json(silent=True) or {}
    kind = str(payload.get("type") or "subscription").strip().lower()
    if kind == "subscription":
        return api_subscription_verify_payment()
    if kind == "store":
        return store_payment_success()
    return jsonify({"success": False, "error": "Unsupported payment type."}), 400


@app.route("/refer-and-earn")
def refer_and_earn():
    user = get_current_user()
    if not user:
        return redirect("/login")
    
    referrals_count = User.query.filter_by(referred_by=user.referral_code).count()
    base = (request.host_url or "").rstrip("/")
    referral_link = f"{base}/register?ref={user.referral_code}"
    return render_template("refer_and_earn.html", user=user, referrals_count=referrals_count, referral_link=referral_link)


@app.route("/upgrade-to-pro", methods=["POST"])
def upgrade_to_pro():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
<<<<<<< HEAD
=======

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp
>>>>>>> 46a09c90cfcc0ec9f84d5761ca933d6cc76fa057
    
    payload = request.get_json(silent=True) or {}
    plan = normalize_plan_name(payload.get("plan") or "pro")
    if plan == "free":
        return jsonify({"success": False, "error": "Invalid plan."}), 400

    # Legacy demo endpoint: used by older template flows.
    apply_user_subscription(user, plan)
    db.session.commit()
    return jsonify({"success": True, "message": f"Account upgraded to {plan.title()} successfully!"})


with app.app_context():
    db.create_all()
    # --- Auto-migrate: add missing columns to existing SQLite tables ---
    import sqlite3 as _sqlite3
    _db_path = os.path.join(app.instance_path, "database.db")
    print(f"[MIGRATION] Looking for database at: {_db_path}")
    if os.path.exists(_db_path):
        _conn = _sqlite3.connect(_db_path)
        _cur = _conn.cursor()
        _cur.execute("PRAGMA table_info(user)")
        _existing_cols = {row[1] for row in _cur.fetchall()}
        print(f"[MIGRATION] Existing columns: {_existing_cols}")
        _new_columns = {
            "is_pro": "BOOLEAN DEFAULT 0",
            "plan": "VARCHAR(16) DEFAULT 'free'",
            "trial_start_date": "DATETIME",
            "subscription_start_date": "DATETIME",
            "subscription_end_date": "DATETIME",
            "referral_code": "VARCHAR(20)",
            "referred_by": "VARCHAR(20)",
            "loyalty_points": "INTEGER DEFAULT 0",
            "wallet_balance": "INTEGER DEFAULT 0",
        }
        for col_name, col_type in _new_columns.items():
            if col_name not in _existing_cols:
                try:
                    _cur.execute(f"ALTER TABLE user ADD COLUMN {col_name} {col_type}")
                    print(f"[MIGRATION] âœ… Added column '{col_name}' to user table.")
                except Exception as e:
                    print(f"[MIGRATION] âš  Skipping '{col_name}': {e}")
        _cur.execute("PRAGMA table_info(store_product)")
        _store_product_cols = {row[1] for row in _cur.fetchall()}
        if "stock" not in _store_product_cols:
            try:
                _cur.execute("ALTER TABLE store_product ADD COLUMN stock INTEGER DEFAULT 0")
                print("[MIGRATION] Added column 'stock' to store_product table.")
            except Exception as e:
                print(f"[MIGRATION] Skipping 'stock' on store_product: {e}")

        _conn.commit()
        _conn.close()
        print("[MIGRATION] Database migration check complete.")
    else:
        print(f"[MIGRATION] Database file not found at {_db_path}, skipping migration.")

    seeded_products = seed_store_products()
    if seeded_products:
        print(f"[STORE] Seeded or refreshed {seeded_products} store products.")
    seeded_mappings = seed_disease_product_mappings()
    if seeded_mappings:
        print(f"[STORE] Seeded or refreshed {seeded_mappings} disease-product mappings.")

    # Seed a DB-backed admin user (keeps env defaults working too).
    # Requested default: admin123@gmail.com / 123 (override via ADMIN_EMAIL/ADMIN_PASSWORD env vars).
    try:
        _admin_email = (ADMIN_EMAIL or "").strip().lower()
        _admin_pw = (ADMIN_PASSWORD or "").strip()
        if _admin_email and _admin_pw:
            _existing_admin = AdminUser.query.filter_by(email=_admin_email).first()  # type: ignore
            if _existing_admin is None:
                db.session.add(
                    AdminUser(  # type: ignore
                        email=_admin_email,
                        password_hash=hash_password(_admin_pw),
                        role="admin",
                    )
                )
                db.session.commit()
            else:
                # Upgrade legacy/plain stored password to hash if needed.
                if not is_password_hash(_existing_admin.password_hash or ""):
                    _existing_admin.password_hash = hash_password(_admin_pw)
                    db.session.commit()
    except Exception:
        db.session.rollback()

    # --- Auto-migrate: add missing columns to existing SQLite tables ---
    import sqlite3 as _sqlite3
    _db_path = os.path.join(app.instance_path, "database.db")
    print(f"[MIGRATION] Looking for database at: {_db_path}")
    if os.path.exists(_db_path):
        _conn = _sqlite3.connect(_db_path)
        _cur = _conn.cursor()
        _cur.execute("PRAGMA table_info(user)")
        _existing_cols = {row[1] for row in _cur.fetchall()}
        print(f"[MIGRATION] Existing columns: {_existing_cols}")
        _new_columns = {
            "is_pro": "BOOLEAN DEFAULT 0",
            "plan": "VARCHAR(16) DEFAULT 'free'",
            "trial_start_date": "DATETIME",
            "subscription_start_date": "DATETIME",
            "subscription_end_date": "DATETIME",
            "referral_code": "VARCHAR(20)",
            "referred_by": "VARCHAR(20)",
            "loyalty_points": "INTEGER DEFAULT 0",
            "wallet_balance": "INTEGER DEFAULT 0",
        }
        for col_name, col_type in _new_columns.items():
            if col_name not in _existing_cols:
                try:
                    _cur.execute(f"ALTER TABLE user ADD COLUMN {col_name} {col_type}")
                    print(f"[MIGRATION] Added column '{col_name}' to user table.")
                except Exception as e:
                    print(f"[MIGRATION] Skipping '{col_name}': {e}")
        _cur.execute("PRAGMA table_info(store_product)")
        _store_product_cols = {row[1] for row in _cur.fetchall()}
        if "stock" not in _store_product_cols:
            try:
                _cur.execute("ALTER TABLE store_product ADD COLUMN stock INTEGER DEFAULT 0")
                print("[MIGRATION] Added column 'stock' to store_product table.")
            except Exception as e:
                print(f"[MIGRATION] Skipping 'stock' on store_product: {e}")

        _conn.commit()
        _conn.close()
        print("[MIGRATION] Database migration check complete.")
    else:
        print(f"[MIGRATION] Database file not found at {_db_path}, skipping migration.")

    seeded_products = seed_store_products()
    if seeded_products:
        print(f"[STORE] Seeded or refreshed {seeded_products} store products.")


if __name__ == "__main__":
    app.run(debug=True, port=8000)
