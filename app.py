import json
import os
import random
import re
import smtplib
import time
import uuid
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from io import BytesIO
from pathlib import Path
from hashlib import sha1
from datetime import date, datetime, timedelta, timezone
from html import escape
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import numpy as np # type: ignore
from flask import Flask, Response, redirect, render_template, request, session, jsonify # type: ignore
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

app.secret_key = (
    os.getenv("FLASK_SECRET_KEY")
    or os.getenv("SECRET_KEY")
    or os.getenv("APP_SECRET_KEY")
    or uuid.uuid4().hex
)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (os.getenv("FLASK_ENV") or "").strip().lower() == "production"

db = SQLAlchemy(app)

UPLOADS_DIR = Path(app.root_path) / "static" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app.config["UPLOAD_FOLDER"] = str(UPLOADS_DIR)
KISAN_DOST_KNOWLEDGE_PATH = Path(app.root_path) / "dataset" / "kisan_dost_faq.json"
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
    disease_histories = db.relationship('DiseaseHistory', backref='user', lazy=True)
    farms = db.relationship('Farm', backref='user', lazy=True, cascade="all, delete-orphan")
    farm_tasks = db.relationship('FarmTask', backref='user', lazy=True, cascade="all, delete-orphan")
    preferences = db.relationship('UserPreference', backref='user', uselist=False, lazy=True, cascade="all, delete-orphan")

    def __init__(self, **kwargs):
        super(User, self).__init__(**kwargs)

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


def generate_otp():
    return "".join([str(random.randint(0, 9)) for _ in range(6)])


def is_password_hash(password_value):
    value = (password_value or "").strip()
    return value.startswith("pbkdf2:") or value.startswith("scrypt:")


def hash_password(password_value):
    return generate_password_hash(password_value, method="pbkdf2:sha256", salt_length=16)


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


def clear_otp_session_state():
    for key in (
        "otp",
        "otp_target",
        "otp_type",
        "otp_user_id",
        "otp_expiry",
        "pending_user",
    ):
        session.pop(key, None)


def save_profile_photo_upload(file_storage, prefix):
    original_name = secure_filename(file_storage.filename or "")
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("Only PNG, JPG, JPEG, or WEBP images are allowed.")

    file_name = f"{prefix}_{uuid.uuid4().hex[:12]}{suffix}"
    save_path = UPLOADS_DIR / file_name
    file_storage.save(str(save_path))
    return file_name


# SMTP Configuration
APP_DISPLAY_NAME = (os.getenv("APP_NAME") or "AgroVisionAI").strip() or "AgroVisionAI"
SMTP_SERVER = (os.getenv("SMTP_SERVER") or "smtp.gmail.com").strip()
SMTP_PORT = get_env_int("SMTP_PORT", 587)
SMTP_EMAIL = (os.getenv("SMTP_EMAIL") or "").strip()
SMTP_PASSWORD = (os.getenv("SMTP_PASSWORD") or "").strip()
SMTP_SENDER_NAME = (os.getenv("SMTP_SENDER_NAME") or APP_DISPLAY_NAME).strip() or APP_DISPLAY_NAME
SMTP_USE_SSL = (os.getenv("SMTP_USE_SSL") or "").strip().lower() in {"1", "true", "yes", "on"}
SMTP_TIMEOUT_SECONDS = get_env_int("SMTP_TIMEOUT_SECONDS", 20)


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
        f"Namaste,\n\n"
        f"Your one-time verification code is: {otp}\n\n"
        "This OTP is valid for the next 5 minutes.\n"
        "If you did not request this code, you can safely ignore this email.\n\n"
        f"- Team {APP_DISPLAY_NAME}"
    )


def build_otp_email_html(otp, logo_cid=None):
    logo_markup = (
        f'<img src="cid:{logo_cid}" alt="{APP_DISPLAY_NAME}" '
        'style="display:block;width:240px;max-width:100%;margin:0 auto 24px;">'
        if logo_cid
        else f'<div style="font-size:28px;font-weight:800;letter-spacing:-0.03em;color:#ffffff;">{APP_DISPLAY_NAME}</div>'
    )

    return f"""\
<!DOCTYPE html>
<html lang="en">
  <body style="margin:0;padding:0;background:#061427;font-family:Manrope,Segoe UI,Arial,sans-serif;color:#eaf4ff;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#061427;padding:32px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;background:linear-gradient(180deg,#081b35 0%,#0d274b 100%);border:1px solid rgba(118,173,255,0.18);border-radius:28px;overflow:hidden;">
            <tr>
              <td style="padding:28px 28px 14px;background:radial-gradient(circle at top, rgba(125,255,46,0.12), transparent 34%),radial-gradient(circle at right top, rgba(42,194,255,0.14), transparent 28%),#081b35;">
                {logo_markup}
                <div style="display:inline-block;padding:8px 14px;border-radius:999px;background:rgba(255,255,255,0.08);border:1px solid rgba(152,201,255,0.16);color:#c5ebff;font-size:12px;font-weight:800;letter-spacing:0.08em;text-transform:uppercase;">
                  Smart Farming Solutions
                </div>
                <h1 style="margin:18px 0 10px;font-size:34px;line-height:1.08;color:#ffffff;font-weight:800;letter-spacing:-0.04em;">
                  Your verification code is ready
                </h1>
                <p style="margin:0;color:#d6e7fb;font-size:16px;line-height:1.75;">
                  Welcome to {APP_DISPLAY_NAME}. Use the OTP below to securely complete your login or registration.
                </p>
              </td>
            </tr>
            <tr>
              <td style="padding:20px 28px 8px;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-radius:22px;background:rgba(255,255,255,0.05);border:1px solid rgba(167,205,255,0.14);">
                  <tr>
                    <td align="center" style="padding:28px 20px;">
                      <div style="color:#9ecfff;font-size:13px;font-weight:800;letter-spacing:0.12em;text-transform:uppercase;margin-bottom:12px;">
                        One-Time Password
                      </div>
                      <div style="display:inline-block;padding:16px 22px;border-radius:18px;background:linear-gradient(135deg,#7dff2e 0%,#30e6bf 54%,#46c4ff 100%);color:#03101f;font-size:34px;font-weight:900;letter-spacing:0.32em;text-indent:0.32em;">
                        {otp}
                      </div>
                      <p style="margin:16px 0 0;color:#d8e8fb;font-size:15px;line-height:1.7;">
                        This code will expire in <strong style="color:#ffffff;">5 minutes</strong>.
                      </p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:8px 28px 0;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                  <tr>
                    <td style="width:50%;padding:10px 10px 0 0;vertical-align:top;">
                      <div style="padding:16px 18px;border-radius:18px;background:rgba(255,255,255,0.04);border:1px solid rgba(167,205,255,0.12);">
                        <div style="color:#7dff2e;font-size:14px;font-weight:800;margin-bottom:8px;">Why this email?</div>
                        <div style="color:#d8e8fb;font-size:14px;line-height:1.7;">
                          You recently requested secure access to your {APP_DISPLAY_NAME} account.
                        </div>
                      </div>
                    </td>
                    <td style="width:50%;padding:10px 0 0 10px;vertical-align:top;">
                      <div style="padding:16px 18px;border-radius:18px;background:rgba(255,255,255,0.04);border:1px solid rgba(167,205,255,0.12);">
                        <div style="color:#2dd8ff;font-size:14px;font-weight:800;margin-bottom:8px;">Security tip</div>
                        <div style="color:#d8e8fb;font-size:14px;line-height:1.7;">
                          Never share this OTP with anyone. If you did not request it, simply ignore this message.
                        </div>
                      </div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:24px 28px 30px;">
                <div style="height:1px;background:rgba(167,205,255,0.12);margin-bottom:18px;"></div>
                <div style="color:#bcd5ef;font-size:13px;line-height:1.8;">
                  Sent by <strong style="color:#ffffff;">{APP_DISPLAY_NAME}</strong><br>
                  AI-powered crop monitoring, disease detection, and climate insights for modern farmers.
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
    # Debug Print for Terminal
    print(f"\n--- [DEBUG OTP] OTP for {target_email} is: {otp} ---\n")

    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print("SMTP credentials are not configured. OTP email skipped.")
        return False

    logo_bytes = load_email_logo_bytes()
    logo_cid = None
    if logo_bytes:
        logo_cid = make_msgid(domain="agrovisionai.local")[1:-1]

    msg = EmailMessage()
    msg["Subject"] = f"{APP_DISPLAY_NAME} - Verification Code"
    msg["From"] = formataddr((SMTP_SENDER_NAME, SMTP_EMAIL))
    msg["To"] = target_email
    msg["Reply-To"] = SMTP_EMAIL
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
        return True
    except Exception as e:
        print(f"SMTP Error: {e}")
        return False


def clamp(value, lower, upper):
    return max(lower, min(value, upper))

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


def build_kisan_dost_reply(user, query):
    crop_name = normalize_kisan_dost_crop_name(user.crop_type or "crop")
    location_name = user.location or "aapke area"
    query_text = (query or "").strip()
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


def remember_notice(session_key, text, tone="success"):
    session[session_key] = {"text": text, "tone": tone}


def get_current_user():
    user = None
    if "user_id" in session:
        user = db.session.get(User, session["user_id"])

    if user is None and "user" in session:
        user = User.query.filter_by(name=session["user"]).first()

    if user is not None:
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

    if weather["rainfall_mm"] < 4:
        recommendations.append(
            {
                "title": "Plan a light irrigation cycle",
                "detail": f"Rainfall near {location_name} is low, so maintain moisture for {crop_name}.",
            }
        )

    if float(soil.get("ph", 0)) < 6.1:
        recommendations.append(
            {
                "title": "Correct acidic soil balance",
                "detail": "Add lime or organic compost to bring the field closer to a balanced pH range.",
            }
        )

    if weather["temp"] >= 34:
        recommendations.append(
            {
                "title": "Protect plants from heat stress",
                "detail": "Shift irrigation and field inspection to cooler hours to reduce midday stress.",
            }
        )

    if float(soil.get("nitrogen", 0)) < 45:
        recommendations.append(
            {
                "title": "Boost nitrogen before the next cycle",
                "detail": f"{crop_name} will benefit from a nutrient top-up within the next few days.",
            }
        )

    recommendations.append(
        {
            "title": "Review satellite and NDVI zones",
            "detail": "Compare the live map and vegetation preview to inspect weaker field patches early.",
        }
    )

    return list(recommendations[:3])  # type: ignore


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

    y_axis = [f"{round(high)}°", f"{round(mid)}°", f"{round(low)}°"]

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
    image_url = preview_url or build_disease_sample_data_uri(result["disease"])

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


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""

        user = User.query.filter_by(email=email).first()
        password_ok, upgraded = check_user_password(user, password)

        if user and password_ok:
            if upgraded:
                db.session.commit()
            otp = generate_otp()
            session["otp"] = otp
            session["otp_target"] = email
            session["otp_type"] = "login"
            session["otp_user_id"] = user.id
            session["otp_expiry"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()
            
            # Send Real Email
            email_sent = send_otp_email(email, otp)
            if not email_sent:
                print(f"DEBUG: Email delivery failed. Login OTP for {email} is {otp} (Simulation)")
            
            return redirect("/verify-otp")
        else:
            return render_template("login.html", error="Invalid email or password.")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        location = (request.form.get("location") or "").strip()
        crop = (request.form.get("crop") or "").strip()
        phone = (request.form.get("phone") or "").strip()

        if not name:
            return render_template("register.html", error="Full name is required.")

        if not email or not phone:
            return render_template("register.html", error="Email and Phone Number are mandatory for registration.")

        if len(password) < 8:
            return render_template("register.html", error="Password must be at least 8 characters long.")

        existing_user = User.query.filter_by(email=email).first()
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
        session["pending_user"] = {
            "name": name,
            "email": email,
            "password": password,
            "location": location,
            "crop": crop,
            "phone": phone,
            "profile_photo": profile_photo
        }
        
        otp = generate_otp()
        session["otp"] = otp
        session["otp_target"] = email
        session["otp_type"] = "register"
        session["otp_expiry"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()
        
        # Send Real Email
        email_sent = send_otp_email(email, otp)
        if not email_sent:
            print(f"DEBUG: Email delivery failed. Registration OTP for {email} is {otp} (Simulation)")
        
        return redirect("/verify-otp")

    return render_template("register.html")


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    if "otp" not in session:
        return redirect("/login")
        
    error = None
    if request.method == "POST":
        user_otp = request.form.get("otp", "")
        
        # Check expiry
        if datetime.now(timezone.utc).timestamp() > session.get("otp_expiry", 0):
            error = "OTP has expired. Please try again."
        elif user_otp == session["otp"]:
            otp_type = session["otp_type"]
            
            if otp_type == "register" and "pending_user" in session:
                data = session["pending_user"]
                new_user = User( # type: ignore
                    name=data["name"],
                    email=data["email"],
                    password=hash_password(data["password"]),
                    location=data["location"],
                    crop_type=data["crop"],
                    phone=data["phone"],
                    profile_photo=data["profile_photo"]
                )
                db.session.add(new_user)
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
                
            elif otp_type == "login" and "otp_user_id" in session:
                user_id = session["otp_user_id"]
                user = User.query.get(user_id)
                if user:
                    session["user_id"] = user.id
                    session["user"] = user.name
                    clear_otp_session_state()
                    return redirect("/dashboard")
        else:
            error = "Invalid OTP. Please check and try again."
            
    return render_template("verify_otp.html", target=session.get("otp_target"), error=error)


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


@app.route("/farm-twin")
def farm_twin():
    user = get_current_user()
    if not user:
        return redirect("/login")

    farm_twin_page = build_farm_twin_context(user)
    return render_template("farm_twin.html", user=user, farm_twin=farm_twin_page)


@app.route("/disease-detection", methods=["GET"])
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


@app.route("/predict-disease", methods=["POST"])
def predict_disease():
    import json
    from pathlib import Path
    from PIL import Image, ImageOps, UnidentifiedImageError # type: ignore
    import io
    
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    uploaded_file = request.files.get("crop_image")
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Upload a crop leaf image before starting analysis."}), 400

    suffix = Path(uploaded_file.filename).suffix.lower()
    if suffix and suffix not in ALLOWED_IMAGE_SUFFIXES:
        return jsonify({"error": "Please upload a PNG, JPG, JPEG, or WEBP image."}), 400

    image_bytes = uploaded_file.read()
    if not image_bytes:
        return jsonify({"error": "Uploaded image is empty."}), 400

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return jsonify({"error": "The uploaded file could not be read as an image."}), 400

    weather = fetch_weather_bundle(user.location or "Bhubaneswar")
    preview_url = save_uploaded_leaf_image(image, uploaded_file.filename)
    
    # Integrate PyTorch model + disease_knowledge as primary detection
    class_index, conf_float, class_name, confidence_pct = predict_with_pytorch(image)
    if class_index and conf_float is not None:
        disease_info = get_disease_info(class_index, conf_float)
        crop_display = class_name.split("___")[0] if "___" in class_name else class_name
        
        new_history = DiseaseHistory(
            user_id=user.id,
            crop_type=crop_display,
            detected_disease=disease_info["disease"],
            confidence=confidence_pct,
        )
        db.session.add(new_history)
        db.session.commit()
        
        return jsonify({
            "success": True,
            "disease": disease_info["disease"],
            "confidence": disease_info["confidence"],
            "cause": disease_info["cause"],
            "symptoms": disease_info.get("recommendation", disease_info["cause"]),
            "organic_solution": disease_info.get("recommendation", ""),
            "chemical_solution": disease_info["solution"],
            "prevention": [disease_info.get("recommendation", "")],
            "explanation_hinglish": disease_info["explanation_hinglish"],
            "diagnostic_reason": f"PyTorch model detected {class_name} ({confidence_pct}%)",
            "risk_level": "Low" if int(confidence_pct) > 85 else "Medium" if int(confidence_pct) > 70 else "High",
            "best_product": disease_info.get("best_product", ""),
            "product_link": disease_info.get("product_link", ""),
            "image_url": preview_url,
            "crop": crop_display
        })
    
    crop_input = (user.crop_type or "generic").lower().strip()
    if crop_input in ["paddy", "peddy", "dhan", "paddi"]:
        crop_key = "rice"
    elif crop_input in ["corn"]:
        crop_key = "maize"
    else:
        crop_key = crop_input

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
    except Exception:
        library = CROP_DISEASE_LIBRARY.get(crop_key, CROP_DISEASE_LIBRARY.get("generic", []))
        if library:
            fallback_entry = library[0]
            diagnosis = {
                "disease": fallback_entry["name"],
                "confidence": 60,
                "symptoms": "Visible spots matching fallback profile.",
                "cause": fallback_entry["cause"],
                "organic_solution": "Remove infected leaves.",
                "chemical_solution": fallback_entry["solution"],
                "prevention": fallback_entry["prevention_tips"],
                "explanation_hinglish": f"Ye scan aapke crop '{crop_key}' ke liye '{fallback_entry['name']}' dikha raha hai.",
                "diagnostic_reason": "Fallback pattern recognition used.",
                "risk_level": "Medium",
                "crop": crop_key.capitalize()
            }

    new_history = DiseaseHistory( # type: ignore
        user_id=user.id,
        crop_type=diagnosis.get("crop", user.crop_type or "Crop"),  # type: ignore
        detected_disease=diagnosis.get("disease", "Unknown"),  # type: ignore
        confidence=int(diagnosis.get("confidence", 80)),  # type: ignore
    )
    db.session.add(new_history)
    db.session.commit()

    return jsonify({
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
        "crop": diagnosis.get("crop", user.crop_type or "Crop")
    })


@app.route("/profile", methods=["GET", "POST"])
def profile():
    user = get_current_user()
    if not user:
        return redirect("/login")

    error = None
    success = None

    if request.method == "POST":
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
                ext = Path(photo_file.filename).suffix.lower()
                if ext in ALLOWED_IMAGE_SUFFIXES:
                    user.profile_photo = save_profile_photo_upload(photo_file, f"profile_{user.id}")
                else:
                    error = "Only PNG, JPG, JPEG, or WEBP images are allowed."

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
    
    query = request.json.get("query")
    if not query:
        return jsonify({"success": False, "error": "No query provided"}), 400

    fallback_reply = build_kisan_dost_reply(user, query)
    use_gemini_chat = os.getenv("USE_GEMINI_CHAT", "").strip().lower() in {"1", "true", "yes"}

    if use_gemini_chat and GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            persona = f"""
            You are 'Kisan Dost', a friendly AI Agriculture Expert assistant.
            The farmer is asking you questions via voice.
            Keep your answers concise (max 3 sentences), helpful, and in Hinglish (Hindi + English).
            Farmer context: Location: {user.location or 'India'}, Crop: {user.crop_type or 'General'}.
            """
            response = model.generate_content(f"{persona}\nFarmer Query: {query}")
            return jsonify({"success": True, "response": response.text.strip()})
        except Exception as e:
            print(f"Chat API Error: {e}")

    return jsonify({"success": True, "response": fallback_reply})


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


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=True)
