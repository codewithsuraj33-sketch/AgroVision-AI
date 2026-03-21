# pyre-ignore-all-errors
import hmac
import json
import os
import random
import re
import smtplib
import textwrap
import threading
import time
import uuid
from base64 import b64encode
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from functools import wraps
from hashlib import sha1, sha256
from html import escape
from io import BytesIO
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

import google.generativeai as genai  # type: ignore
import numpy as np  # type: ignore
from flask import Flask, Response, abort, has_app_context, jsonify, redirect, render_template, request, send_file, session  # type: ignore
from flask_sqlalchemy import SQLAlchemy  # type: ignore
from PIL import Image, ImageOps, UnidentifiedImageError  # type: ignore
from werkzeug.security import check_password_hash, generate_password_hash  # type: ignore
from werkzeug.utils import secure_filename  # type: ignore

try:
    import torch  # type: ignore
    from torchvision import transforms  # type: ignore
except Exception:
    torch = None

app = Flask(__name__)

SHARED_UI_CSS_TAG = '<link rel="stylesheet" href="/static/shared-ui.css">'
SHARED_UI_JS_TAG = '<script src="/static/shared-ui.js"></script>'


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
    return response


def normalize_pdf_text(value, max_length=None):
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    if max_length is not None and max_length > 3 and len(text) > max_length:
        text = str(text)[0 : int(max_length) - 3].rstrip() + "..."
    return str(text.encode("ascii", "replace").decode("ascii"))


def slugify_download_token(value, default="report"):
    normalized = normalize_pdf_text(value).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug or default


def compact_pdf_list(values, limit=5, item_length=180):
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []

    normalized_items = []
    for item in values:
        text = normalize_pdf_text(item, item_length)
        if text:
            normalized_items.append(text)
        if len(normalized_items) >= limit:
            break
    return normalized_items


def escape_pdf_text(value):
    return str(value or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap_pdf_line(text, width=92, bullet=False):
    normalized = normalize_pdf_text(text)
    if not normalized:
        return []
    return textwrap.wrap(
        normalized,
        width=width,
        initial_indent="- " if bullet else "",
        subsequent_indent="  " if bullet else "",
        break_long_words=False,
        break_on_hyphens=False,
    ) or [normalized]


def format_pdf_rgb(color):
    return " ".join(f"{max(0.0, min(1.0, float(channel))):.3f}" for channel in color)


def build_pdf_rect_command(x, y, width, height, fill_color=None, stroke_color=None, line_width=1):
    commands = ["q"]
    if fill_color is not None:
        commands.append(f"{format_pdf_rgb(fill_color)} rg")
    if stroke_color is not None:
        commands.append(f"{format_pdf_rgb(stroke_color)} RG")
        commands.append(f"{line_width:.2f} w")
    commands.append(f"{x:.2f} {y:.2f} {width:.2f} {height:.2f} re")
    if fill_color is not None and stroke_color is not None:
        commands.append("B")
    elif fill_color is not None:
        commands.append("f")
    else:
        commands.append("S")
    commands.append("Q")
    return commands


def build_pdf_text_commands(lines, x, y, font="F1", size=10.5, leading=14, color=(0.12, 0.18, 0.16)):
    safe_lines = [normalize_pdf_text(line) for line in lines if normalize_pdf_text(line)]
    if not safe_lines:
        return []

    commands = [
        "BT",
        f"/{font} {size:.2f} Tf",
        f"{format_pdf_rgb(color)} rg",
        f"{leading:.2f} TL",
        f"1 0 0 1 {x:.2f} {y:.2f} Tm",
    ]
    for index, line in enumerate(safe_lines):
        if index:
            commands.append("T*")
        commands.append(f"({escape_pdf_text(line)}) Tj")
    commands.append("ET")
    return commands


def build_pdf_blocks(meta_lines=None, sections=None):
    blocks = []

    meta_content = []
    for meta_line in meta_lines or []:
        meta_content.extend(wrap_pdf_line(meta_line, width=82, bullet=False))
    if meta_content:
        blocks.append(
            {
                "heading": "Report Snapshot",
                "lines": meta_content,
                "height": 26 + 14 * len(meta_content) + 18,
                "fill_color": (0.94, 0.98, 0.95),
                "stroke_color": (0.78, 0.89, 0.81),
                "stripe_color": (0.26, 0.62, 0.41),
            }
        )

    for section in sections or []:
        heading = normalize_pdf_text(section.get("heading"), 80) or "Section"
        section_lines = []
        for paragraph in section.get("paragraphs") or []:
            section_lines.extend(wrap_pdf_line(paragraph, width=84, bullet=False))
            if section_lines:
                section_lines.append("")
        for item in section.get("items") or []:
            section_lines.extend(wrap_pdf_line(item, width=80, bullet=True))
        while section_lines and not section_lines[-1].strip():
            section_lines.pop()
        if not section_lines:
            continue

        chunk_size = 13
        for chunk_index in range(0, len(section_lines), chunk_size):
            chunk_lines = list(section_lines or [])[int(chunk_index) : int(chunk_index) + int(chunk_size)]
            chunk_heading = heading if chunk_index == 0 else f"{heading} (cont.)"
            blocks.append(
                {
                    "heading": chunk_heading,
                    "lines": chunk_lines,
                    "height": 28 + 14 * len(chunk_lines) + 18,
                    "fill_color": (1.0, 1.0, 1.0),
                    "stroke_color": (0.84, 0.90, 0.86),
                    "stripe_color": (0.95, 0.73, 0.16),
                }
            )

    return blocks


def paginate_pdf_blocks(blocks):
    page_top = 642
    page_bottom = 54
    gap = 14
    pages = []
    current_page = []
    cursor_y = page_top

    for block in blocks:
        block_height = float(block.get("height") or 0)
        if current_page and cursor_y - block_height < page_bottom:
            pages.append(current_page)
            current_page = []
            cursor_y = page_top
        current_page.append(dict(block, top_y=cursor_y))
        cursor_y -= block_height + gap

    if current_page:
        pages.append(current_page)
    return pages or [[]]


def build_pdf_page_commands(title, blocks, page_number, page_count):
    commands = []
    page_width = 612
    page_height = 792
    margin_x = 36
    content_width = page_width - (margin_x * 2)

    commands.extend(build_pdf_rect_command(0, 0, page_width, page_height, fill_color=(0.97, 0.98, 0.97)))
    commands.extend(build_pdf_rect_command(0, 676, page_width, 116, fill_color=(0.07, 0.32, 0.22)))
    commands.extend(build_pdf_rect_command(0, 664, page_width, 12, fill_color=(0.95, 0.73, 0.16)))

    title_lines = list(wrap_pdf_line(title, width=34, bullet=False) or [])[0:2]
    commands.extend(build_pdf_text_commands(["AgroVision AI"], margin_x, 756, font="F2", size=11, leading=13, color=(1, 1, 1)))
    commands.extend(build_pdf_text_commands(title_lines, margin_x, 730, font="F2", size=22, leading=24, color=(1, 1, 1)))
    commands.extend(
        build_pdf_text_commands(
            ["Styled report export for farm records, sharing, and follow-up actions."],
            margin_x,
            694,
            font="F1",
            size=9.6,
            leading=12,
            color=(0.90, 0.96, 0.92),
        )
    )

    if not blocks:
        commands.extend(
            build_pdf_rect_command(margin_x, 560, content_width, 70, fill_color=(1, 1, 1), stroke_color=(0.84, 0.90, 0.86))
        )
        commands.extend(build_pdf_text_commands(["No report content available."], margin_x + 18, 598, font="F2", size=12))

    for block in blocks:
        top_y = float(block["top_y"])
        height = float(block["height"])
        bottom_y = top_y - height
        commands.extend(
            build_pdf_rect_command(
                margin_x,
                bottom_y,
                content_width,
                height,
                fill_color=block.get("fill_color"),
                stroke_color=block.get("stroke_color"),
                line_width=1,
            )
        )
        commands.extend(
            build_pdf_rect_command(
                margin_x,
                top_y - 6,
                content_width,
                6,
                fill_color=block.get("stripe_color"),
            )
        )
        commands.extend(
            build_pdf_text_commands(
                [block.get("heading") or "Section"],
                margin_x + 18,
                top_y - 24,
                font="F2",
                size=12,
                leading=14,
                color=(0.10, 0.28, 0.18),
            )
        )
        commands.extend(
            build_pdf_text_commands(
                block.get("lines") or [],
                margin_x + 18,
                top_y - 44,
                font="F1",
                size=10.2,
                leading=13.5,
                color=(0.16, 0.22, 0.18),
            )
        )

    commands.extend(build_pdf_rect_command(margin_x, 30, content_width, 1.2, fill_color=(0.84, 0.90, 0.86)))
    commands.extend(
        build_pdf_text_commands(
            [f"Page {page_number} of {page_count}"],
            page_width - 112,
            18,
            font="F1",
            size=9,
            leading=11,
            color=(0.34, 0.42, 0.37),
        )
    )
    return commands


def build_text_pdf_bytes(title, meta_lines=None, sections=None):
    blocks = build_pdf_blocks(meta_lines=meta_lines, sections=sections)
    pages = paginate_pdf_blocks(blocks)

    objects = []
    page_objects = []
    font_regular_object_number = 3
    font_bold_object_number = 4
    next_object_number = 5

    for page_index, page_blocks in enumerate(pages, start=1):
        stream_commands = build_pdf_page_commands(title, page_blocks, page_index, len(pages))
        stream_bytes = "\n".join(stream_commands).encode("latin-1", errors="replace")
        content_object_number = next_object_number
        page_object_number = next_object_number + 1
        next_object_number += 2
        page_objects.append(page_object_number)

        objects.append(
            (
                content_object_number,
                b"<< /Length "
                + str(len(stream_bytes)).encode("ascii")
                + b" >>\nstream\n"
                + stream_bytes
                + b"\nendstream",
            )
        )
        objects.append(
            (
                page_object_number,
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 {font_regular_object_number} 0 R /F2 {font_bold_object_number} 0 R >> >> "
                    f"/Contents {content_object_number} 0 R >>"
                ).encode("ascii"),
            )
        )

    kids = " ".join(f"{page_object_number} 0 R" for page_object_number in page_objects)
    ordered_objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Count {len(page_objects)} /Kids [{kids}] >>".encode("ascii"),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    }
    for object_number, payload in objects:
        ordered_objects[object_number] = payload

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]

    for object_number in range(1, max(ordered_objects.keys()) + 1):
        offsets.append(len(pdf))
        pdf.extend(f"{object_number} 0 obj\n".encode("ascii"))
        pdf.extend(ordered_objects[object_number])
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in list(offsets)[int(1) : len(list(offsets))]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("ascii")
    )
    return bytes(pdf)


def build_pdf_download_response(filename, title, meta_lines=None, sections=None):
    safe_filename = f"{slugify_download_token(filename)}.pdf"
    pdf_bytes = build_text_pdf_bytes(title, meta_lines=meta_lines, sections=sections)
    download_requested = str(request.args.get("download") or "").strip().lower() in {"1", "true", "yes", "on"}
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=download_requested,
        download_name=safe_filename,
        max_age=0,
    )


def cache_last_disease_report_pdf(user, payload):
    if user is None or not isinstance(payload, dict) or not payload.get("success"):
        return

    etiology = payload.get("etiology") if isinstance(payload.get("etiology"), dict) else {}
    session["last_disease_report_pdf"] = {
        "user_id": int(getattr(user, "id", 0) or 0),
        "generated_at": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "location": normalize_pdf_text(getattr(user, "location", "") or "", 80),
        "crop": normalize_pdf_text(payload.get("crop"), 80),
        "disease": normalize_pdf_text(payload.get("disease"), 100),
        "report_title": normalize_pdf_text(payload.get("report_title"), 120),
        "confidence": normalize_pdf_text(payload.get("confidence_display") or f"{int(payload.get('confidence') or 0)}%", 24),
        "risk_level": normalize_pdf_text(payload.get("risk_level"), 32),
        "analysis_source": normalize_pdf_text(payload.get("analysis_source"), 60),
        "diagnostic_reason": normalize_pdf_text(payload.get("diagnostic_reason") or payload.get("why_this_result"), 280),
        "cause": normalize_pdf_text(payload.get("cause"), 220),
        "pathogen": normalize_pdf_text(etiology.get("pathogen"), 180),
        "environment": normalize_pdf_text(etiology.get("environment"), 180),
        "transmission": normalize_pdf_text(etiology.get("transmission"), 180),
        "symptoms": compact_pdf_list(payload.get("symptoms_list") or payload.get("matched_symptoms") or payload.get("symptoms"), limit=5, item_length=180),
        "organic_solutions": compact_pdf_list(payload.get("organic_solutions") or payload.get("organic_solution"), limit=4, item_length=180),
        "chemical_solutions": compact_pdf_list(payload.get("chemical_solutions") or payload.get("chemical_solution"), limit=4, item_length=180),
        "prevention_tips": compact_pdf_list(payload.get("prevention_tips") or payload.get("prevention"), limit=5, item_length=180),
        "do_now_checklist": compact_pdf_list(payload.get("do_now_checklist"), limit=4, item_length=180),
        "suggested_products": compact_pdf_list(
            [item.get("name") for item in payload.get("suggested_products", []) if isinstance(item, dict)],
            limit=3,
            item_length=100,
        ),
    }
    session.modified = True


def jsonify_disease_result(user, payload):
    cache_last_disease_report_pdf(user, payload)
    return jsonify(payload)


def build_fallback_disease_pdf_payload(user):
    latest_history = (
        DiseaseHistory.query.filter_by(user_id=user.id).order_by(DiseaseHistory.date.desc()).first()
        if user is not None
        else None
    )
    if latest_history is None:
        return None

    timestamp = normalize_timestamp(getattr(latest_history, "date", None))
    return {
        "report_title": normalize_pdf_text(f"{latest_history.detected_disease} Disease Report", 120),
        "generated_at": timestamp.strftime("%d %b %Y, %I:%M %p") if timestamp else datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "location": normalize_pdf_text(getattr(user, "location", "") or "", 80),
        "crop": normalize_pdf_text(getattr(latest_history, "crop_type", "") or getattr(user, "crop_type", "") or "Crop", 80),
        "disease": normalize_pdf_text(getattr(latest_history, "detected_disease", "") or "Unknown", 100),
        "confidence": normalize_pdf_text(f"{int(getattr(latest_history, 'confidence', 0) or 0)}%", 24),
        "risk_level": "Needs field review",
        "analysis_source": "Saved diagnosis history",
        "diagnostic_reason": "A full structured report was not cached, so this PDF summarizes the latest saved diagnosis history.",
        "cause": "",
        "pathogen": "",
        "environment": "",
        "transmission": "",
        "symptoms": [],
        "organic_solutions": [],
        "chemical_solutions": [],
        "prevention_tips": [],
        "do_now_checklist": ["Open disease detection again to generate a fresh full report PDF."],
        "suggested_products": [],
    }


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
GROQ_VISION_MODEL = (os.getenv("GROQ_VISION_MODEL") or "llama-3.2-11b-vision-preview").strip()

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

database_url = (os.getenv("DATABASE_URL") or "").strip()
if database_url.startswith("postgres://"):
    database_url = "postgresql://" + str(database_url)[len("postgres://") :]

app.config["SQLALCHEMY_DATABASE_URI"] = database_url or "sqlite:///database.db"
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
AI_CROP_DOCTOR_FAQ_PATH = Path(app.root_path) / "dataset" / "ai_crop_doctor_project_faq.txt"
AI_CROP_DOCTOR_LOCAL_QA_PATH = Path(app.root_path) / "dataset" / "ai_crop_doctor_local_qa.json"
AI_CROP_DOCTOR_CHAT_KNOWLEDGE_PATH = Path(app.root_path) / "dataset" / "ai_crop_doctor_chat_knowledge.json"
DISEASE_SYMPTOM_RULES_PATH = Path(app.root_path) / "dataset" / "disease_symptom_rules.json"
CROP_LIBRARY_DATA_PATH = Path(app.root_path) / "dataset" / "crop_library.json"
CULTIVATION_TIPS_DATA_PATH = Path(app.root_path) / "dataset" / "cultivation_tips.json"
STORE_PRODUCTS_DATA_PATH = Path(app.root_path) / "dataset" / "store_products.json"
DISEASE_STORE_PRODUCTS_DATA_PATH = Path(app.root_path) / "dataset" / "disease_store_products.json"
DISEASE_PRODUCT_MAPPINGS_DATA_PATH = Path(app.root_path) / "dataset" / "disease_product_mappings.json"
DISEASE_DATASET_PATH = Path(app.root_path) / "dataset" / "disease_data.json"
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
CULTIVATION_TIPS_CACHE = None
DISEASE_DATA_CACHE = None
PRODUCT_IMAGE_INDEX_CACHE = None
DISEASE_REFERENCE_SIGNATURE_CACHE = None
KAGGLE_REFERENCE_SIGNATURE_CACHE = None
AI_CROP_DOCTOR_CHAT_KNOWLEDGE_CACHE = None
AI_CROP_DOCTOR_CHAT_MATCH_ENTRIES_CACHE = None
LOCATION_GEOCODE_CACHE = {}
KAGGLE_REFERENCE_DATASET_DIRS = [
    Path(app.root_path) / "dataset" / "PlantVillage" / "PlantVillage",
    Path(app.root_path) / "dataset" / "kaggle_leaf_fallback",
]
DISABLED_DASHBOARD_MODULES = {
    "rent_tractor",
    "land_lease",
    "rural_services",
    "govt_schemes",
    "money_manager",
    "ai_crop_scan",
    "farming_solutions",
    "agri_market",
    "govt_buddy_ai",
    "my_wallet",
    "upgrade_hub",
}
CROP_LIBRARY_IMAGE_DIR = Path(app.root_path) / "static" / "images" / "crops"
CROP_LIBRARY_DEFAULT_IMAGE = "/static/images/default_crop.png"
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

# Admin Panel (defaults requested by user; override via environment for safety)
ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "admin123@gmail.com").strip().lower()
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "123").strip()
ADMIN_NOTIFY_EMAIL = (os.getenv("ADMIN_NOTIFY_EMAIL") or ADMIN_EMAIL).strip().lower()
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
        "description": "All Pro features + priority order tracking updates.",
    },
}

TRACTOR_SERVICE_CATEGORIES = [
    {"id": "all", "label": "All Services", "icon": "fa-table-cells-large"},
    {"id": "land_preparation", "label": "Land Preparation", "icon": "fa-tractor"},
    {"id": "sowing", "label": "Sowing", "icon": "fa-seedling"},
    {"id": "harvesting", "label": "Harvesting", "icon": "fa-scissors"},
    {"id": "transport", "label": "Transport", "icon": "fa-truck"},
    {"id": "spraying", "label": "Spraying", "icon": "fa-wind"},
]

TRACTOR_MARKETPLACE_MACHINES = [
    {"id": "rotavator-7ft-puri", "category": "land_preparation", "name": "Rotavator (7ft)", "hp": "45HP+", "price_per_hour": 800, "rating": 4.8, "rating_count": 124, "availability": "Available now", "lat": 19.8181, "lng": 85.8224, "seller": "Puri Agro Fleet", "features": ["Fast soil turning", "Residue mixing", "Same-day dispatch"]},
    {"id": "cultivator-9tyre-puri", "category": "land_preparation", "name": "Cultivator (9 Tyres)", "hp": "35HP+", "price_per_hour": 600, "rating": 4.6, "rating_count": 88, "availability": "Available in 15 min", "lat": 19.8092, "lng": 85.8408, "seller": "Mahadev Implements", "features": ["Bed loosening", "Affordable tillage", "Village support"]},
    {"id": "mb-plough-puri", "category": "land_preparation", "name": "MB Plough", "hp": "50HP+", "price_per_hour": 900, "rating": 4.7, "rating_count": 61, "availability": "Available in 20 min", "lat": 19.8218, "lng": 85.8451, "seller": "FieldPro Machinery", "features": ["Deep ploughing", "Heavy-duty frame", "Operator included"]},
    {"id": "laser-leveller-puri", "category": "land_preparation", "name": "Laser Leveller", "hp": "55HP+", "price_per_hour": 1200, "rating": 4.9, "rating_count": 33, "availability": "Available tomorrow", "lat": 19.7995, "lng": 85.8287, "seller": "Precision Farm Works", "features": ["Level field faster", "Water saving", "High-precision setup"]},
    {"id": "seed-drill-puri", "category": "sowing", "name": "Seed Drill", "hp": "40HP+", "price_per_hour": 700, "rating": 4.5, "rating_count": 71, "availability": "Available now", "lat": 19.8268, "lng": 85.8329, "seller": "Sowing Solutions Hub", "features": ["Uniform seed depth", "Faster coverage", "Operator ready"]},
    {"id": "rice-transplanter-puri", "category": "sowing", "name": "Rice Transplanter", "hp": "30HP+", "price_per_hour": 950, "rating": 4.7, "rating_count": 46, "availability": "Available in 40 min", "lat": 19.8044, "lng": 85.8501, "seller": "Paddy Tech Point", "features": ["Paddy nursery support", "Uniform spacing", "Lower labor cost"]},
    {"id": "maize-planter-puri", "category": "sowing", "name": "Maize Planter", "hp": "35HP+", "price_per_hour": 750, "rating": 4.4, "rating_count": 29, "availability": "Available in 30 min", "lat": 19.8122, "lng": 85.8146, "seller": "GreenRow Agro", "features": ["Row precision", "Fertilizer attachment", "Quick deployment"]},
    {"id": "mini-reaper-puri", "category": "harvesting", "name": "Mini Reaper", "hp": "28HP+", "price_per_hour": 850, "rating": 4.6, "rating_count": 58, "availability": "Available in 30 min", "lat": 19.8204, "lng": 85.8583, "seller": "Harvest Express", "features": ["Small plot friendly", "Clean cut", "Fuel efficient"]},
    {"id": "combine-harvester-puri", "category": "harvesting", "name": "Combine Harvester", "hp": "76HP+", "price_per_hour": 2200, "rating": 4.9, "rating_count": 41, "availability": "Available in 2 hr", "lat": 19.7925, "lng": 85.8164, "seller": "Odisha Harvest Pro", "features": ["Large acreage ready", "Operator with crew", "High output"]},
    {"id": "paddy-harvester-puri", "category": "harvesting", "name": "Paddy Harvester", "hp": "62HP+", "price_per_hour": 1800, "rating": 4.8, "rating_count": 39, "availability": "Available in 1 hr", "lat": 19.8075, "lng": 85.8612, "seller": "Rice Cut Fleet", "features": ["Paddy focused", "Low grain loss", "Field pickup ready"]},
    {"id": "tractor-trolley-puri", "category": "transport", "name": "Tractor Trolley", "hp": "35HP+", "price_per_hour": 500, "rating": 4.4, "rating_count": 93, "availability": "Available now", "lat": 19.8152, "lng": 85.8421, "seller": "Village Move Logistics", "features": ["Crop hauling", "Input carrying", "Local route support"]},
    {"id": "water-tanker-puri", "category": "transport", "name": "Water Tanker", "hp": "45HP+", "price_per_hour": 650, "rating": 4.5, "rating_count": 57, "availability": "Available in 20 min", "lat": 19.8017, "lng": 85.8362, "seller": "Aqua Farm Carrier", "features": ["Water supply", "Irrigation assist", "Fast refill cycle"]},
    {"id": "input-trailer-puri", "category": "transport", "name": "Input Carrier Trailer", "hp": "32HP+", "price_per_hour": 450, "rating": 4.3, "rating_count": 37, "availability": "Available in 25 min", "lat": 19.8227, "lng": 85.8174, "seller": "Farm Cargo Point", "features": ["Input delivery", "Feed transfer", "Flexible trip rate"]},
    {"id": "boom-sprayer-puri", "category": "spraying", "name": "Boom Sprayer", "hp": "25HP+", "price_per_hour": 900, "rating": 4.7, "rating_count": 68, "availability": "Available in 22 min", "lat": 19.8148, "lng": 85.8525, "seller": "SprayLine Services", "features": ["Wide spray width", "Uniform coverage", "Trained operator"]},
    {"id": "battery-spray-puri", "category": "spraying", "name": "Battery Spray Unit", "hp": "Portable", "price_per_hour": 350, "rating": 4.2, "rating_count": 54, "availability": "Available in 15 min", "lat": 19.8054, "lng": 85.8248, "seller": "QuickSpray Rural", "features": ["Budget friendly", "Small farms", "Quick turnaround"]},
    {"id": "agri-drone-puri", "category": "spraying", "name": "Agri Drone", "hp": "Smart Flight", "price_per_hour": 1500, "rating": 4.9, "rating_count": 25, "availability": "Available in 3 hr", "lat": 19.8284, "lng": 85.8388, "seller": "Drone Kisan Ops", "features": ["Precision spray", "Large coverage", "AI route planning"]},
]

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
WEATHER_API_CACHE_TTL_SECONDS = 300
WEATHER_API_CACHE = {}
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


class AlertRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    farm_id = db.Column(db.Integer, db.ForeignKey("farm.id"), index=True)
    alert_key = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(40), default="system", index=True)
    severity = db.Column(db.String(20), default="insight", index=True)
    title = db.Column(db.String(180), nullable=False)
    detail = db.Column(db.Text, nullable=False)
    action_url = db.Column(db.String(255), default="/alerts")
    is_read = db.Column(db.Boolean, default=False, index=True)
    is_active = db.Column(db.Boolean, default=True, index=True)
    email_sent = db.Column(db.Boolean, default=False)
    sms_sent = db.Column(db.Boolean, default=False)
    last_notified_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        index=True,
    )

    __table_args__ = (
        db.UniqueConstraint("user_id", "alert_key", name="uq_alert_record_user_key"),
    )

    def __init__(self, **kwargs):
        super(AlertRecord, self).__init__(**kwargs)


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


class TractorBooking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    machine_id = db.Column(db.String(120), nullable=False, index=True)
    machine_name = db.Column(db.String(180), nullable=False)
    category = db.Column(db.String(40), nullable=False, index=True)
    farm_location = db.Column(db.String(160))
    farm_lat = db.Column(db.Float)
    farm_lng = db.Column(db.Float)
    booking_date = db.Column(db.Date, nullable=False, index=True)
    slot_label = db.Column(db.String(40), nullable=False)
    duration_hours = db.Column(db.Integer, default=1)
    price_per_hour = db.Column(db.Integer, default=0)
    total_amount_inr = db.Column(db.Integer, default=0)
    payment_mode = db.Column(db.String(20), default="pay_later")
    payment_status = db.Column(db.String(20), default="pending")
    booking_status = db.Column(db.String(20), default="confirmed")
    notes = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __init__(self, **kwargs):
        super(TractorBooking, self).__init__(**kwargs)


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

            # Free plan always has access to non-premium routes.
            if plan_rank(user_plan) >= plan_rank(min_plan) and is_paid_subscription_active(user):
                return f(*args, **kwargs)

            # Trial acts like Pro access for a limited window.
            if min_plan == "pro" and is_trial_active(user):
                return f(*args, **kwargs)

            return redirect("/subscriptions?required=1")

        return decorated_function

    return decorator


def check_subscription(f):
    return require_plan("pro")(f)


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
    stored = (ADMIN_PASSWORD or "").strip()
    candidate = (candidate_password or "").strip()
    if not stored or not candidate:
        return False

    if is_password_hash(stored):
        return check_password_hash(stored, candidate)

    return stored == candidate


def is_admin_authenticated():
    return bool(session.get("admin_authed") and session.get("admin_email") == ADMIN_EMAIL)


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
        "otp_target",
        "otp_type",
        "otp_user_id",
        "otp_expiry",
        "otp_notice",
        "otp_debug_available",
        "otp_sent_at",
        "pending_user",
    ):
        session.pop(key, None)


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


def build_otp_notice(email_sent, failure_reason=None, *, whatsapp_sent=False, whatsapp_reason=None):
    if email_sent and whatsapp_sent:
        return (
            f"Verification code sent successfully on email and WhatsApp. "
            f"The code stays valid for {OTP_EXPIRY_MINUTES} minutes."
        )

    if email_sent:
        return f"Verification code sent successfully on email. The code stays valid for {OTP_EXPIRY_MINUTES} minutes."

    if whatsapp_sent:
        return (
            f"Verification code sent on WhatsApp. The code stays valid for {OTP_EXPIRY_MINUTES} minutes."
        )

    if OTP_DEBUG_FALLBACK_ENABLED:
        return (
            "Email delivery failed in this environment, so a local fallback OTP is shown below for testing."
        )

    if failure_reason or whatsapp_reason:
        return "We could not send the OTP right now. Please try again after checking the mail and WhatsApp settings."

    return "We could not send the OTP right now. Please try again."


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

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(self)",
    )
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response


def save_profile_photo_upload(file_storage, prefix):
    original_name = secure_filename(file_storage.filename or "")
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("Only PNG, JPG, JPEG, or WEBP images are allowed.")

    file_name = f"{prefix}_{str(uuid.uuid4().hex)[0:12]}{suffix}"
    save_path = UPLOADS_DIR / file_name
    file_storage.save(str(save_path))
    return file_name


def save_product_image_upload(file_storage, slug_hint="product"):
    """Save an admin-uploaded product image into /static/products and return its public URL."""
    original_name = secure_filename(file_storage.filename or "")
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("Only PNG, JPG, JPEG, or WEBP images are allowed.")

    safe_hint = str(slugify_crop_name(slug_hint or "product"))[0:28] or "product"
    file_name = f"{safe_hint}_{str(uuid.uuid4().hex)[0:12]}.jpg"
    save_path = PRODUCTS_UPLOAD_DIR / file_name

    # Normalize all uploads to a square JPEG for consistent store UI.
    try:
        img = Image.open(file_storage.stream)
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
RESEND_API_KEY = (os.getenv("RESEND_API_KEY") or "").strip()
RESEND_FROM_EMAIL = (os.getenv("RESEND_FROM_EMAIL") or "onboarding@resend.dev").strip() or "onboarding@resend.dev"
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
TWILIO_ACCOUNT_SID = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
TWILIO_AUTH_TOKEN = (os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
TWILIO_SMS_FROM = (os.getenv("TWILIO_SMS_FROM") or "").strip()
TWILIO_WHATSAPP_FROM = (os.getenv("TWILIO_WHATSAPP_FROM") or "").strip()
TWILIO_CONTENT_SID = (os.getenv("TWILIO_CONTENT_SID") or "").strip()
TWILIO_USE_WHATSAPP = (os.getenv("TWILIO_USE_WHATSAPP") or "").strip().lower() in {"1", "true", "yes", "on"}
TASK_REMINDER_INTERVAL_SECONDS = max(60, get_env_int("TASK_REMINDER_INTERVAL_SECONDS", 600))
TASK_REMINDER_POLL_SECONDS = max(30, min(TASK_REMINDER_INTERVAL_SECONDS, 60))
TASK_REMINDER_WORKER = {"started": False, "thread": None}
TASK_REMINDER_LOCK = threading.Lock()


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


def build_basic_email_html(subject, body):
    headline = str(subject or APP_DISPLAY_NAME).strip() or APP_DISPLAY_NAME
    content = str(body or "").strip()
    html_lines = "<br>".join(escape(line) for line in content.splitlines()) if content else ""
    return f"""
<!DOCTYPE html>
<html lang="en">
  <body style="margin:0;padding:24px;background:#f4f8f3;font-family:Arial,sans-serif;color:#173127;">
    <div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #dce7df;border-radius:18px;overflow:hidden;">
      <div style="padding:18px 24px;background:linear-gradient(135deg,#1f6f43,#98c15a);color:#ffffff;">
        <div style="font-size:12px;letter-spacing:0.14em;text-transform:uppercase;opacity:0.88;">{escape(APP_DISPLAY_NAME)}</div>
        <h1 style="margin:10px 0 0;font-size:24px;line-height:1.3;">{escape(headline)}</h1>
      </div>
      <div style="padding:24px;font-size:15px;line-height:1.7;color:#1f2937;">
        {html_lines}
      </div>
    </div>
  </body>
</html>
""".strip()


def send_resend_email(target_email, subject, text_content, html_content=None, *, label="email"):
    recipient = str(target_email or "").strip()
    if not recipient:
        return False, f"Recipient email is missing for {label}."
    if not RESEND_API_KEY:
        return False, "Resend API key is not configured."

    payload = {
        "from": formataddr((SMTP_SENDER_NAME, RESEND_FROM_EMAIL)),
        "to": [recipient],
        "subject": str(subject or APP_DISPLAY_NAME).strip() or APP_DISPLAY_NAME,
        "text": str(text_content or "").strip(),
        "html": str(html_content or "").strip() or build_basic_email_html(subject, text_content),
    }
    request_headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": f"{APP_DISPLAY_NAME}/1.0",
    }

    try:
        req = Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        with urlopen(req, timeout=API_TIMEOUT_SECONDS) as response:
            response_data = json.loads(response.read().decode("utf-8"))
        if isinstance(response_data, dict) and response_data.get("id"):
            return True, None
        if isinstance(response_data, dict):
            error_message = str(response_data.get("message") or response_data.get("error") or "").strip()
            return False, error_message or f"Resend API request failed for {label}."
        return False, f"Unexpected Resend response for {label}."
    except HTTPError as exc:
        error_body = ""
        try:
            raw_error = exc.read().decode("utf-8")
            error_payload = json.loads(raw_error)
            if isinstance(error_payload, dict):
                error_body = str(error_payload.get("message") or error_payload.get("error") or error_payload).strip()
            else:
                error_body = str(error_payload).strip()
        except Exception:
            error_body = str(exc)
        return False, f"HTTP {exc.code}: {error_body or exc.reason}"
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return False, str(exc)


def send_email_content(target_email, subject, body, *, html_body=None, label="email"):
    recipient = str(target_email or "").strip()
    if not recipient:
        return False, f"Recipient email is missing for {label}."

    if isinstance(body, (list, tuple)):
        text_content = "\n".join(str(line) for line in body)
    else:
        text_content = str(body or "").strip()
    html_content = str(html_body or "").strip() or build_basic_email_html(subject, text_content)

    if RESEND_API_KEY:
        resend_sent, resend_error = send_resend_email(
            recipient,
            subject,
            text_content,
            html_content,
            label=label,
        )
        if resend_sent:
            return True, None
        print(f"Resend Error ({label}): {resend_error}")

    msg = EmailMessage()
    msg["Subject"] = str(subject or APP_DISPLAY_NAME).strip() or APP_DISPLAY_NAME
    msg["From"] = formataddr((SMTP_SENDER_NAME, SMTP_EMAIL))
    msg["To"] = recipient
    msg["Reply-To"] = SMTP_EMAIL
    msg["X-Auto-Response-Suppress"] = "OOF, AutoReply"
    msg.set_content(text_content)
    msg.add_alternative(html_content, subtype="html")
    return send_smtp_message(msg, label=label)


def send_otp_email(target_email, otp):
    """
    Sends a 6-digit OTP to the user's email using Resend first, then SMTP fallback.
    """
    # Debug Print for Terminal
    print(f"\n--- [DEBUG OTP] OTP for {target_email} is: {otp} ---\n")

    subject = f"{APP_DISPLAY_NAME} verification code: {otp}"
    text_body = build_otp_email_text(otp)
    html_body = build_otp_email_html(otp)

    if RESEND_API_KEY:
        resend_sent, resend_error = send_resend_email(
            target_email,
            subject,
            text_body,
            html_body,
            label="otp email",
        )
        if resend_sent:
            return True, None
        print(f"Resend Error (otp email): {resend_error}")

    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print("SMTP credentials are not configured. OTP email skipped.")
        if RESEND_API_KEY:
            return False, "Resend delivery failed and SMTP fallback is not configured."
        return False, "SMTP credentials are not configured."

    logo_bytes = None
    logo_cid = None
    if OTP_EMAIL_EMBED_LOGO:
        logo_bytes = load_email_logo_bytes()
        if logo_bytes:
            logo_cid = str(make_msgid(domain="agrovisionai.local"))[1:-1]


    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((SMTP_SENDER_NAME, SMTP_EMAIL))
    msg["To"] = target_email
    msg["Reply-To"] = SMTP_EMAIL
    msg["X-Auto-Response-Suppress"] = "OOF, AutoReply"
    msg.set_content(text_body)
    msg.add_alternative(build_otp_email_html(otp, logo_cid=logo_cid), subtype="html")

    if logo_bytes and logo_cid:
        msg.add_related(
            logo_bytes,
            maintype="image",
            subtype=EMAIL_LOGO_SUBTYPE,
            cid=f"<{logo_cid}>",
            filename=EMAIL_LOGO_FILENAME,
        )
    return send_smtp_message(msg, label="otp email")


def send_otp_whatsapp(target_phone, otp):
    otp_code = str(otp or "").strip()
    if not otp_code:
        return False, "OTP code is missing."

    message = (
        f"{APP_DISPLAY_NAME} verification code: {otp_code}. "
        f"It is valid for {OTP_EXPIRY_MINUTES} minutes. Do not share this code with anyone."
    )
    return send_twilio_text_message(
        target_phone,
        message,
        prefer_whatsapp=True,
        label="otp whatsapp",
    )


def send_admin_order_email(order, user, product):
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

    sent, _ = send_basic_email(
        ADMIN_NOTIFY_EMAIL,
        subject,
        body_lines,
        label="admin order email",
    )
    return sent


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
        "co2_tonnes": float(int(float(total_co2 or 0) * 100) / 100.0),
        "credits": float(int(float(credits or 0) * 10) / 10.0),
        "impact_level": "Outstanding" if (credits or 0) > 10 else ("Significant" if (credits or 0) > 5 else "Good")
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


def load_ai_crop_doctor_chat_knowledge():
    global AI_CROP_DOCTOR_CHAT_KNOWLEDGE_CACHE

    if AI_CROP_DOCTOR_CHAT_KNOWLEDGE_CACHE is not None:
        return AI_CROP_DOCTOR_CHAT_KNOWLEDGE_CACHE

    default_payload = {
        "intents": [],
        "faq": [],
        "daily_questions": [],
        "judge_questions": [],
        "advanced_faq": [],
        "matching_config": {},
    }
    if not AI_CROP_DOCTOR_CHAT_KNOWLEDGE_PATH.exists():
        AI_CROP_DOCTOR_CHAT_KNOWLEDGE_CACHE = default_payload
        return AI_CROP_DOCTOR_CHAT_KNOWLEDGE_CACHE

    try:
        data = json.loads(AI_CROP_DOCTOR_CHAT_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        AI_CROP_DOCTOR_CHAT_KNOWLEDGE_CACHE = default_payload
        return AI_CROP_DOCTOR_CHAT_KNOWLEDGE_CACHE

    if not isinstance(data, dict):
        AI_CROP_DOCTOR_CHAT_KNOWLEDGE_CACHE = default_payload
        return AI_CROP_DOCTOR_CHAT_KNOWLEDGE_CACHE

    normalized_payload = dict(default_payload)
    for key in ["intents", "faq", "daily_questions", "judge_questions", "advanced_faq"]:
        value = data.get(key)
        normalized_payload[key] = value if isinstance(value, list) else []
    matching_config = data.get("matching_config")
    normalized_payload["matching_config"] = matching_config if isinstance(matching_config, dict) else {}
    AI_CROP_DOCTOR_CHAT_KNOWLEDGE_CACHE = normalized_payload
    return AI_CROP_DOCTOR_CHAT_KNOWLEDGE_CACHE


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
    "मैं", "मेरे", "मेरी", "मेरा", "मुझे", "है", "हैं", "का", "की", "के", "को", "से",
    "पर", "और", "क्या", "कैसे", "कब", "क्यों", "करो", "करें", "करना", "रहा", "रहे", "रही",
    "ongoing",
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
    "chipchipa": "sticky",
    "chipchipe": "sticky",
    "safedi": "white",
    "keedaa": "pest",
    "garmi": "heat",
    "barish": "rain",
    "baarish": "rain",
    "medicine": "spray",
    "medicines": "spray",
    "doctor": "help",
    "madad": "help",
    "mur": "curl",
    "mura": "curl",
    "murra": "curl",
    "twisted": "curl",
    "sticky": "sticky",
    "hollow": "hole",
    "holes": "hole",
    "worms": "worm",
    "keedae": "pest",
    "jadon": "root",
    "peeli": "yellow",
    "marks": "spots",
    "patches": "patch",
    "patchy": "patch",
    "पत्ता": "leaf",
    "पत्ते": "leaf",
    "पत्ती": "leaf",
    "पौधा": "plant",
    "पौधे": "plant",
    "पील": "yellow",
    "पीला": "yellow",
    "पीले": "yellow",
    "पीली": "yellow",
    "पीलीं": "yellow",
    "पीलापन": "yellow",
    "पड़": "change",
    "पड़ना": "change",
    "रहा": "ongoing",
    "रहे": "ongoing",
    "हैं": "is",
    "है": "is",
    "सफेद": "white",
    "भूरा": "brown",
    "भूरे": "brown",
    "काला": "black",
    "काले": "black",
    "धब्बा": "spots",
    "धब्बे": "spots",
    "दाग": "spots",
    "जड़": "root",
    "जड़े": "root",
    "कीड़ा": "pest",
    "कीड़े": "pest",
    "कीट": "pest",
    "चिपचिपा": "sticky",
    "सूखा": "dry",
    "सूखी": "dry",
    "सूख": "dry",
    "मुड़": "curl",
    "मुड़ना": "curl",
    "मुड़ रहे": "curl",
    "बारिश": "rain",
    "गर्मी": "heat",
    "पानी": "water",
    "दवा": "spray",
    "खाद": "fertilizer",
    "मिट्टी": "soil",
}

AI_CROP_DOCTOR_SYMPTOM_CUES = {
    "yellow", "brown", "white", "black", "leaf", "root", "spots", "powder", "curl",
    "holes", "wilt", "dry", "pest", "fungal", "fungus", "rot", "infection", "burn",
}

AI_CHAT_GREETING_TERMS = {
    "hi", "hii", "hiii", "hello", "helo", "hey", "namaste", "namaskar", "salaam",
    "morning", "afternoon", "evening",
}

AI_CHAT_GREETING_PHRASES = {
    "hi",
    "hii",
    "hiii",
    "hello",
    "helo",
    "hey",
    "namaste",
    "namaskar",
    "salaam",
    "good morning",
    "good afternoon",
    "good evening",
}

AI_CHAT_FOLLOWUP_TERMS = {
    "ye", "yeh", "is", "isko", "iska", "iske", "iss", "isme",
    "wo", "woh", "us", "usko", "uska", "uske", "usme",
    "kya", "kaise", "kab", "kitna", "kyu", "kyon", "kyun",
    "karu", "kru", "karna", "ab", "abhi", "fir", "phir",
    "detail", "details", "next", "then",
}

AI_CROP_DOCTOR_DATASET_HINT_TOKENS = {
    "aphid", "bacterial", "blight", "curl", "fungal", "fungus", "infect", "insect",
    "leaf", "lesion", "mildew", "mite", "mold", "mosaic", "pest", "powder", "rot",
    "rust", "spot", "thrip", "virus", "whitefly", "worm",
}

AI_CROP_DOCTOR_LOW_SIGNAL_TOKENS = {
    "agriculture", "farming", "farm", "crop", "plant", "technology", "system",
}

AI_CROP_DOCTOR_FUZZY_TOKEN_THRESHOLD = 0.82
AI_CROP_DOCTOR_FUZZY_PHRASE_THRESHOLD = 0.86


AI_CROP_DOCTOR_CHAT_INTENT_ALIAS_MAP = {
    "fallback_help": "general_help",
    "multi_analysis": "general_help",
    "multi_issue_analysis": "general_help",
    "caterpillar": "fall_armyworm",
    "avoid_spray": "rain_warning",
    "heat_protection": "heatwave_issue",
    "irrigation_timing": "irrigation_timing",
    "ai_explanation": "ai_explanation",
    "accuracy_info": "accuracy_info",
    "fallback_response": "fallback_response",
    "product_recommendation": "best_fungicide",
    "organic_solution": "organic_treatment",
    "comparison_answer": "organic_vs_chemical",
    "last_solution": "last_solution",
    "project_uniqueness": "project_uniqueness",
    "impact_answer": "impact_answer",
    "scalability_answer": "scalability_answer",
    "fungal_solution": "fungal_solution",
    "virus_no_cure": "virus_no_cure",
}


AI_CROP_DOCTOR_LOCAL_QA_PHRASE_ALIASES = {
    "fertilizer kiya hai": "fertilizer kya hai",
    "fertiliser kiya hai": "fertilizer kya hai",
    "irrigasion kiya hai": "irrigation kya hai",
    "irrigation kiya hai": "irrigation kya hai",
    "पीले पड़ रहे": "yellow leaf",
    "पीला पड़ रहा": "yellow leaf",
    "पीली पड़ रही": "yellow leaf",
    "पीले हो रहे": "yellow leaf",
    "पीला हो रहा": "yellow leaf",
    "सफेद पाउडर": "white powder",
    "भूरे धब्बे": "brown spots",
    "काले धब्बे": "black spots",
    "जड़ काली": "root black",
    "जड़ें काली": "root black",
    "चिपचिपे पत्ते": "sticky leaf",
    "चिपचिपा पत्ता": "sticky leaf",
    "धीमी बढ़त": "slow growth",
    "धीमी ग्रोथ": "slow growth",
}


def normalize_ai_crop_doctor_match_text(text):
    normalized_source = str(text or "").lower()
    for source_phrase, target_phrase in AI_CROP_DOCTOR_LOCAL_QA_PHRASE_ALIASES.items():
        normalized_source = normalized_source.replace(source_phrase, f" {target_phrase} ")

    tokens = []
    for raw_token in re.findall(r"[a-z0-9\u0900-\u097f]+", normalized_source):
        token = AI_CROP_DOCTOR_LOCAL_QA_TOKEN_ALIASES.get(raw_token, raw_token)
        if token and token.endswith("s") and len(token) > 4 and token not in {"ph", "tips"}:
            token = str(token)[:-1]
        tokens.append(token)
    return " ".join(tokens).strip()


def extract_ai_crop_doctor_match_tokens(text):
    normalized_text = normalize_ai_crop_doctor_match_text(text)
    return {
        token
        for token in normalized_text.split()
        if token and token not in AI_CROP_DOCTOR_LOCAL_QA_STOPWORDS
    }


def build_ai_crop_doctor_token_signature(text):
    return " ".join(sorted(extract_ai_crop_doctor_match_tokens(text)))


def compute_ai_crop_doctor_fuzzy_similarity(left, right):
    normalized_left = normalize_ai_crop_doctor_match_text(left)
    normalized_right = normalize_ai_crop_doctor_match_text(right)
    if not normalized_left or not normalized_right:
        return 0.0

    compact_left = normalized_left.replace(" ", "")
    compact_right = normalized_right.replace(" ", "")
    token_signature_left = build_ai_crop_doctor_token_signature(normalized_left)
    token_signature_right = build_ai_crop_doctor_token_signature(normalized_right)

    similarity_scores = [
        SequenceMatcher(None, normalized_left, normalized_right).ratio(),
        SequenceMatcher(None, compact_left, compact_right).ratio(),
    ]
    if token_signature_left and token_signature_right:
        similarity_scores.append(SequenceMatcher(None, token_signature_left, token_signature_right).ratio())

    shorter_text = normalized_left if len(normalized_left) <= len(normalized_right) else normalized_right
    longer_text = normalized_right if shorter_text == normalized_left else normalized_left
    if len(shorter_text) >= 5 and shorter_text in longer_text:
        similarity_scores.append(min(1.0, 0.9 + (len(shorter_text) / max(len(longer_text), 1)) * 0.08))

    return max(similarity_scores)


def count_ai_crop_doctor_fuzzy_token_matches(query_tokens, candidate_tokens, threshold=AI_CROP_DOCTOR_FUZZY_TOKEN_THRESHOLD):
    normalized_query_tokens = [str(token or "").strip() for token in (query_tokens or []) if str(token or "").strip()]
    normalized_candidate_tokens = [str(token or "").strip() for token in (candidate_tokens or []) if str(token or "").strip()]
    if not normalized_query_tokens or not normalized_candidate_tokens:
        return 0

    match_count = 0
    used_candidates = set()
    for query_token in normalized_query_tokens:
        if len(query_token) < 5:
            continue
        best_index = None
        best_score = 0.0
        for index, candidate_token in enumerate(normalized_candidate_tokens):
            if index in used_candidates or len(candidate_token) < 5:
                continue
            if abs(len(query_token) - len(candidate_token)) > 4:
                continue
            similarity_score = SequenceMatcher(None, query_token, candidate_token).ratio()
            if similarity_score > best_score:
                best_score = similarity_score
                best_index = index
        if best_index is not None and best_score >= threshold:
            used_candidates.add(best_index)
            match_count += 1
    return match_count


def get_ai_crop_doctor_best_fuzzy_score(query_text, candidates):
    normalized_query = normalize_ai_crop_doctor_match_text(query_text)
    if len(normalized_query.replace(" ", "")) < 5:
        return 0.0

    best_score = 0.0
    for candidate in candidates or []:
        normalized_candidate = normalize_ai_crop_doctor_match_text(candidate)
        if len(normalized_candidate.replace(" ", "")) < 4:
            continue
        best_score = max(best_score, compute_ai_crop_doctor_fuzzy_similarity(normalized_query, normalized_candidate))
    return best_score


def format_ai_crop_doctor_products(products):
    labels = []
    if not isinstance(products, list):
        return labels
    for item in products:
        raw_value = ""
        if isinstance(item, dict):
            raw_value = str(item.get("id") or item.get("product") or item.get("name") or "").strip()
        else:
            raw_value = str(item or "").strip()
        if not raw_value:
            continue
        labels.append(raw_value.replace("_", " ").replace("-", " ").title())
    return labels


def join_ai_crop_doctor_list(values, limit=3):
    cleaned_values = [str(value or "").strip() for value in values if str(value or "").strip()]
    if not cleaned_values:
        return ""
    return ", ".join(list(cleaned_values)[:limit])


def format_ai_crop_doctor_structured_answer(answer, language="Hinglish", follow_up=None):
    normalized_language = str(language or "Hinglish").strip().lower()
    follow_up = [str(item or "").strip() for item in (follow_up or []) if str(item or "").strip()]

    if isinstance(answer, str):
        message = answer.strip()
    elif isinstance(answer, list):
        message = str(answer[0] or "").strip() if answer else ""
    elif isinstance(answer, dict):
        if str(answer.get("message") or "").strip():
            message = str(answer.get("message") or "").strip()
        else:
            parts = []
            subject = (
                str(answer.get("disease") or "").strip()
                or str(answer.get("problem") or "").strip()
                or str(answer.get("definition") or "").strip()
            )
            confidence_hint = str(answer.get("confidence_hint") or "").strip()
            cause = str(answer.get("cause") or "").strip()
            note = str(answer.get("note") or "").strip()
            warning = str(answer.get("warning") or "").strip()
            tip = str(answer.get("tip") or "").strip()
            benefit = str(answer.get("benefit") or "").strip()
            recommendation = str(answer.get("recommendation") or "").strip()
            example = str(answer.get("example") or "").strip()

            symptoms = answer.get("symptoms") if isinstance(answer.get("symptoms"), list) else []
            prevention = answer.get("prevention") if isinstance(answer.get("prevention"), list) else []
            solution = answer.get("solution")
            comparison = answer.get("comparison") if isinstance(answer.get("comparison"), dict) else {}
            impact = answer.get("impact") if isinstance(answer.get("impact"), list) else []
            products = format_ai_crop_doctor_products(answer.get("products"))

            if subject:
                if normalized_language == "english":
                    subject_line = f"It looks related to {subject}."
                    if confidence_hint:
                        subject_line += f" Confidence hint: {confidence_hint}."
                    parts.append(subject_line.strip())
                else:
                    subject_line = f"Yeh {subject} se related lag raha hai."
                    if confidence_hint:
                        subject_line += f" Confidence hint {confidence_hint}."
                    parts.append(subject_line.strip())
            if symptoms:
                label = "Symptoms" if normalized_language == "english" else "Symptoms"
                parts.append(f"{label}: {join_ai_crop_doctor_list(symptoms, limit=3)}.")
            if cause:
                label = "Cause" if normalized_language == "english" else "Cause"
                parts.append(f"{label}: {cause}.")
            if isinstance(solution, dict):
                organic = solution.get("organic") if isinstance(solution.get("organic"), list) else []
                chemical = solution.get("chemical") if isinstance(solution.get("chemical"), list) else []
                if organic:
                    label = "Organic" if normalized_language == "english" else "Organic"
                    parts.append(f"{label}: {join_ai_crop_doctor_list(organic, limit=2)}.")
                if chemical:
                    label = "Chemical" if normalized_language == "english" else "Chemical"
                    parts.append(f"{label}: {join_ai_crop_doctor_list(chemical, limit=2)}.")
            elif isinstance(solution, list):
                label = "Action" if normalized_language == "english" else "Action"
                parts.append(f"{label}: {join_ai_crop_doctor_list(solution, limit=3)}.")
            elif str(solution or "").strip():
                label = "Action" if normalized_language == "english" else "Action"
                parts.append(f"{label}: {str(solution).strip()}.")
            if prevention:
                label = "Prevention" if normalized_language == "english" else "Prevention"
                parts.append(f"{label}: {join_ai_crop_doctor_list(prevention, limit=2)}.")
            if comparison:
                organic_line = str(comparison.get("organic") or "").strip()
                chemical_line = str(comparison.get("chemical") or "").strip()
                if organic_line or chemical_line:
                    parts.append(
                        "Organic: "
                        + (organic_line or "N/A")
                        + " | Chemical: "
                        + (chemical_line or "N/A")
                        + "."
                    )
            if products:
                label = "Suggested products" if normalized_language == "english" else "Suggested products"
                parts.append(f"{label}: {join_ai_crop_doctor_list(products, limit=3)}.")
            if recommendation:
                parts.append(f"{recommendation}.")
            if benefit:
                parts.append(f"{benefit}.")
            if note:
                parts.append(f"{note}.")
            if warning:
                parts.append(f"Warning: {warning}.")
            if tip:
                parts.append(f"Tip: {tip}.")
            if impact:
                parts.append(f"Impact: {join_ai_crop_doctor_list(impact, limit=3)}.")
            if example:
                parts.append(f"Example: {example}.")
            message = " ".join(part.strip() for part in parts if str(part or "").strip())
    else:
        message = ""

    message = re.sub(r"\s+", " ", str(message or "").strip())
    if not message:
        return None
    if follow_up:
        message = f"{message} Confirm karne ke liye batayein: {join_ai_crop_doctor_list(follow_up, limit=2)}?"
    return message


def load_ai_crop_doctor_chat_match_entries():
    global AI_CROP_DOCTOR_CHAT_MATCH_ENTRIES_CACHE

    if AI_CROP_DOCTOR_CHAT_MATCH_ENTRIES_CACHE is not None:
        return AI_CROP_DOCTOR_CHAT_MATCH_ENTRIES_CACHE

    knowledge = load_ai_crop_doctor_chat_knowledge()
    entries = []
    intent_index = {}

    def build_entry(source, tag, patterns, keywords, answer, category="", follow_up=None, confidence_threshold=0.5):
        entry = {
            "source": source,
            "tag": str(tag or "").strip().lower(),
            "category": str(category or "").strip().lower(),
            "patterns": [str(item or "").strip() for item in (patterns or []) if str(item or "").strip()],
            "keywords": [str(item or "").strip() for item in (keywords or []) if str(item or "").strip()],
            "answer": answer,
            "follow_up": [str(item or "").strip() for item in (follow_up or []) if str(item or "").strip()],
            "confidence_threshold": float(confidence_threshold or 0.5),
        }
        if entry["tag"]:
            intent_index.setdefault(entry["tag"], entry)
        entries.append(entry)

    for intent in knowledge.get("intents", []):
        if not isinstance(intent, dict):
            continue
        responses = intent.get("responses")
        answer = responses if isinstance(responses, (str, list, dict)) else None
        build_entry(
            "intent",
            intent.get("tag"),
            intent.get("patterns", []),
            intent.get("keywords", []),
            answer,
            category="intent",
            follow_up=intent.get("follow_up", []),
            confidence_threshold=float(intent.get("confidence_threshold") or 0.5),
        )

    for item in knowledge.get("faq", []):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        patterns = list(item.get("patterns", [])) if isinstance(item.get("patterns"), list) else []
        if question:
            patterns.insert(0, question)
        build_entry(
            "faq",
            item.get("tag") or question,
            patterns,
            item.get("keywords", []),
            item.get("answer"),
            category="faq",
            confidence_threshold=0.5,
        )

    for item in knowledge.get("daily_questions", []):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not question or not answer:
            continue
        build_entry(
            "daily_question",
            item.get("tag") or question,
            [question],
            item.get("keywords", []),
            answer,
            category="daily_question",
            confidence_threshold=0.5,
        )

    for item in knowledge.get("advanced_faq", []):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        patterns = list(item.get("patterns", [])) if isinstance(item.get("patterns"), list) else []
        if question:
            patterns.insert(0, question)
        build_entry(
            "advanced_faq",
            item.get("tag") or question,
            patterns,
            item.get("keywords", []),
            item.get("answer"),
            category="advanced_faq",
            confidence_threshold=0.5,
        )

    for item in knowledge.get("judge_questions", []):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        expected_intent = str(item.get("expected_intent") or "").strip().lower()
        resolved_tag = AI_CROP_DOCTOR_CHAT_INTENT_ALIAS_MAP.get(expected_intent, expected_intent)
        target_entry = None
        if isinstance(intent_index, dict):
            target_entry = intent_index.get(resolved_tag)
        if target_entry is not None:
            patterns = target_entry.get("patterns", [])
            if not isinstance(patterns, list):
                patterns = []
                target_entry["patterns"] = patterns
            if question not in patterns:
                patterns.append(question)

    AI_CROP_DOCTOR_CHAT_MATCH_ENTRIES_CACHE = entries
    return AI_CROP_DOCTOR_CHAT_MATCH_ENTRIES_CACHE


def build_ai_chat_greeting_reply(language="Hinglish"):
    normalized_language = str(language or "Hinglish").strip().lower()
    if normalized_language == "english":
        return "Hello! I am AI Crop Doctor. Tell me your crop, symptom, weather, or spray question and I will help."
    return "Namaste! Main AI Crop Doctor hoon. Aap crop, symptom, weather, ya spray se related sawal pooch sakte ho."


def build_ai_chat_uncertain_query_reply(language="Hinglish"):
    normalized_language = str(language or "Hinglish").strip().lower()
    if normalized_language == "english":
        return "Please ask in a little more detail, like crop name, symptom, weather issue, or what you want to know about seeds, fertilizer, or disease."
    return "Thoda aur clear batayein, jaise crop ka naam, symptom, weather issue, ya aap beej, khad, bimari me se kis cheez ke baare me pooch rahe ho."


def lookup_ai_crop_doctor_disease_dataset_answer(query_text, language="Hinglish"):
    query_text = str(query_text or "").strip()
    if not query_text:
        return None

    normalized_query = normalize_ai_crop_doctor_match_text(query_text)
    query_tokens = extract_ai_crop_doctor_match_tokens(query_text)
    strong_query_tokens = query_tokens - AI_CROP_DOCTOR_LOW_SIGNAL_TOKENS
    if not strong_query_tokens:
        return None

    if not (
        strong_query_tokens & AI_CROP_DOCTOR_DATASET_HINT_TOKENS
        or strong_query_tokens & AI_CROP_DOCTOR_SYMPTOM_CUES
    ):
        return None

    best_entry = None
    best_score = 0.0
    best_overlap = 0
    best_name_hit = False
    best_fuzzy_score = 0.0

    for entry in load_disease_dataset().values():
        disease_name = str(entry.get("name") or "").strip()
        disease_name_normalized = normalize_ai_crop_doctor_match_text(disease_name)
        disease_name_tokens = extract_ai_crop_doctor_match_tokens(disease_name)

        signature_parts = [disease_name]
        signature_parts.extend(entry.get("symptoms", []))
        signature_parts.extend((entry.get("solution") or {}).get("organic", []))
        signature_parts.extend((entry.get("solution") or {}).get("chemical", []))
        signature_parts.extend(entry.get("prevention", []))

        etiology = entry.get("etiology") or {}
        signature_parts.extend(
            [
                str(etiology.get("pathogen") or "").strip(),
                str(etiology.get("environment") or "").strip(),
                str(etiology.get("transmission") or "").strip(),
            ]
        )

        signature_tokens = extract_ai_crop_doctor_match_tokens(" ".join(signature_parts))
        overlap = len(strong_query_tokens & signature_tokens)
        disease_overlap = len(strong_query_tokens & disease_name_tokens)
        fuzzy_overlap = count_ai_crop_doctor_fuzzy_token_matches(strong_query_tokens, signature_tokens)
        name_hit = bool(
            disease_name_normalized
            and (
                disease_name_normalized in normalized_query
                or normalized_query in disease_name_normalized
            )
        )
        fuzzy_score = get_ai_crop_doctor_best_fuzzy_score(
            normalized_query,
            [
                disease_name,
                " ".join(signature_parts[:6]),
                *entry.get("symptoms", [])[:4],
                *entry.get("prevention", [])[:2],
            ],
        )

        if not overlap and not disease_overlap and not name_hit and not fuzzy_overlap and fuzzy_score < AI_CROP_DOCTOR_FUZZY_PHRASE_THRESHOLD:
            continue

        score = overlap * 2.0 + disease_overlap * 4.0
        if name_hit:
            score += 8.0
        if fuzzy_overlap:
            score += float(fuzzy_overlap) * 2.5
        if fuzzy_score >= AI_CROP_DOCTOR_FUZZY_PHRASE_THRESHOLD:
            score += fuzzy_score * 8.0

        for symptom in entry.get("symptoms", [])[:4]:
            symptom_normalized = normalize_ai_crop_doctor_match_text(symptom)
            if symptom_normalized and symptom_normalized in normalized_query:
                score += 3.0

        if score > best_score:
            best_entry = entry
            best_score = score
            best_overlap = overlap
            best_name_hit = name_hit
            best_fuzzy_score = fuzzy_score

    if best_entry is None:
        return None

    if not best_name_hit and best_fuzzy_score < AI_CROP_DOCTOR_FUZZY_PHRASE_THRESHOLD and (best_overlap < 3 or best_score < 7.0):
        return None

    if not isinstance(best_entry, dict):
        return None

    pathogen = str((best_entry.get("etiology") or {}).get("pathogen") or "").strip()
    cause_text = pathogen
    if pathogen and not any(token in pathogen.lower() for token in ("fung", "bacteria", "viral", "virus")):
        cause_text = f"Fungal infection caused by {pathogen}"

    answer = {
        "disease": best_entry.get("name"),
        "confidence_hint": best_entry.get("confidence"),
        "symptoms": list(best_entry.get("symptoms", [])),
        "cause": cause_text,
        "solution": best_entry.get("solution", {}),
        "prevention": list(best_entry.get("prevention", [])),
        "products": list(best_entry.get("products", [])),
        "recommendation": "Image ya close symptom detail se isko aur confirm kiya ja sakta hai.",
    }
    return format_ai_crop_doctor_structured_answer(answer, language=language)


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


def lookup_ai_crop_doctor_chat_knowledge(query_text):
    query_text = str(query_text or "").strip()
    if not query_text:
        return None

    query_lower = query_text.lower()
    normalized_query = normalize_ai_crop_doctor_match_text(query_text)
    query_tokens = extract_ai_crop_doctor_match_tokens(query_text)
    if not query_tokens:
        return None

    language = detect_ai_chat_language(query_text)
    if is_ai_chat_greeting_query(query_text):
        return build_ai_chat_greeting_reply(language=language)

    best_entry = None
    best_score = 0.0
    best_strong_overlap = 0
    best_phrase_match = False
    best_threshold = 7.0

    for entry in load_ai_crop_doctor_chat_match_entries():
        signature_tokens = set()
        score = 0.0
        phrase_match = False
        pattern_phrase_match = False
        keyword_hits = 0
        normalized_tag = normalize_ai_crop_doctor_match_text(entry.get("tag"))

        if normalized_tag:
            if normalized_query == normalized_tag or normalized_query.replace(" ", "") == normalized_tag.replace(" ", ""):
                score += 16
                phrase_match = True
                pattern_phrase_match = True
                keyword_hits += 1
            elif (normalized_tag and normalized_query and normalized_tag in normalized_query) or (normalized_query and normalized_tag and normalized_query in normalized_tag):
                score += 6
                phrase_match = True
                pattern_phrase_match = True
            else:
                tag_fuzzy_score = compute_ai_crop_doctor_fuzzy_similarity(normalized_query, normalized_tag)
                if tag_fuzzy_score >= 0.9:
                    score += tag_fuzzy_score * 10.0
                    phrase_match = True
                    pattern_phrase_match = True
                    keyword_hits += 1

        for pattern in entry.get("patterns", []):
            normalized_pattern = normalize_ai_crop_doctor_match_text(pattern)
            pattern_tokens = extract_ai_crop_doctor_match_tokens(pattern)
            signature_tokens.update(pattern_tokens)
            if not normalized_pattern:
                continue
            if normalized_query == normalized_pattern:
                score += 14
                phrase_match = True
                pattern_phrase_match = True
            elif normalized_pattern in normalized_query or normalized_query in normalized_pattern:
                score += 7
                phrase_match = True
                pattern_phrase_match = True
            else:
                pattern_fuzzy_score = compute_ai_crop_doctor_fuzzy_similarity(normalized_query, normalized_pattern)
                if pattern_fuzzy_score >= AI_CROP_DOCTOR_FUZZY_PHRASE_THRESHOLD:
                    score += pattern_fuzzy_score * 8.0
                    phrase_match = True
                    pattern_phrase_match = True
            overlap = len(set(query_tokens) & set(pattern_tokens))
            if overlap:
                score += float(min(5.0, float(overlap) * 1.5))
            fuzzy_overlap = count_ai_crop_doctor_fuzzy_token_matches(query_tokens, pattern_tokens)
            if fuzzy_overlap:
                score += float(min(4.0, float(fuzzy_overlap) * 1.5))

        for keyword in entry.get("keywords", []):
            normalized_keyword = normalize_ai_crop_doctor_match_text(keyword)
            keyword_tokens = extract_ai_crop_doctor_match_tokens(keyword)
            signature_tokens.update(keyword_tokens)
            if not normalized_keyword:
                continue
            if normalized_keyword in normalized_query or str(keyword).strip().lower() in query_lower:
                score += 5
                phrase_match = True
                keyword_hits = int(keyword_hits) + 1
            else:
                keyword_fuzzy_score = compute_ai_crop_doctor_fuzzy_similarity(normalized_query, normalized_keyword)
                if keyword_fuzzy_score >= 0.9:
                    score += keyword_fuzzy_score * 6.0
                    phrase_match = True
                    keyword_hits = int(keyword_hits) + 1
            overlap = len(set(query_tokens) & set(keyword_tokens))
            if overlap:
                score += float(min(4.0, float(overlap) * 2.0))
            fuzzy_overlap = count_ai_crop_doctor_fuzzy_token_matches(query_tokens, keyword_tokens)
            if fuzzy_overlap:
                score += float(min(3.0, float(fuzzy_overlap) * 1.5))

        tag_text = str(entry.get("tag") or "").replace("_", " ").strip().lower()
        tag_tokens = extract_ai_crop_doctor_match_tokens(tag_text)
        signature_tokens.update(tag_tokens)

        strong_query_tokens = set(query_tokens) - set(AI_CROP_DOCTOR_LOW_SIGNAL_TOKENS)
        strong_signature_tokens = set(signature_tokens) - set(AI_CROP_DOCTOR_LOW_SIGNAL_TOKENS)
        strong_overlap = len(strong_query_tokens & strong_signature_tokens)
        fuzzy_strong_overlap = count_ai_crop_doctor_fuzzy_token_matches(strong_query_tokens, strong_signature_tokens)
        if not phrase_match and strong_overlap == 0 and fuzzy_strong_overlap == 0:
            continue
        if entry.get("category") == "advanced_faq" and not pattern_phrase_match and strong_overlap < 2:
            continue

        score += strong_overlap * 3
        if fuzzy_strong_overlap:
            score += float(fuzzy_strong_overlap) * 2.0
        if strong_query_tokens:
            score += float(int((strong_overlap / max(len(strong_query_tokens), 1)) * 400) / 100.0)
        if tag_text and any(token in tag_text for token in strong_query_tokens):
            score += 1
        if entry.get("category") and str(entry.get("category")) in query_lower:
            score += 1

        threshold = max(6.0, 5.0 + float(entry.get("confidence_threshold") or 0.5) * 5.0)
        if keyword_hits and strong_overlap:
            threshold -= 0.5

        if score > best_score:
            best_score = score
            best_entry = entry
            best_strong_overlap = strong_overlap
            best_phrase_match = phrase_match
            best_threshold = threshold

    if best_entry and best_score >= best_threshold and (best_phrase_match or best_strong_overlap >= 2):
        add_follow_up = best_score < best_threshold + 2 and best_entry.get("follow_up")
        return format_ai_crop_doctor_structured_answer(
            best_entry.get("answer"),
            language=language,
            follow_up=best_entry.get("follow_up") if add_follow_up else None,
        )

    return None


def lookup_ai_crop_doctor_local_qa(query_text):
    query_text = str(query_text or "").strip()
    query_lower = query_text.lower()
    normalized_query = normalize_ai_crop_doctor_match_text(query_text)
    query_tokens = extract_ai_crop_doctor_match_tokens(query_text)
    language = detect_ai_chat_language(query_text)
    symptom_rules = load_disease_symptom_rules()

    # Handle common symptom-only queries even when tokenization or encoding varies across environments.
    direct_symptom_patterns = [
        ("root rot", [{"root", "black"}, {"root", "wilt"}]),
        ("white powder", [{"white", "powder"}]),
        ("brown spots", [{"brown", "spots"}]),
        ("yellow", [{"yellow", "leaf"}, {"yellow"}]),
    ]
    normalized_tokens = {str(token or "").strip().lower() for token in query_tokens if str(token or "").strip()}
    for rule_key, token_groups in direct_symptom_patterns:
        rule = symptom_rules.get(rule_key)
        if not isinstance(rule, dict):
            continue
        if rule_key in normalized_query or rule_key in query_lower:
            formatted_answer = format_ai_crop_doctor_symptom_rule_answer(rule, language=language)
            if formatted_answer:
                return formatted_answer
        for token_group in token_groups:
            if set(token_group).issubset(normalized_tokens):
                formatted_answer = format_ai_crop_doctor_symptom_rule_answer(rule, language=language)
                if formatted_answer:
                    return formatted_answer

    if not query_tokens:
        return None

    if is_ai_chat_greeting_query(query_text):
        return build_ai_chat_greeting_reply(language=language)

    if query_tokens & AI_CROP_DOCTOR_SYMPTOM_CUES:
        dataset_answer = lookup_ai_crop_doctor_disease_dataset_answer(query_text, language=language)
        if dataset_answer:
            return dataset_answer
        symptom_rule = match_disease_symptom_rule(normalized_query, query_text)
        formatted_answer = format_ai_crop_doctor_symptom_rule_answer(symptom_rule, language=language)
        if formatted_answer:
            return formatted_answer

    structured_answer = lookup_ai_crop_doctor_chat_knowledge(query_text)
    if structured_answer:
        return structured_answer

    best_entry = None
    best_score = 0.0
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
        overlap = len(set(query_tokens) & set(question_tokens))
        score = 0.0
        phrase_match = False
        if normalized_query == normalized_question:
            score += 12.0
            phrase_match = True
        elif normalized_question and (normalized_question in normalized_query or normalized_query in normalized_question):
            score += 6.0
            phrase_match = True
        else:
            question_fuzzy_score = compute_ai_crop_doctor_fuzzy_similarity(normalized_query, normalized_question)
            if question_fuzzy_score >= AI_CROP_DOCTOR_FUZZY_PHRASE_THRESHOLD:
                score += question_fuzzy_score * 8.0
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
                    score = float(score) + 5.0
                    phrase_match = True
                else:
                    keyword_fuzzy_score = compute_ai_crop_doctor_fuzzy_similarity(normalized_query, normalized_keyword)
                    if keyword_fuzzy_score >= 0.9:
                        score += keyword_fuzzy_score * 5.0
                        phrase_match = True
                current_keyword_tokens = extract_ai_crop_doctor_match_tokens(keyword_text)
                keyword_tokens.update(current_keyword_tokens)
                if current_keyword_tokens:
                    score += float(min(4.0, float(len(set(query_tokens) & set(current_keyword_tokens))) * 2.0))
                    fuzzy_overlap = count_ai_crop_doctor_fuzzy_token_matches(query_tokens, current_keyword_tokens)
                    if fuzzy_overlap:
                        score += float(min(3.0, float(fuzzy_overlap) * 1.5))

        category = str(entry.get("category") or "").strip().lower()
        if category and category in query_lower:
            score += 2.0

        signature_tokens = set(question_tokens) | keyword_tokens
        strong_query_tokens = set(query_tokens) - set(AI_CROP_DOCTOR_LOW_SIGNAL_TOKENS)
        strong_signature_tokens = set(signature_tokens) - set(AI_CROP_DOCTOR_LOW_SIGNAL_TOKENS)
        strong_overlap = len(strong_query_tokens & strong_signature_tokens)
        fuzzy_strong_overlap = count_ai_crop_doctor_fuzzy_token_matches(strong_query_tokens, strong_signature_tokens)
        if not phrase_match and strong_overlap == 0 and fuzzy_strong_overlap == 0:
            continue
        score += float(overlap)
        if strong_overlap:
            score += float(strong_overlap) * 3.0
            score += float(int((strong_overlap / max(len(strong_query_tokens), 1)) * 400) / 100.0)
        if fuzzy_strong_overlap:
            score += float(fuzzy_strong_overlap) * 2.0

        if score > best_score:
            best_entry = entry
            best_score = score
            best_strong_overlap = strong_overlap
            best_phrase_match = phrase_match

    if best_entry and best_score >= 7 and (best_phrase_match or best_strong_overlap >= 2):
        return get_ai_crop_doctor_local_answer(best_entry, language=language)

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
        "mancozeb": "Mancozeb 75% WP",
        "copper_oxychloride": "Copper Oxychloride 50% WP",
        "copper-oxychloride": "Copper Oxychloride 50% WP",
        "propiconazole": "Propiconazole 25 EC",
        "chlorothalonil": "Chlorothalonil 75 WP",
        "metalaxyl": "Ridomil Gold 68 WG",
        "ridomil": "Ridomil Gold 68 WG",
        "sulfur": "Sulfur 80 WDG",
        "imidacloprid": "Imidacloprid 17.8 SL",
        "azoxystrobin": "Azoxystrobin 23 SC",
        "tricyclazole": "Tricyclazole 75 WP",
        "leaf_mold": "Chlorothalonil 75 WP",
        "leaf-mold": "Chlorothalonil 75 WP",
        "target_spot": "Azoxystrobin 23 SC",
        "target-spot": "Azoxystrobin 23 SC",
        "leaf_rust": "Propiconazole 25 EC",
        "leaf-rust": "Propiconazole 25 EC",
        "yellow_rust": "Propiconazole 25 EC",
        "yellow-rust": "Propiconazole 25 EC",
        "common_rust": "Propiconazole 25 EC",
        "common-rust": "Propiconazole 25 EC",
        "copper": "Copper Oxychloride 50% WP",
    }
    mapped_name = alias_map.get(hint)
    if mapped_name:
        product = find_store_product_by_name(mapped_name)
        if product is not None:
            return product

    for product in get_all_store_products():
        haystack = " ".join(
            filter(
                None,
                [
                    str(getattr(product, "name", "") or ""),
                    str(getattr(product, "description", "") or ""),
                    " ".join(getattr(product, "tags", []) or []),
                ]
            )
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
    best_score = 0.0
    for raw_key, raw_rule in load_disease_symptom_rules().items():
        key = str(raw_key or "").strip().lower()
        if not key or not isinstance(raw_rule, dict):
            continue
        score = 0.0
        if key and str(key) in str(diagnostic_text):
            score = float(score) + 5.0
        key_tokens = set(re.findall(r"[a-z0-9]+", key))
        text_tokens = set(re.findall(r"[a-z0-9]+", diagnostic_text))
        score += float(len(key_tokens & text_tokens))
        if score > best_score:
            best_score = score
            best_key = key
            best_rule = raw_rule

    if best_rule is None or best_score < 2:
        return None
    return {"key": str(best_key), **dict(best_rule)}




def slugify_crop_name(name):
    parts = re.findall(r"[a-z0-9]+", (name or "").lower())
    return "-".join(parts) or "crop"


def normalize_disease_key(disease_name):
    value = str(disease_name or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value


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
    return [item[2] for item in list(related_candidates)[0:limit]]


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
        str(disease_name or "").strip(),
        slug,
        disease_slug,
        slug.replace("generic-", ""),
    ]

    seen_candidates = set()
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        if not candidate or candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        for suffix in (".jpg", ".jpeg", ".png", ".webp"):
            file_path = image_dir / f"{candidate}{suffix}"
            if file_path.exists():
                return f"/static/library/diseases/{candidate}{suffix}"

    normalized_candidates = []
    for candidate in candidates:
        normalized_candidate = "-".join(re.findall(r"[a-z0-9]+", str(candidate or "").lower()))
        if normalized_candidate and normalized_candidate not in normalized_candidates:
            normalized_candidates.append(normalized_candidate)

    try:
        for file_path in image_dir.iterdir():
            if not file_path.is_file() or file_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            normalized_stem = "-".join(re.findall(r"[a-z0-9]+", file_path.stem.lower()))
            if normalized_stem in normalized_candidates:
                return f"/static/library/diseases/{file_path.name}"
    except OSError:
        pass

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


def get_library_tips_crop_options():
    crop_labels = sorted({item["name"] for item in load_crop_library() if str(item.get("name") or "").strip()})
    return ["All"] + crop_labels


def build_library_stage_sections(items):
    grouped = {}
    for item in items:
        grouped.setdefault(item["type"], []).append(item)
    sections = []
    for label in ("Fungus", "Bacteria", "Virus", "Insect"):
        raw_items = grouped.get(label, [])
        if isinstance(raw_items, list) and raw_items:
            sections.append({"label": str(label), "items": [raw_items[i] for i in range(min(len(raw_items), 8))]})
    if not sections:
        raw_list = items if isinstance(items, list) else []
        sections.append({"label": "Popular Guides", "items": [raw_list[i] for i in range(min(len(raw_list), 8))]})
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
        "covered_crop_count": len({str(item.get("crops", ["none"])[0]) for item in disease_items if isinstance(item.get("crops"), list) and item.get("crops")}),
        "fungus_count": int(type_counts.get("Fungus", 0)),
        "bacteria_count": int(type_counts.get("Bacteria", 0)),
        "virus_count": int(type_counts.get("Virus", 0)),
        "insect_count": int(type_counts.get("Insect", 0)),
    }


def load_cultivation_tips_dataset():
    global CULTIVATION_TIPS_CACHE

    if CULTIVATION_TIPS_CACHE is not None:
        return CULTIVATION_TIPS_CACHE

    if not CULTIVATION_TIPS_DATA_PATH.exists():
        CULTIVATION_TIPS_CACHE = {"tasks": [], "stages": []}
        return CULTIVATION_TIPS_CACHE

    try:
        raw_data = json.loads(CULTIVATION_TIPS_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        CULTIVATION_TIPS_CACHE = {"tasks": [], "stages": []}
        return CULTIVATION_TIPS_CACHE

    if not isinstance(raw_data, dict):
        CULTIVATION_TIPS_CACHE = {"tasks": [], "stages": []}
        return CULTIVATION_TIPS_CACHE

    tasks = raw_data.get("tasks", [])
    stages = raw_data.get("stages", [])
    CULTIVATION_TIPS_CACHE = {
        "tasks": tasks if isinstance(tasks, list) else [],
        "stages": stages if isinstance(stages, list) else [],
    }
    return CULTIVATION_TIPS_CACHE


def get_crop_library_entry_by_name(crop_name):
    normalized_name = str(crop_name or "").strip().lower()
    if not normalized_name or normalized_name == "all":
        return None
    return next((entry for entry in load_crop_library() if str(entry.get("name") or "").strip().lower() == normalized_name), None)


def build_library_tips_data(active_crop):
    base_data = load_cultivation_tips_dataset()
    crop_entry = get_crop_library_entry_by_name(active_crop)
    crop_name = crop_entry["name"] if crop_entry is not None else (active_crop if active_crop and active_crop != "All" else "your crop")
    crop_text = str(crop_name)

    if crop_entry is not None and isinstance(crop_entry, dict):
        profile_cards = [
            {"icon": "fa-seedling", "label": "Planting", "value": str(crop_entry.get("planting_method", "N/A"))},
            {"icon": "fa-ruler-combined", "label": "Spacing", "value": f"{crop_entry.get('row_spacing', 'N/A')} rows | {crop_entry.get('plant_spacing', 'N/A')} plants"},
            {"icon": "fa-mountain-sun", "label": "Soil & pH", "value": f"{crop_entry.get('soil_type', 'N/A')} | pH {crop_entry.get('ph_range', 'N/A')}"},
            {"icon": "fa-cloud-sun-rain", "label": "Climate", "value": f"{crop_entry.get('temperature', 'N/A')} | {crop_entry.get('rainfall', 'N/A')}"},
            {"icon": "fa-sun-plant-wilt", "label": "Sun & Humidity", "value": f"{crop_entry.get('sunlight', 'N/A')} | {crop_entry.get('humidity', 'N/A')}"},
            {"icon": "fa-flask", "label": "NPK Target", "value": f"N {crop_entry.get('nitrogen', 'N/A')} | P {crop_entry.get('phosphorus', 'N/A')} | K {crop_entry.get('potassium', 'N/A')}"},
        ]
        crop_guides = [
            {
                "title": "Field setup",
                "items": [
                    f"Use {crop_entry['planting_method']} planting for a more uniform {crop_text.lower()} stand.",
                    f"Keep row spacing near {crop_entry['row_spacing']} and plant spacing near {crop_entry['plant_spacing']} to reduce canopy congestion.",
                    f"Prefer {crop_entry['soil_type']} and hold the root zone close to pH {crop_entry['ph_range']}.",
                ],
            },
            {
                "title": "Nutrition and water",
                "items": [
                    f"Plan the nutrient schedule around N {crop_entry['nitrogen']}, P {crop_entry['phosphorus']}, and K {crop_entry['potassium']}.",
                    f"Match irrigation frequency to the local weather window because {crop_text.lower()} performs best around {crop_entry['temperature']}.",
                    f"Keep moisture steady without waterlogging, especially when humidity stays around {crop_entry['humidity']}.",
                ],
            },
            {
                "title": "Companion planning",
                "items": [
                    f"Good companion crops: {', '.join([str(crop_entry.get('good_companions', [])[i]) for i in range(min(len(list(crop_entry.get('good_companions', []))), 4))]) or 'No companion guidance available yet.'}",
                    f"Avoid pairing with: {', '.join([str(crop_entry.get('bad_companions', [])[i]) for i in range(min(len(list(crop_entry.get('bad_companions', []))), 4))]) or 'No avoid-list available yet.'}",
                    f"Life cycle: {str(crop_entry.get('life_cycle', 'annual'))} crop, so keep labour and harvest planning aligned with that cycle.",
                ],
            },
            {
                "title": "Crop-specific reminders",
                "items": [str(crop_entry.get("farming_tips", [])[i]) for i in range(min(len(list(crop_entry.get("farming_tips", []))), 5))],
            },
        ]
    else:
        profile_cards = [
            {"icon": "fa-location-dot", "label": "Start with field choice", "value": "Select a crop to unlock soil, spacing, and climate guidance."},
            {"icon": "fa-vial-circle-check", "label": "Test before input", "value": "Use soil pH and fertility results before deciding irrigation or fertilizer loads."},
            {"icon": "fa-shield-heart", "label": "Prevent stress early", "value": "Good drainage, clean tools, and regular scouting avoid costly recovery sprays later."},
        ]
        crop_guides = [
            {
                "title": "How to use this page",
                "items": [
                    "Pick a crop from the filter to see climate, soil, spacing, and nutrient guidance.",
                    "Use By Task for operational reminders and By Stage for a simple season-wise checklist.",
                    "Match these tips with your local weather, soil, and disease history before applying inputs.",
                ],
            }
        ]

    tasks = []
    raw_tasks = base_data.get("tasks", []) if isinstance(base_data, dict) else []
    for raw_task in raw_tasks:
        if not isinstance(raw_task, dict):
            continue
        tasks.append(
            {
                "icon": str(raw_task.get("icon") or "fa-list-check"),
                "label": str(raw_task.get("label") or "Field task").strip() or "Field task",
                "summary": str(raw_task.get("summary") or "").strip(),
                "detail": str(raw_task.get("detail") or "").strip(),
            }
        )

    tasks.extend(
        [
            {
                "icon": "fa-ruler-combined",
                "label": "Spacing and canopy control",
                "summary": f"Keep {crop_text} canopy open enough for airflow, spray coverage, and even light capture.",
                "detail": f"Follow recommended row and plant spacing, remove weak or overcrowded plants, and stop dense canopy pockets from trapping humidity around {crop_text.lower()}.",
            },
            {
                "icon": "fa-cloud-sun-rain",
                "label": "Weather readiness",
                "summary": "Adjust irrigation, sprays, and field visits around rain, humidity, and heat shifts.",
                "detail": f"Before wet spells, improve drainage and postpone unnecessary irrigation. In heat windows, protect {crop_text.lower()} from sudden moisture stress and increase field checks.",
            },
            {
                "icon": "fa-clipboard-check",
                "label": "Harvest and records",
                "summary": "Track field actions so you can connect yield, disease pressure, and input timing.",
                "detail": f"Record irrigation dates, fertilizer splits, spray rounds, and harvest notes for {crop_text.lower()} so the next season becomes easier to plan and troubleshoot.",
            },
        ]
    )

    stages = []
    raw_stages = base_data.get("stages", []) if isinstance(base_data, dict) else []
    for raw_stage in raw_stages:
        if not isinstance(raw_stage, dict):
            continue
        items = [str(item).strip() for item in raw_stage.get("items", []) if str(item).strip()]
        label = str(raw_stage.get("label") or "Crop stage").strip() or "Crop stage"
        if crop_entry is not None:
            if "seedling" in label.lower():
                items.append(f"Keep spacing close to {crop_entry['row_spacing']} x {crop_entry['plant_spacing']} from the beginning.")
            elif "vegetative" in label.lower():
                items.append(f"Maintain root-zone conditions around {crop_entry['soil_type']} and pH {crop_entry['ph_range']}.")
            elif "flower" in label.lower():
                items.append(f"Protect flowering with steady moisture and timely potassium close to {crop_entry['potassium']}.")
            elif "harvest" in label.lower():
                items.append(f"Plan harvest labour early because {crop_text} follows a {crop_entry['life_cycle'].lower()} production cycle.")
            elif "post" in label.lower():
                items.append(f"Review companion and rotation choices before replanting {crop_text.lower()} in the same field.")
        stages.append(
            {
                "icon": str(raw_stage.get("icon") or "fa-leaf"),
                "label": label,
                "items": unique_crop_list(items),
            }
        )

    return {
        "crop_name": crop_text,
        "profile_cards": profile_cards,
        "crop_guides": crop_guides,
        "tasks": tasks,
        "stages": stages,
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
            weak_tag_product_count = int(weak_tag_product_count) + 1

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

    missing_mappings_list = [m for m in missing_mappings]
    missing_content_list = [c for c in missing_content]
    recommendation_review_list = [r for r in recommendation_review]

    return {
        "mapping_count": len(mappings),
        "unmapped_count": len(missing_mappings),
        "missing_content_count": len(missing_content),
        "weak_tag_product_count": int(weak_tag_product_count),
        "missing_mappings": [missing_mappings_list[i] for i in range(min(len(missing_mappings_list), 12))],
        "missing_content": [missing_content_list[i] for i in range(min(len(missing_content_list), 12))],
        "recommendation_review": [recommendation_review_list[i] for i in range(min(len(recommendation_review_list), 10))],
    }


def safe_json_loads(raw_value, default):
    if raw_value in (None, ""):
        return default
    try:
        return json.loads(raw_value)
    except (TypeError, ValueError):
        return default


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
    estimated = int(float(int(base_price * (1 + markup) * 100) / 100.0))
    return max(base_price + 20, estimated)


def compute_store_discount(price, mrp):
    base_price = max(int(price or 0), 1)
    base_mrp = max(int(mrp or 0), base_price)
    return max(0, int(float(int((base_mrp - base_price) * 10000 / base_mrp) / 100.0)))


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
            rating = float(int(float(rating_value) * 10) / 10.0)
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
        seeded_count = int(seeded_count) + 1

    if seeded_count:
        db.session.commit()
    return seeded_count


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


def normalize_store_search_text(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def matches_store_search_query(search_text, search_query):
    normalized_query = normalize_store_search_text(search_query)
    if not normalized_query:
        return True

    normalized_search = normalize_store_search_text(search_text)
    if normalized_query in normalized_search:
        return True

    search_tokens = normalized_search.split()
    query_tokens = normalized_query.split()
    if not search_tokens or not query_tokens:
        return False

    return all(
        any(query_token in search_token or search_token in query_token for search_token in search_tokens)
        for query_token in query_tokens
    )


def serialize_store_product(product):
    meta = get_store_category_meta(product.category)
    tags = safe_json_loads(product.tags_json, [])
    if not isinstance(tags, list):
        tags = []

    description = str(product.description or "").strip()
    rating_value = float(int(float(product.rating or 0) * 10) / 10.0)
    search_text = " ".join(
        [
            str(product.name or ""),
            str(product.slug or ""),
            str(product.category or ""),
            description,
            str(product.seller or ""),
            str(product.unit or ""),
            " ".join(str(tag) for tag in tags),
        ]
    ).lower()

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


def apply_store_filters(products, search_query="", active_category="All", sort_option="featured", recommended_slug=None):
    category = active_category if active_category in STORE_CATEGORY_ORDER else "All"

    filtered = []
    for product in products:
        if category != "All" and product["category"] != category:
            continue
        if not matches_store_search_query(product["search_text"], search_query):
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
            count = sum(1 for product in serialized_products)
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
        float(sum(product["rating"] for product in serialized_products)) / max(len(serialized_products), 1),
        1,
    ) if serialized_products else 0.0
    has_active_filters = bool(str(search_query or "").strip()) or active_category != "All" or sort_option != "featured"
    organic_products = [product for product in serialized_products if product["category"] == "Organic"]
    organic_featured = organic_products[0] if organic_products else None

    return {
        "products": filtered_products,
        "all_products": serialized_products,
        "catalog_products": filtered_products if has_active_filters else serialized_products,
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
        "organic_count": len(organic_products),
        "organic_products": organic_products[:4],
        "organic_featured": organic_featured,
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


def get_order_status_timestamps(order):
    notes = get_order_notes(order)
    raw_timestamps = notes.get("status_timestamps")
    timestamp_map = raw_timestamps if isinstance(raw_timestamps, dict) else {}
    result = {}
    for status in FULFILLMENT_STATUS_ORDER:
        result[status] = parse_stored_timestamp(timestamp_map.get(status))
    return result


def set_order_status_timestamp(order, status, timestamp=None):
    if order == None:
        return

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in FULFILLMENT_STATUS_ORDER:
        return

    notes = get_order_notes(order)
    raw_timestamps = notes.get("status_timestamps")
    timestamp_map = raw_timestamps if isinstance(raw_timestamps, dict) else {}
    when = normalize_timestamp(timestamp) or datetime.now(timezone.utc)
    timestamp_map[normalized_status] = when.isoformat()
    notes["status_timestamps"] = timestamp_map
    set_order_notes(order, notes)


def build_order_timeline(order):
    fulfillment_status = get_fulfillment_status(order)
    payment_status = str(getattr(order, "status", "created") or "created").strip().lower()
    status_timestamps = get_order_status_timestamps(order)
    is_paid = payment_status == "paid"
    confirmed_timestamp = status_timestamps.get("confirmed") or (
        status_timestamps.get("delivered") if fulfillment_status == "delivered" else None
    )

    return [
        {
            "key": "placed",
            "label": "Order placed",
            "detail": "Your request is saved in the system.",
            "completed": True,
            "timestamp": normalize_timestamp(getattr(order, "created_at", None)),
        },
        {
            "key": "pending",
            "label": "Pending review" if is_paid else "Payment pending",
            "detail": (
                "Admin review is pending before dispatch."
                if is_paid
                else "Complete payment to move this order into confirmation."
            ),
            "completed": is_paid,
            "timestamp": status_timestamps.get("pending") if is_paid else None,
        },
        {
            "key": "confirmed",
            "label": "Confirmed",
            "detail": "Admin has approved the order and preparation is underway.",
            "completed": fulfillment_status in {"confirmed", "delivered"},
            "timestamp": confirmed_timestamp,
        },
        {
            "key": "delivered",
            "label": "Delivered",
            "detail": "The order is marked as delivered.",
            "completed": fulfillment_status == "delivered",
            "timestamp": status_timestamps.get("delivered"),
        },
    ]


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


def parse_percentage_value(value, default=0):
    text = str(value or "").strip().replace("%", "")
    if not text:
        return default
    try:
        return clamp(int(float(text)), 0, 99)
    except (TypeError, ValueError):
        return default


def normalize_disease_dataset_entry(entry_name, raw_entry):
    if not isinstance(raw_entry, dict):
        return None

    name = str(raw_entry.get("name") or raw_entry.get("disease_name") or entry_name or "Unknown Disease").strip() or "Unknown Disease"
    etiology = raw_entry.get("etiology", {})
    if not isinstance(etiology, dict):
        etiology = {}

    solution = raw_entry.get("solution", {})
    if not isinstance(solution, dict):
        solution = {}

    symptoms = [str(item).strip() for item in raw_entry.get("symptoms", []) if str(item).strip()]
    prevention = [str(item).strip() for item in raw_entry.get("prevention", []) if str(item).strip()]
    products = [str(item).strip() for item in raw_entry.get("products", []) if str(item).strip()]
    organic = [str(item).strip() for item in solution.get("organic", []) if str(item).strip()]
    chemical = [str(item).strip() for item in solution.get("chemical", []) if str(item).strip()]
    match_features = raw_entry.get("match_features", {})
    if not isinstance(match_features, dict):
        match_features = {}
    priority_rules = [str(item).strip() for item in raw_entry.get("priority_rules", []) if str(item).strip()]

    return {
        "name": name,
        "key": normalize_disease_key(name),
        "confidence": str(raw_entry.get("confidence") or "").strip(),
        "etiology": {
            "pathogen": str(etiology.get("pathogen") or "").strip(),
            "environment": str(etiology.get("environment") or "").strip(),
            "transmission": str(etiology.get("transmission") or "").strip(),
        },
        "symptoms": symptoms,
        "solution": {
            "organic": organic,
            "chemical": chemical,
        },
        "products": products,
        "prevention": prevention,
        "match_features": {
            "color": [str(item).strip() for item in match_features.get("color", []) if str(item).strip()],
            "pattern": [str(item).strip() for item in match_features.get("pattern", []) if str(item).strip()],
            "part": [str(item).strip() for item in match_features.get("part", []) if str(item).strip()],
            "texture": [str(item).strip() for item in match_features.get("texture", []) if str(item).strip()],
            "spread": str(match_features.get("spread") or "").strip(),
        },
        "priority_rules": priority_rules,
    }


def load_disease_dataset():
    global DISEASE_DATA_CACHE

    if DISEASE_DATA_CACHE is not None:
        return DISEASE_DATA_CACHE

    DISEASE_DATA_CACHE = {}
    if not DISEASE_DATASET_PATH.exists():
        return DISEASE_DATA_CACHE

    try:
        raw_data = json.loads(DISEASE_DATASET_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return DISEASE_DATA_CACHE

    normalized_entries = {}
    if isinstance(raw_data, dict):
        iterable = raw_data.items()
    elif isinstance(raw_data, list):
        iterable = ((item.get("name") or item.get("disease_name"), item) for item in raw_data if isinstance(item, dict))
    else:
        iterable = []

    for entry_name, raw_entry in iterable:
        normalized_entry = normalize_disease_dataset_entry(entry_name, raw_entry)
        if normalized_entry is None:
            continue
        normalized_entries[normalized_entry["key"]] = normalized_entry

    DISEASE_DATA_CACHE = normalized_entries
    return DISEASE_DATA_CACHE


def find_disease_dataset_entry(disease_name=""):
    normalized_name = normalize_disease_key(disease_name)
    if not normalized_name:
        return None

    dataset = load_disease_dataset()
    alias_map = {
        "mosaic disease": "mosaic virus",
        "mosaic virus": "mosaic virus",
        "rust leaf spot": "leaf spot",
        "rust / leaf spot": "leaf spot",
        "septoria leaf spot": "leaf spot",
        "tomato septoria leaf spot": "leaf spot",
        "leaf curl": "leaf curl virus",
        "tomato yellowleaf curl virus": "leaf curl virus",
        "tomato yellow leaf curl virus": "leaf curl virus",
        "tomato mosaic virus": "mosaic virus",
        "bacterial spot": "bacterial spot",
        "bell bacterial spot": "bacterial spot",
        "pepper bacterial spot": "bacterial spot",
        "tomato bacterial spot": "bacterial spot",
        "leaf mold": "leaf mold",
        "target spot": "target spot",
        "rice blast": "rice blast",
        "leaf rust": "leaf rust",
        "yellow rust": "yellow rust",
        "common rust": "common rust",
        "healthy crop": "healthy",
    }
    direct_match = dataset.get(normalized_name)
    if direct_match is not None:
        return direct_match

    aliased_name = alias_map.get(normalized_name)
    if aliased_name:
        direct_alias_match = dataset.get(aliased_name)
        if direct_alias_match is not None:
            return direct_alias_match

    stop_tokens = {"crop", "disease", "attack", "damage", "virus", "leaf", "plant", "infection"}
    name_tokens = {
        token for token in re.findall(r"[a-z0-9]+", normalized_name)
        if token not in stop_tokens
    }
    if not name_tokens:
        return None

    scored_matches = []
    for entry in dataset.values():
        entry_key = entry["key"]
        entry_tokens = {
            token for token in re.findall(r"[a-z0-9]+", entry_key)
            if token not in stop_tokens
        }
        overlap = len(name_tokens & entry_tokens)
        similarity_ratio = SequenceMatcher(None, normalized_name, entry_key).ratio()
        if not overlap and similarity_ratio < 0.84:
            continue
        if overlap < max(1, min(len(name_tokens), len(entry_tokens), 2)) and similarity_ratio < 0.84:
            continue
        score = overlap * 4
        if normalized_name in entry_key or entry_key in normalized_name:
            score += 3
        score += similarity_ratio
        scored_matches.append((score, len(entry.get("products", [])), len(entry.get("symptoms", [])), entry))

    scored_matches.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return scored_matches[0][3] if scored_matches else None


def load_local_product_image_index():
    global PRODUCT_IMAGE_INDEX_CACHE

    if PRODUCT_IMAGE_INDEX_CACHE is not None:
        return PRODUCT_IMAGE_INDEX_CACHE

    index = {}
    if not PRODUCTS_UPLOAD_DIR.exists():
        PRODUCT_IMAGE_INDEX_CACHE = index
        return PRODUCT_IMAGE_INDEX_CACHE

    for file_path in PRODUCTS_UPLOAD_DIR.iterdir():
        if not file_path.is_file():
            continue
        url = f"/static/products/{quote(file_path.name)}"
        index[file_path.name.lower()] = url
        index[slugify_crop_name(file_path.stem)] = url

    PRODUCT_IMAGE_INDEX_CACHE = index
    return PRODUCT_IMAGE_INDEX_CACHE


def resolve_local_product_image_url(*candidates):
    image_index = load_local_product_image_index()
    for candidate in candidates:
        raw_value = str(candidate or "").strip()
        if not raw_value:
            continue
        basename = Path(urlparse(raw_value).path or raw_value).name
        stem = Path(basename).stem
        for key in (raw_value.lower(), basename.lower(), slugify_crop_name(stem), slugify_crop_name(basename)):
            if key in image_index:
                return image_index[key]
    return STORE_PRODUCT_FALLBACK_IMAGE


def infer_solution_bucket(product_asset_name="", product_name="", organic_solutions=None):
    normalized_asset = slugify_crop_name(Path(str(product_asset_name or "")).stem)
    normalized_name = slugify_crop_name(product_name)
    organic_haystack = " ".join(str(item or "") for item in (organic_solutions or [])).lower()
    organic_markers = {"neem", "trichoderma", "solarization", "compost", "bio", "organic", "soap"}
    if any(marker in organic_haystack for marker in organic_markers):
        if any(marker in normalized_asset for marker in organic_markers) or any(marker in normalized_name for marker in organic_markers):
            return "Organic"
    if any(marker in normalized_asset for marker in organic_markers) or any(marker in normalized_name for marker in organic_markers):
        return "Organic"
    return "Chemical"


def format_product_asset_name(asset_name):
    stem = Path(str(asset_name or "")).stem.replace("_", " ").replace("-", " ").strip()
    return stem.title() if stem else "Crop Care Product"


def build_dynamic_product_description(product_name, disease_name, disease_type, solution_bucket, etiology):
    disease_label = str(disease_name or "crop disease").strip().lower()
    pathogen = str((etiology or {}).get("pathogen") or "").strip().lower()
    category_phrase_map = {
        "fungus": "fungicide",
        "bacteria": "bactericide",
        "virus": "vector-management crop-care product",
        "insect": "insecticide",
        "stress": "crop recovery input",
        "healthy": "preventive crop-care input",
        "general": "crop-care input",
    }
    category_phrase = category_phrase_map.get(disease_type, category_phrase_map["general"])
    if solution_bucket.lower() == "organic":
        category_phrase = "organic crop-care solution"
    if pathogen:
        return f"{product_name} is a {category_phrase} aligned with {disease_label} management and {pathogen} pressure."
    return f"{product_name} is a {category_phrase} effective for {disease_label} management and field protection."


def build_dynamic_product_benefits(disease_name, solution_bucket, symptoms, prevention, etiology):
    benefits = [f"Recommended for {str(disease_name or 'crop disease').strip()} management."]
    if symptoms and len(list(symptoms)) > 0:
        benefits.append(f"Targets visible issues like {str(list(symptoms)[0]).strip().lower()}.")
    if str((etiology or {}).get("environment") or "").strip():
        benefits.append(f"Useful during {str(etiology['environment']).strip().lower()} conditions.")
    elif prevention and len(list(prevention)) > 0:
        benefits.append(f"Supports prevention steps such as {str(list(prevention)[0]).strip().lower()}.")
    benefits.append(f"Fits a {solution_bucket.lower()} treatment plan for rapid field action.")
    return unique_crop_list(benefits)[:3]


def build_virtual_store_product(product_asset_name, disease_entry, payload):
    disease_name = str(payload.get("disease") or disease_entry.get("name") or "Crop Disease").strip()
    crop_name = str(payload.get("crop") or "Crop").strip()
    etiology = disease_entry.get("etiology") or build_fallback_etiology(payload)
    organic_solutions = list((disease_entry.get("solution") or {}).get("organic", []))
    solution_bucket = infer_solution_bucket(product_asset_name, "", organic_solutions)
    category = "Organic" if solution_bucket == "Organic" else "Pesticides"
    category_meta = get_store_category_meta(category)
    product_name = format_product_asset_name(product_asset_name)
    detail_slug = slugify_crop_name(product_asset_name or product_name)
    price_seed = int(sha1(f"{disease_name}|{product_name}|preview".encode()).hexdigest(), 16)
    price = (249 if solution_bucket == "Organic" else 399) + (price_seed % 7) * 35
    image_url = resolve_local_product_image_url(product_asset_name, product_name)
    description = build_dynamic_product_description(
        product_name,
        disease_name,
        infer_disease_type_from_text(disease_name, etiology.get("pathogen")),
        solution_bucket,
        etiology,
    )
    return {
        "id": None,
        "slug": detail_slug,
        "name": product_name,
        "category": category,
        "category_label": category,
        "category_icon": category_meta["icon"],
        "category_accent": category_meta["accent"],
        "price": int(price),
        "mrp": int(price + max(60, int(price * 0.18))),
        "discount_pct": 12,
        "rating": 4.5,
        "rating_label": "4.5",
        "rating_count": 36,
        "image_url": image_url,
        "fallback_image": STORE_PRODUCT_FALLBACK_IMAGE,
        "description": description,
        "short_description": truncate_text(description, 96),
        "seller": default_store_seller(category),
        "unit": "Pack",
        "tags": [str(disease_name).lower(), str(solution_bucket).lower(), str(crop_name).lower()],
        "highlights": build_dynamic_product_benefits(
            disease_name,
            solution_bucket,
            disease_entry.get("symptoms", []),
            disease_entry.get("prevention", []),
            etiology,
        ),
        "detail_url": f"/market/recommendation/{detail_slug}?asset={quote(str(product_asset_name or ''))}&disease={quote(disease_name)}&crop={quote(crop_name)}",
        "search_text": " ".join([product_name, category, description, disease_name, crop_name]).lower(),
    }


def build_fallback_etiology(payload):
    cause = str(payload.get("cause") or "").strip()
    disease_type = infer_disease_type_from_text(
        payload.get("disease"),
        payload.get("cause"),
        payload.get("organic_solution"),
        payload.get("chemical_solution"),
    )
    environment_map = {
        "fungus": "Humid canopy and leaf wetness conditions",
        "bacteria": "Warm wet conditions with splash spread",
        "virus": "Vector-prone warm field conditions",
        "insect": "Active pest movement in the crop canopy",
        "stress": "Field stress and crop imbalance",
        "healthy": "Healthy growing conditions",
        "general": "Field conditions need closer verification",
    }
    transmission_map = {
        "fungus": "Airborne spores and water splash",
        "bacteria": "Rain splash, tools, and infected tissue",
        "virus": "Insect vectors and infected plants",
        "insect": "Flying pests, larvae, or plant-to-plant spread",
        "stress": "Abiotic stress across the affected patch",
        "healthy": "None",
        "general": "Requires field scouting for confirmation",
    }
    return {
        "pathogen": cause or "Needs field confirmation",
        "environment": environment_map.get(disease_type, environment_map["general"]),
        "transmission": transmission_map.get(disease_type, transmission_map["general"]),
    }


def build_dataset_do_now_checklist(disease_entry):
    actions = ["Inspect nearby plants for the same symptoms before full-field treatment."]
    solution = disease_entry.get("solution") or {}
    organic = [str(item).strip() for item in solution.get("organic", []) if str(item).strip()]
    chemical = [str(item).strip() for item in solution.get("chemical", []) if str(item).strip()]
    prevention = [str(item).strip() for item in disease_entry.get("prevention", []) if str(item).strip()]

    if organic:
        actions.append(f"Start with organic option: {organic[0]}")
    if chemical:
        actions.append(f"If pressure continues, use chemical option: {chemical[0]}")
    if prevention:
        actions.append(prevention[0])
    actions.append("Review the affected patch again within 24 to 48 hours.")
    items_5319 = unique_crop_list(actions)
    if not isinstance(items_5319, list):
        items_5319 = list(items_5319 or [])
    return items_5319[:4]


def resolve_dataset_store_products(disease_entry):
    matched_products = []
    seen_ids = set()
    for asset_name in disease_entry.get("products", []):
        product = find_store_product_by_asset_hint(asset_name)
        if product is None:
            continue
        if getattr(product, "id", None) in seen_ids:
            continue
        seen_ids.add(product.id)
        matched_products.append(product)
    return matched_products


def build_disease_product_card(product_asset_name, disease_entry, payload, store_product=None):
    disease_name = str(payload.get("disease") or disease_entry.get("name") or "Crop Disease").strip()
    crop_name = str(payload.get("crop") or "Crop").strip()
    etiology = disease_entry.get("etiology") or build_fallback_etiology(payload)
    organic_solutions = list((disease_entry.get("solution") or {}).get("organic", []))
    disease_type = infer_disease_type_from_text(disease_name, etiology.get("pathogen"))
    solution_bucket = infer_solution_bucket(product_asset_name, getattr(store_product, "name", ""), organic_solutions)
    serialized_product = serialize_store_product(store_product) if store_product is not None else None
    virtual_product = build_virtual_store_product(product_asset_name, disease_entry, payload) if serialized_product is None else None
    product_name = serialized_product["name"] if serialized_product is not None else format_product_asset_name(product_asset_name)
    category = serialized_product["category"] if serialized_product is not None else ("Organic" if solution_bucket == "Organic" else "Pesticides")
    brand = serialized_product["seller"] if serialized_product is not None else default_store_seller(category)
    price_seed = int(sha1(f"{disease_name}|{product_name}".encode()).hexdigest(), 16)
    rating = serialized_product["rating"] if serialized_product is not None else round(float(4.3 + (price_seed % 5) * 0.1), 1)
    price = serialized_product["price"] if serialized_product is not None else (249 if solution_bucket == "Organic" else 399) + (price_seed % 7) * 35
    detail_url = serialized_product["detail_url"] if serialized_product is not None else virtual_product["detail_url"]
    image_url = resolve_local_product_image_url(
        serialized_product["image_url"] if serialized_product is not None else "",
        virtual_product["image_url"] if virtual_product is not None else "",
        product_asset_name,
        getattr(store_product, "image_url", "") if store_product is not None else "",
        product_name,
    )
    description = build_dynamic_product_description(product_name, disease_name, disease_type, solution_bucket, etiology)
    benefits = build_dynamic_product_benefits(
        disease_name,
        solution_bucket,
        disease_entry.get("symptoms", []),
        disease_entry.get("prevention", []),
        etiology,
    )
    return {
        "id": serialized_product.get("id") if serialized_product is not None else None,
        "name": product_name,
        "brand": brand,
        "category": category,
        "solution_type": f"{solution_bucket} Solution",
        "rating": rating,
        "rating_label": f"{float(str(rating or 0)):.1f}",
        "price": int(price),
        "description": description,
        "benefits": benefits,
        "image_url": image_url,
        "fallback_image": STORE_PRODUCT_FALLBACK_IMAGE,
        "detail_url": detail_url,
        "buy_url": detail_url,
        "buy_product_id": serialized_product.get("id") if serialized_product is not None else None,
        "reason": f"Selected for {crop_name} because it supports the treatment plan for {disease_name}.",
        "asset_name": str(product_asset_name or "").strip(),
    }


def build_disease_report_context(payload, recommended_product=None, best_product_name=""):
    disease_name = str(payload.get("disease") or "").strip()
    disease_entry = find_disease_dataset_entry(disease_name)
    if disease_entry is not None:
        payload["disease"] = disease_entry["name"]

    report_name = str(payload.get("disease") or disease_name or "Unknown Disease").strip() or "Unknown Disease"
    confidence_value = int(payload.get("confidence") or 0) or parse_percentage_value(
        disease_entry.get("confidence") if disease_entry is not None else "",
        default=65,
    )
    etiology = disease_entry.get("etiology") if disease_entry is not None else build_fallback_etiology(payload)
    symptoms = list(disease_entry.get("symptoms", [])) if disease_entry is not None else extract_guidance_points(payload.get("symptoms"), limit=5)
    organic_solutions = (
        list((disease_entry.get("solution") or {}).get("organic", []))
        if disease_entry is not None
        else extract_guidance_points(payload.get("organic_solution"), limit=3)
    )
    chemical_solutions = (
        list((disease_entry.get("solution") or {}).get("chemical", []))
        if disease_entry is not None
        else extract_guidance_points(payload.get("chemical_solution"), limit=3)
    )
    prevention = list(disease_entry.get("prevention", [])) if disease_entry is not None else (
        list(payload.get("prevention") or [])
        if isinstance(payload.get("prevention"), list)
        else extract_guidance_points(payload.get("prevention"), limit=4)
    )

    card_seed = {
        "name": report_name,
        "symptoms": symptoms,
        "prevention": prevention,
        "solution": {"organic": organic_solutions, "chemical": chemical_solutions},
        "etiology": etiology,
    }
    suggested_products = []
    product_refs = disease_entry.get("products") if disease_entry is not None else []
    if not isinstance(product_refs, list):
        product_refs = []

    for asset_name in product_refs:
        matched_product = find_store_product_by_asset_hint(asset_name)
        suggested_products.append(build_disease_product_card(asset_name, card_seed, payload, store_product=matched_product))

    if not suggested_products and recommended_product is not None:
        suggested_products.append(
            build_disease_product_card(
                recommended_product.get("name", best_product_name),
                card_seed,
                payload,
                store_product=get_store_product_by_id(recommended_product.get("id")),
            )
        )
    elif not suggested_products and best_product_name:
        matched_product = find_store_product_by_name(best_product_name)
        suggested_products.append(build_disease_product_card(best_product_name, card_seed, payload, store_product=matched_product))

    payload["confidence"] = confidence_value
    payload["symptoms"] = "; ".join(symptoms) if symptoms else str(payload.get("symptoms") or "")
    payload["organic_solution"] = "; ".join(organic_solutions) if organic_solutions else str(payload.get("organic_solution") or "")
    payload["chemical_solution"] = "; ".join(chemical_solutions) if chemical_solutions else str(payload.get("chemical_solution") or "")
    payload["prevention"] = prevention

    return {
        "report_title": f"{report_name} Disease Report",
        "confidence_display": f"{confidence_value}%",
        "etiology": etiology,
        "symptoms_list": symptoms,
        "organic_solutions": organic_solutions,
        "chemical_solutions": chemical_solutions,
        "prevention_tips": prevention,
        "suggested_products": suggested_products,
        "disease_dataset_found": disease_entry is not None,
    }


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
    items_5503 = unique_crop_list(actions)
    if not isinstance(items_5503, list):
        items_5503 = list(items_5503 or [])
    return items_5503[:4]


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
    product_tokens = set(re.findall(r"[a-z0-9]+", str(search_text or "").lower()))
    if crop_tokens:
        score += len(crop_tokens & product_tokens) * 1.5

    if diagnostic_text:
        diagnostic_tokens = set(re.findall(r"[a-z0-9]+", str(diagnostic_text or "").lower()))
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



def resolve_store_recommendation(disease_name=None, cause=None, organic_solution=None, chemical_solution=None, best_product_name="", crop_name=None):
    if best_product_name:
        product = find_store_product_by_name(best_product_name)
        if product:
            return product

    diagnostic_text = " ".join(filter(None, [str(disease_name or ""), str(cause or ""), str(organic_solution or ""), str(chemical_solution or "")])).lower()
    disease_type = infer_disease_type_from_text(diagnostic_text)

    if disease_name:
        mapping = DiseaseProductMapping.query.filter_by(disease_key=normalize_disease_key(disease_name)).first()
        if mapping and getattr(mapping, "product_id", None):
            product = StoreProduct.query.get(mapping.product_id)
            if product:
                return product

    scored_matches = []
    for product in get_all_store_products():
        score = score_store_product_for_diagnosis(product, disease_type, diagnostic_text, crop_name=crop_name)
        scored_matches.append((score, float(product.rating or 0), product))

    scored_matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored_matches[0][2] if scored_matches and scored_matches[0][0] > 0 else None


def enrich_disease_response_payload(payload):
    enriched = dict(payload)
    confidence = clamp(int(enriched.get("confidence") or 0), 0, 99)
    if confidence == 0:
        confidence = 65
    risk_level = str(enriched.get("risk_level") or "").strip().title()
    if risk_level not in {"Low", "Medium", "High"}:
        risk_level = "Low" if confidence >= 88 else "Medium" if confidence >= 72 else "High"

    dataset_entry = find_disease_dataset_entry(enriched.get("disease"))
    if dataset_entry is not None:
        etiology = dataset_entry.get("etiology", {})
        organic_solutions = [str(item).strip() for item in (dataset_entry.get("solution") or {}).get("organic", []) if str(item).strip()]
        chemical_solutions = [str(item).strip() for item in (dataset_entry.get("solution") or {}).get("chemical", []) if str(item).strip()]
        prevention = [str(item).strip() for item in dataset_entry.get("prevention", []) if str(item).strip()]
        symptoms = [str(item).strip() for item in dataset_entry.get("symptoms", []) if str(item).strip()]

        enriched["disease"] = dataset_entry["name"]
        enriched["confidence"] = confidence or parse_percentage_value(dataset_entry.get("confidence"), default=65)
        enriched["risk_level"] = risk_level
        enriched["analysis_source"] = str(enriched.get("analysis_source") or "AI diagnosis").strip()
        enriched["confidence_label"] = build_confidence_label(enriched["confidence"])
        enriched["cause"] = str(etiology.get("pathogen") or enriched.get("cause") or "").strip()
        enriched["symptoms"] = "; ".join(symptoms)
        enriched["organic_solution"] = "; ".join(organic_solutions)
        enriched["chemical_solution"] = "; ".join(chemical_solutions)
        enriched["prevention"] = prevention
        enriched["matched_symptoms"] = list(symptoms or [])[:4]
        enriched["why_this_result"] = str(enriched.get("diagnostic_reason") or "Matched with disease_data.json entry.").strip()
        enriched["consult_expert"] = build_consult_expert_note(enriched["confidence"], risk_level)
        enriched["do_now_checklist"] = build_dataset_do_now_checklist(dataset_entry)
        return enriched

    library_item = get_library_disease_item_by_name(
        disease_name=enriched.get("disease"),
        crop_name=enriched.get("crop"),
    )
    if library_item:
        enriched["disease"] = library_item.get("name") or enriched.get("disease")
    return enriched


def attach_store_recommendation(payload, best_product_name=""):
    payload = enrich_disease_response_payload(payload)
    disease_name = payload.get("disease")
    dataset_entry = find_disease_dataset_entry(disease_name)
    disease_type = infer_disease_type_from_text(
        disease_name,
        payload.get("cause"),
        payload.get("organic_solution"),
        payload.get("chemical_solution"),
    )
    recommended_product = None
    if dataset_entry is not None:
        dataset_products = resolve_dataset_store_products(dataset_entry)
        if dataset_products:
            recommended_product = dataset_products[0]

    if recommended_product is None:
        recommended_product = resolve_store_recommendation(
            disease_name=disease_name,
            cause=payload.get("cause"),
            organic_solution=payload.get("organic_solution"),
            chemical_solution=payload.get("chemical_solution"),
            best_product_name=best_product_name,
            crop_name=payload.get("crop"),
        )

    serialized_product = None
    if recommended_product is not None:
        serialized_product = serialize_store_product(recommended_product)
        serialized_product["reason"] = build_store_recommendation_reason(payload, serialized_product, disease_type)
        payload["recommended_product"] = serialized_product
        payload["best_product"] = serialized_product.get("name", "")
        payload["product_link"] = serialized_product.get("detail_url", "")
    else:
        payload["recommended_product"] = None
        payload.setdefault("best_product", "")
        payload.setdefault("product_link", "")

    context = build_disease_report_context(
        payload,
        recommended_product=serialized_product,
        best_product_name=best_product_name,
    )
    if isinstance(context, dict):
        for k, v in context.items():
            payload[k] = v

    suggested = list(payload.get("suggested_products") or [])
    if suggested:
        payload["product_link"] = suggested[0].get("detail_url", "/market")
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
        "Authorization": f"Basic {b64encode(f'{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}'.encode()).decode('ascii')}",
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
        f"{order_id}|{payment_id}".encode(),
        sha256,
    ).hexdigest()
    return hmac.compare_digest(generated_signature, signature)


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
    best_keyword_signal = 0

    for entry in knowledge_entries:
        keywords = [str(keyword).lower() for keyword in entry.get("keywords", [])]
        crops = [str(crop).lower() for crop in entry.get("crops", [])]
        score = 0
        keyword_signal = 0

        for keyword in keywords:
            keyword_tokens = set(re.findall(r"[a-z0-9]+", keyword))
            if keyword in query_lower:
                score += 3
                keyword_signal = max(keyword_signal, 3)
            elif keyword_tokens and keyword_tokens.issubset(query_tokens):
                score += 2
                keyword_signal = max(keyword_signal, 2)
            elif keyword_tokens & query_tokens:
                score += 1
                keyword_signal = max(keyword_signal, 1)

        if crops and crop_lower:
            if any(crop in explicit_query_crops for crop in crops):
                score += 3
            elif not explicit_query_crops and crop_lower in crops:
                score += 2

        if score > best_score:
            best_score = score
            best_entry = entry
            best_keyword_signal = keyword_signal

    if best_entry and best_score >= 2 and best_keyword_signal > 0:
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

    if is_ai_chat_greeting_query(clean_query):
        return clean_query

    user_messages = [str(item.get("content") or "").strip() for item in history if item.get("role") == "user"]
    if user_messages and user_messages[-1].lower() == clean_query.lower():
        user_messages = user_messages[:-1]

    if not user_messages:
        return clean_query

    query_tokens = re.findall(r"[a-z0-9]+", clean_query.lower())
    should_merge_context = any(token in AI_CHAT_FOLLOWUP_TERMS for token in query_tokens)
    if not should_merge_context:
        return clean_query

    context_parts = user_messages[-2:] + [clean_query]
    return " ".join(part for part in context_parts if part)


def is_ai_chat_greeting_query(query_text):
    normalized = re.sub(r"[^a-z0-9\s]+", " ", str(query_text or "").strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False
    if normalized in AI_CHAT_GREETING_PHRASES:
        return True
    if re.fullmatch(r"h+i+", normalized):
        return True

    tokens = [token for token in re.findall(r"[a-z0-9]+", normalized) if token]
    return bool(tokens) and len(tokens) <= 3 and all(token in AI_CHAT_GREETING_TERMS for token in tokens)


def is_ai_chat_low_context_query(query_text):
    normalized = normalize_ai_crop_doctor_match_text(query_text)
    tokens = extract_ai_crop_doctor_match_tokens(normalized)
    if not tokens or is_ai_chat_greeting_query(query_text):
        return False
    if len(tokens) > 4:
        return False
    if tokens & AI_CROP_DOCTOR_SYMPTOM_CUES:
        return False
    if tokens & AI_CROP_DOCTOR_DATASET_HINT_TOKENS:
        return False
    return True


def format_ai_chat_history_for_prompt(history):
    lines = []
    for item in list(history or [])[-6:]:
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


def build_kisan_dost_reply(user, query, history=None, allow_generic_fallback=True):
    crop_name = normalize_kisan_dost_crop_name(user.crop_type or "crop")
    location_name = user.location or "aapke area"
    history = sanitize_ai_chat_history(history)
    query_text = build_ai_chat_context_query(query, history)
    query_lower = query_text.lower()
    answer_language = detect_ai_chat_language(query_text)
    farms = []
    task_summary = {"open_count": 0, "overdue_count": 0, "preview": []}
    if has_app_context():
        try:
            _, farms = ensure_user_farm_setup(user)
            task_summary = build_task_summary(user, limit=2)
        except Exception:
            farms = []
            task_summary = {"open_count": 0, "overdue_count": 0, "preview": []}
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
    weather_report_terms = ["report", "forecast", "update", "today", "aaj", "kal", "tomorrow", "weekly", "week"]
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

    if is_ai_chat_greeting_query(query_text):
        return build_ai_chat_greeting_reply(language=answer_language)

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
            "Abhi aapke planner me koi open task nahi hai. "
            "Farms page me irrigation, spray, fertilizer ya harvest ke reminders add kar lo. "
            "Isse daily planning kaafi easy ho jayegi."
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
                f"Humidity {assistant_weather['humidity']}% hai, estimated rainfall {assistant_weather['rainfall_mm']} mm hai, aur weather {assistant_weather['description']} hai. "
                f"Agar chaho to main barish ya irrigation advice bhi bata sakta hoon."
            )
        if any(word in query_lower for word in weather_report_terms):
            return (
                f"{assistant_weather['city']} ka latest weather update: temperature {assistant_weather['temp']} C, humidity {assistant_weather['humidity']}%, "
                f"rainfall around {assistant_weather['rainfall_mm']} mm, aur wind {round(float(assistant_weather['wind_speed_kmh']), 1)} km/h ke aas paas hai. "
                f"Current condition {assistant_weather['description']} hai aur last update {assistant_weather['updated_at']} ka hai. "
                f"Aise weather me spray se pehle leaf dryness aur field drainage check karna useful rahega."
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

    if not allow_generic_fallback:
        return None

    if is_ai_chat_low_context_query(query_text):
        return build_ai_chat_uncertain_query_reply(language=answer_language)

    return (
        f"{crop_name} ke liye abhi best focus moisture, soil balance, aur timely monitoring par rakho. "
        f"Agar aap mandi, irrigation, disease, ya weather me se kisi specific cheez par sawal puchoge to main aur exact advice de sakta hoon. "
        f"Dashboard ke cards bhi live guidance ke liye ready hain."
    )


def build_ai_chat_unknown_reply(query_text=""):
    language = detect_ai_chat_language(query_text)
    if str(language).strip().lower() == "english":
        return "Sorry, mujhe iske bare me pata nahi hai."
    return "Sorry, mujhe iske bare me pata nahi hai."


def resolve_ai_chat_response(user, query, history=None):
    sanitized_history = sanitize_ai_chat_history(history)
    context_query = build_ai_chat_context_query(query, sanitized_history)

    groq_reply = ask_groq_ai_crop_doctor(user, query, sanitized_history)
    if groq_reply:
        return {"response": groq_reply, "provider": "groq"}

    dataset_reply = lookup_ai_crop_doctor_local_qa(context_query)
    if dataset_reply:
        return {"response": dataset_reply, "provider": "local_knowledge"}

    contextual_reply = build_kisan_dost_reply(
        user,
        query,
        sanitized_history,
        allow_generic_fallback=False,
    )
    if contextual_reply:
        return {"response": contextual_reply, "provider": "contextual_assistant"}

    return {"response": build_ai_chat_unknown_reply(query), "provider": "fallback"}


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


def geocode_location_label(location_label):
    location_text = str(location_label or "").strip()
    if not location_text:
        return None

    cache_key = location_text.lower()
    if cache_key in LOCATION_GEOCODE_CACHE:
        return LOCATION_GEOCODE_CACHE[cache_key]

    queries = [location_text]
    if "india" not in cache_key:
        queries.append(f"{location_text}, India")

    for query in queries:
        payload = fetch_json(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 1,
            },
            headers={
                "User-Agent": "AgroVisionAI/1.0 (farm map geocoder)",
                "Accept": "application/json",
            },
        )
        if not isinstance(payload, list) or not payload:
            continue

        first = payload[0] or {}
        try:
            lat = float(first.get("lat"))
            lng = float(first.get("lon"))
        except (TypeError, ValueError):
            continue

        zoom = 12
        location_type = str(first.get("type") or "").strip().lower()
        if location_type in {"village", "hamlet", "suburb", "neighbourhood"}:
            zoom = 14
        elif location_type in {"city", "town", "municipality"}:
            zoom = 12
        elif location_type in {"state", "region", "county", "district"}:
            zoom = 10

        result = {"lat": lat, "lng": lng, "zoom": zoom}
        LOCATION_GEOCODE_CACHE[cache_key] = result
        return result

    LOCATION_GEOCODE_CACHE[cache_key] = None
    return None


def normalize_timestamp(value):
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def parse_stored_timestamp(value):
    if isinstance(value, str):
        raw_value = value.strip()
        if not raw_value:
            return None
        try:
            return normalize_timestamp(datetime.fromisoformat(raw_value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return normalize_timestamp(value)


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


def send_smtp_message(msg, label="email"):
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        return False, f"SMTP credentials are not configured for {label}."

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
        print(f"SMTP Error ({label}): {e}")
        return False, str(e)


def send_basic_email(target_email, subject, body, *, label="email"):
    return send_email_content(target_email, subject, body, label=label)


def send_twilio_text_message(target_phone, message, *, prefer_whatsapp=False, label="phone message"):
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return False, "Twilio credentials are not configured."

    to_number = normalize_alert_phone_number(target_phone)
    if not to_number:
        return False, "Target phone number is missing."

    if prefer_whatsapp:
        from_number = str(TWILIO_WHATSAPP_FROM or "").strip()
        if not from_number:
            return False, "Twilio WhatsApp sender is not configured."
        if not from_number.startswith("whatsapp:"):
            from_number = f"whatsapp:{normalize_alert_phone_number(from_number)}"
        if not str(to_number).startswith("whatsapp:"):
            to_number = f"whatsapp:{to_number}"
    else:
        from_number = normalize_alert_phone_number(TWILIO_SMS_FROM)
        if not from_number:
            return False, "Twilio SMS sender is not configured."

    auth_token = b64encode(f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode("utf-8")
    response = fetch_json(
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
        method="POST",
        headers={"Authorization": f"Basic {auth_token}", "Accept": "application/json"},
        form_body={"To": to_number, "From": from_number, "Body": str(message or "").strip()},
    )
    if response and response.get("sid"):
        return True, None

    error_message = ""
    if isinstance(response, dict):
        error_message = str(response.get("message") or response.get("detail") or "").strip()
    return False, error_message or f"Twilio API request failed for {label}."


def normalize_alert_phone_number(raw_phone):
    raw_text = str(raw_phone or "").strip()
    if not raw_text:
        return ""
    if raw_text.startswith("whatsapp:"):
        return raw_text

    prefix = "+" if raw_text.startswith("+") else ""
    digits_only = re.sub(r"\D+", "", raw_text)
    if not digits_only:
        return ""
    if not prefix and len(digits_only) == 10:
        digits_only = f"91{digits_only}"
        prefix = "+"
    if not prefix:
        prefix = "+"
    return f"{prefix}{digits_only}"


def build_alert_email_text(alert, user):
    farm_label = getattr(user, "location", "") or "your farm"
    return (
        f"{APP_DISPLAY_NAME} Alert\n\n"
        f"{alert.title}\n\n"
        f"{alert.detail}\n\n"
        f"Farm context: {farm_label}\n"
        f"Open: {alert.action_url or '/alerts'}\n\n"
        f"- Team {APP_DISPLAY_NAME}\n"
    )


def send_alert_email(user, preferences, alert):
    target_email = str(getattr(preferences, "alert_email", "") or getattr(user, "email", "") or "").strip()
    if not target_email:
        return False, "Alert email address is missing."
    subject = f"{APP_DISPLAY_NAME} Alert: {alert.title}"
    return send_email_content(
        target_email,
        subject,
        build_alert_email_text(alert, user),
        label="alert email",
    )


def build_alert_phone_message(alert):
    return f"{APP_DISPLAY_NAME}: {alert.title}. {alert.detail}"


def send_alert_phone_message(user, preferences, alert):
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return False, "Twilio credentials are not configured."

    to_number = normalize_alert_phone_number(getattr(preferences, "alert_phone", "") or getattr(user, "phone", ""))
    if not to_number:
        return False, "Alert phone number is missing."

    from_number = TWILIO_WHATSAPP_FROM if TWILIO_USE_WHATSAPP and TWILIO_WHATSAPP_FROM else TWILIO_SMS_FROM
    if not from_number:
        return False, "Twilio sender number is not configured."

    if TWILIO_USE_WHATSAPP:
        if not str(from_number).startswith("whatsapp:"):
            from_number = f"whatsapp:{normalize_alert_phone_number(from_number)}"
        if not str(to_number).startswith("whatsapp:"):
            to_number = f"whatsapp:{to_number}"
    else:
        from_number = normalize_alert_phone_number(from_number)

    form_body = {"To": to_number, "From": from_number}
    if TWILIO_CONTENT_SID:
        form_body["ContentSid"] = TWILIO_CONTENT_SID
        form_body["ContentVariables"] = json.dumps(
            {"1": alert.title[:60], "2": (alert.detail or "")[:120]}
        )
    else:
        form_body["Body"] = build_alert_phone_message(alert)

    auth_token = b64encode(f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode("utf-8")
    response = fetch_json(
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
        method="POST",
        headers={"Authorization": f"Basic {auth_token}", "Accept": "application/json"},
        form_body=form_body,
    )
    if response and response.get("sid"):
        return True, None

    error_message = ""
    if isinstance(response, dict):
        error_message = str(response.get("message") or response.get("detail") or "").strip()
    return False, error_message or "Twilio API request failed."


def build_order_amount_label(order):
    amount_inr = int(getattr(order, "amount", 0) or 0) / 100.0
    currency = str(getattr(order, "currency", RAZORPAY_CURRENCY) or RAZORPAY_CURRENCY)
    return f"{currency} {amount_inr:.2f}"


def get_order_product_name(order, product=None):
    direct_product = product or getattr(order, "product", None)
    product_name = str(getattr(direct_product, "name", "") or "").strip()
    if product_name:
        return product_name
    notes = get_order_notes(order)
    return str(notes.get("product_name") or "Store Product").strip() or "Store Product"


def send_user_order_email(order, user, product=None, event_type="placed"):
    if user is None:
        return False, "User is missing."

    target_email = str(getattr(user, "email", "") or "").strip()
    if not target_email:
        return False, "User email is missing."

    event_key = str(event_type or "placed").strip().lower()
    event_copy = {
        "placed": {
            "subject": f"{APP_DISPLAY_NAME} order placed #{getattr(order, 'id', '')}",
            "headline": "Your order has been placed successfully.",
            "detail": "Our team has received your order and it is waiting for confirmation.",
        },
        "confirmed": {
            "subject": f"{APP_DISPLAY_NAME} order confirmed #{getattr(order, 'id', '')}",
            "headline": "Your order has been confirmed by the admin team.",
            "detail": "We have started preparing your order for delivery.",
        },
        "delivered": {
            "subject": f"{APP_DISPLAY_NAME} order delivered #{getattr(order, 'id', '')}",
            "headline": "Your order has been marked as delivered.",
            "detail": "If you have not received the product yet, please contact support immediately.",
        },
    }.get(event_key)
    if event_copy is None:
        return False, "Unsupported order event."

    body_lines = [
        f"Hello {str(getattr(user, 'name', '') or 'Farmer').strip()},",
        "",
        event_copy["headline"],
        event_copy["detail"],
        "",
        f"Order ID: #{getattr(order, 'id', '-')}",
        f"Product: {get_order_product_name(order, product)}",
        f"Amount: {build_order_amount_label(order)}",
        f"Payment status: {str(getattr(order, 'status', 'created') or 'created').title()}",
        f"Fulfillment status: {get_fulfillment_status(order).title()}",
        "Track order: /track-order",
        "",
        f"- Team {APP_DISPLAY_NAME}",
    ]
    return send_basic_email(
        target_email,
        event_copy["subject"],
        body_lines,
        label="user order email",
    )


def send_task_status_email(user, task, event_type="created"):
    if user is None:
        return False, "User is missing."

    target_email = str(getattr(user, "email", "") or "").strip()
    if not target_email:
        return False, "User email is missing."

    event_key = str(event_type or "created").strip().lower()
    event_copy = {
        "created": {
            "subject": f"{APP_DISPLAY_NAME} task created: {task.title}",
            "headline": "A new task was added to your farm planner.",
            "detail": "Review the task details and keep it on schedule.",
        },
        "started": {
            "subject": f"{APP_DISPLAY_NAME} task started: {task.title}",
            "headline": "Your task is now marked as in progress.",
            "detail": "Complete it after the field work is finished to keep reminders accurate.",
        },
        "completed": {
            "subject": f"{APP_DISPLAY_NAME} task completed: {task.title}",
            "headline": "Great work. This task is now marked as completed.",
            "detail": "The planner has been updated and pending reminders for this task are cleared.",
        },
        "reminder": {
            "subject": f"{APP_DISPLAY_NAME} reminder: complete {task.title}",
            "headline": "This task is still in progress and needs your attention.",
            "detail": "Please mark it complete once the work is done so you keep receiving the right updates.",
        },
    }.get(event_key)
    if event_copy is None:
        return False, "Unsupported task event."

    farm_label = getattr(getattr(task, "farm", None), "name", "") or "All farms"
    due_label = format_task_due_label(task)
    body_lines = [
        f"Hello {str(getattr(user, 'name', '') or 'Farmer').strip()},",
        "",
        event_copy["headline"],
        event_copy["detail"],
        "",
        f"Task: {task.title}",
        f"Farm: {farm_label}",
        f"Category: {getattr(task, 'category', '') or 'General'}",
        f"Priority: {str(getattr(task, 'priority', 'medium') or 'medium').title()}",
        f"Status: {str(getattr(task, 'status', 'todo') or 'todo').replace('_', ' ').title()}",
        f"Due: {due_label}",
        "Planner: /farms#task-planner",
        "",
        f"- Team {APP_DISPLAY_NAME}",
    ]
    return send_basic_email(
        target_email,
        event_copy["subject"],
        body_lines,
        label="task status email",
    )


def build_order_status_alert_key(order, event_type):
    order_id = getattr(order, "id", order)
    return f"order-status-{str(event_type or 'placed').strip().lower()}-{int(order_id)}"


def upsert_order_status_alert(user, order, product=None, event_type="placed", commit=True):
    if user is None or order is None:
        return None

    event_key = str(event_type or "placed").strip().lower()
    alert_copy = {
        "placed": {
            "title": f"Order placed: #{getattr(order, 'id', '-')}",
            "detail": f"{get_order_product_name(order, product)} payment is received and admin confirmation is pending.",
            "action_url": "/track-order",
        },
        "confirmed": {
            "title": f"Order confirmed: #{getattr(order, 'id', '-')}",
            "detail": f"{get_order_product_name(order, product)} has been confirmed and is moving toward delivery.",
            "action_url": "/track-order",
        },
        "delivered": {
            "title": f"Order delivered: #{getattr(order, 'id', '-')}",
            "detail": f"{get_order_product_name(order, product)} has been marked as delivered.",
            "action_url": "/track-order",
        },
    }.get(event_key)
    if alert_copy is None:
        return None

    alert_key = build_order_status_alert_key(order, event_key)
    alert = AlertRecord.query.filter_by(user_id=user.id, alert_key=alert_key).first()
    if alert is None:
        alert = AlertRecord(user_id=user.id, alert_key=alert_key)  # type: ignore
        db.session.add(alert)

    alert.category = "order"
    alert.severity = "insight"
    alert.title = alert_copy["title"]
    alert.detail = alert_copy["detail"]
    alert.action_url = alert_copy["action_url"]
    alert.is_active = True
    alert.is_read = False
    alert.last_notified_at = datetime.now(timezone.utc)

    if commit:
        db.session.commit()
    return alert


def build_task_reminder_alert_key(task_or_id):
    task_id = getattr(task_or_id, "id", task_or_id)
    return f"task-reminder-{int(task_id)}"


def build_task_reminder_detail(task):
    farm_label = "All farms"
    farm_id = getattr(task, "farm_id", None)
    if farm_id:
        with db.session.no_autoflush:
            farm = Farm.query.filter_by(id=farm_id).first()
        if farm and getattr(farm, "name", ""):
            farm_label = farm.name
    due_label = format_task_due_label(task)
    category_label = (getattr(task, "category", "") or "General").strip() or "General"
    task_status = (getattr(task, "status", "") or "todo").strip().lower()
    detail_parts = [
        (
            f"'{task.title}' is still in progress in your farm planner."
            if task_status == "in_progress"
            else f"'{task.title}' is still pending in your farm planner."
        ),
        f"Farm: {farm_label}.",
        f"Category: {category_label}.",
    ]
    if due_label and due_label != "No deadline":
        detail_parts.append(f"Timeline: {due_label}.")
    detail_parts.append("Mark it done once the field work is completed.")
    return " ".join(detail_parts)


def deactivate_task_reminder_alert(task_id, user_id=None, commit=True):
    query = AlertRecord.query.filter_by(alert_key=build_task_reminder_alert_key(task_id))
    if user_id is not None:
        query = query.filter_by(user_id=user_id)
    alert = query.first()
    if alert is None:
        return None
    alert.is_active = False
    alert.is_read = True
    if commit:
        db.session.commit()
    return alert


def upsert_task_reminder_alert(task, force_notify=False, commit=True, send_channels=True):
    if task is None:
        return None

    if (getattr(task, "status", "") or "").strip().lower() == "done":
        return deactivate_task_reminder_alert(task.id, user_id=task.user_id, commit=commit)

    user = User.query.filter_by(id=task.user_id).first()
    if user is None:
        return None

    preferences = get_or_create_user_preferences(user, commit=False)
    now = datetime.now(timezone.utc)
    alert_key = build_task_reminder_alert_key(task)
    alert_title = f"Task reminder: {task.title}"
    alert_detail = build_task_reminder_detail(task)
    alert_action_url = "/farms#task-planner"
    alert = AlertRecord.query.filter_by(user_id=user.id, alert_key=alert_key).first()
    is_new = alert is None
    if alert is None:
        alert = AlertRecord(user_id=user.id, alert_key=alert_key)  # type: ignore

    previous_signature = (
        getattr(alert, "title", ""),
        getattr(alert, "detail", ""),
        getattr(alert, "action_url", ""),
        bool(getattr(alert, "is_active", False)),
    )

    alert.farm_id = task.farm_id
    alert.category = "task"
    alert.severity = "insight"
    alert.title = alert_title
    alert.detail = alert_detail
    alert.action_url = alert_action_url
    alert.is_active = True
    if is_new:
        db.session.add(alert)

    current_signature = (
        alert.title,
        alert.detail,
        alert.action_url,
        bool(alert.is_active),
    )
    became_active_or_changed = is_new or previous_signature != current_signature
    if became_active_or_changed or force_notify:
        alert.is_read = False

    last_notified_at = normalize_timestamp(getattr(alert, "last_notified_at", None))
    should_notify = force_notify or became_active_or_changed
    if not should_notify:
        if last_notified_at is None:
            should_notify = True
        else:
            should_notify = (now - last_notified_at).total_seconds() >= TASK_REMINDER_INTERVAL_SECONDS

    if should_notify:
        if send_channels:
            email_sent = False
            sms_sent = False
            if (getattr(task, "status", "") or "").strip().lower() == "in_progress":
                email_sent, _ = send_task_status_email(user, task, "reminder")
            elif getattr(preferences, "email_alerts", False):
                email_sent, _ = send_alert_email(user, preferences, alert)
            if getattr(preferences, "sms_alerts", False):
                sms_sent, _ = send_alert_phone_message(user, preferences, alert)
            alert.email_sent = bool(alert.email_sent or email_sent)
            alert.sms_sent = bool(alert.sms_sent or sms_sent)
        alert.last_notified_at = now

    if commit:
        db.session.commit()
    return alert


def process_open_task_reminders():
    active_keys = set()
    open_tasks = FarmTask.query.filter(FarmTask.status != "done").all()
    for task in open_tasks:
        active_keys.add(build_task_reminder_alert_key(task))
        upsert_task_reminder_alert(task, force_notify=False, commit=False)

    active_reminder_alerts = AlertRecord.query.filter(
        AlertRecord.is_active.is_(True),
        AlertRecord.alert_key.like("task-reminder-%"),
    ).all()
    for alert in active_reminder_alerts:
        if alert.alert_key not in active_keys:
            alert.is_active = False
            alert.is_read = True

    db.session.commit()


def task_reminder_worker_loop():
    while True:
        try:
            with app.app_context():
                process_open_task_reminders()
        except Exception as exc:
            print(f"Task reminder worker error: {exc}")
        time.sleep(TASK_REMINDER_POLL_SECONDS)


@app.before_request
def ensure_task_reminder_worker_started():
    if app.config.get("TESTING"):
        return
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    if TASK_REMINDER_WORKER.get("started"):
        return

    with TASK_REMINDER_LOCK:
        if TASK_REMINDER_WORKER.get("started"):
            return
        worker = threading.Thread(target=task_reminder_worker_loop, name="task-reminder-worker", daemon=True)
        worker.start()
        TASK_REMINDER_WORKER["thread"] = worker
        TASK_REMINDER_WORKER["started"] = True


def build_alert_history_chart(alert_records, days=7):
    today = datetime.now(timezone.utc).date()
    counts_by_day = {}
    for record in alert_records:
        record_time = normalize_timestamp(getattr(record, "updated_at", None) or getattr(record, "created_at", None))
        if record_time is None:
            continue
        day_key = record_time.astimezone(timezone.utc).date()
        counts_by_day[day_key] = counts_by_day.get(day_key, 0) + 1

    labels = []
    values = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        labels.append(day.strftime("%d %b"))
        values.append(int(counts_by_day.get(day, 0)))
    return {"labels": labels, "values": values}


def serialize_alert_record(record):
    severity = str(getattr(record, "severity", "") or "insight").strip().lower()
    if severity not in {"heat", "rain", "disease", "insight"}:
        severity = "insight"

    return {
        "id": record.id,
        "severity": severity,
        "title": getattr(record, "title", "") or "Farm alert",
        "detail": getattr(record, "detail", "") or "Review this alert for more details.",
        "time_ago": format_relative_time(getattr(record, "updated_at", None) or getattr(record, "created_at", None)),
        "action_url": getattr(record, "action_url", "") or "/alerts",
        "is_read": bool(getattr(record, "is_read", False)),
        "open_url": f"/alerts/{record.id}/open",
    }


def build_user_alert_candidates(user, preferences, primary_farm, weather, soil, crop_health, task_summary):
    crop_label = normalize_kisan_dost_crop_name(getattr(user, "crop_type", "") or "crop")
    location_label = (
        (getattr(primary_farm, "location", "") or "").strip()
        or (getattr(user, "location", "") or "").strip()
        or "your farm"
    )
    farm_id = getattr(primary_farm, "id", None)
    candidates = []

    def add_candidate(alert_key, category, severity, title, detail, action_url):
        candidates.append(
            {
                "alert_key": alert_key,
                "category": category,
                "severity": severity,
                "title": title,
                "detail": detail,
                "action_url": action_url,
                "farm_id": farm_id,
            }
        )

    if getattr(preferences, "weather_alerts", False) and float(weather.get("temp", 0) or 0) >= 34:
        add_candidate(
            "weather-heat",
            "weather",
            "heat",
            "Heat stress risk",
            f"Temperature near {location_label} is around {weather['temp']} C. Shift irrigation and field work to cooler hours.",
            "/weather",
        )

    if getattr(preferences, "weather_alerts", False) and (
        float(weather.get("rainfall_mm", 0) or 0) >= 6 or float(weather.get("humidity", 0) or 0) >= 88
    ):
        add_candidate(
            "weather-rain",
            "weather",
            "rain",
            "Rain and waterlogging alert",
            f"Moisture levels are rising around {location_label}. Check field drainage before the next irrigation cycle.",
            "/weather",
        )

    if getattr(preferences, "disease_alerts", False) and (
        float(weather.get("humidity", 0) or 0) >= 78 and (
            float(weather.get("rainfall_mm", 0) or 0) >= 3 or float(weather.get("clouds", 0) or 0) >= 60
        )
    ):
        add_candidate(
            "disease-risk",
            "disease",
            "disease",
            "Disease pressure rising",
            f"Humidity and moisture can increase fungal disease risk for {crop_label} near {location_label}. Inspect affected leaves closely.",
            "/disease-detection",
        )

    if getattr(preferences, "crop_alerts", False) and int(soil.get("nitrogen", 0) or 0) < 45:
        add_candidate(
            "soil-low-nitrogen",
            "soil",
            "insight",
            "Low nitrogen detected",
            f"Soil nitrogen is trending low for {crop_label}. Plan a nutrient top-up before the next growth stage.",
            "/soil-health",
        )

    soil_ph = float(soil.get("ph", 6.5) or 6.5)
    if getattr(preferences, "crop_alerts", False) and (soil_ph < 6.0 or soil_ph > 7.5):
        add_candidate(
            "soil-ph-imbalance",
            "soil",
            "insight",
            "Soil pH imbalance",
            f"Soil pH is currently {soil_ph}. Review soil corrections before applying the next input mix.",
            "/soil-health",
        )

    if getattr(preferences, "crop_alerts", False) and int(crop_health.get("score", 80) or 80) < 60:
        add_candidate(
            "crop-health-drop",
            "crop",
            "insight",
            "Crop health needs attention",
            f"{crop_label} health score is {crop_health['score']}%. Review crop stress indicators and recent field conditions.",
            "/crop-monitoring",
        )

    overdue_tasks = [item for item in task_summary.get("all", []) if item.get("overdue")]
    if overdue_tasks:
        add_candidate(
            "task-overdue",
            "task",
            "insight",
            "Planner task overdue",
            f"{len(overdue_tasks)} planner task(s) are overdue. Clear urgent field work before it affects crop timing.",
            "/farms#task-planner",
        )

    due_today_count = int(task_summary.get("due_today_count", 0) or 0)
    if due_today_count:
        add_candidate(
            "task-due-today",
            "task",
            "insight",
            "Task due today",
            f"{due_today_count} planner task(s) need attention today. Review irrigation, spray, or inspection work now.",
            "/farms#task-planner",
        )

    return candidates


def sync_user_alerts(user):
    preferences = get_or_create_user_preferences(user)
    primary_farm, _ = ensure_user_farm_setup(user)
    location_label = (
        (getattr(primary_farm, "location", "") or "").strip()
        or (getattr(user, "location", "") or "").strip()
        or "Bhubaneswar"
    )
    weather = fetch_weather_bundle(location_label)
    soil = build_soil_profile(user, weather)
    crop_health = build_crop_health(user, weather, soil)
    task_summary = build_task_summary(user, limit=24)
    candidates = build_user_alert_candidates(user, preferences, primary_farm, weather, soil, crop_health, task_summary)
    active_keys = set()
    now = datetime.now(timezone.utc)

    for candidate in candidates:
        active_keys.add(candidate["alert_key"])
        alert = AlertRecord.query.filter_by(user_id=user.id, alert_key=candidate["alert_key"]).first()
        is_new = alert is None
        if alert is None:
            alert = AlertRecord(user_id=user.id, alert_key=candidate["alert_key"])  # type: ignore
            db.session.add(alert)

        previous_signature = (
            getattr(alert, "severity", ""),
            getattr(alert, "title", ""),
            getattr(alert, "detail", ""),
            getattr(alert, "action_url", ""),
            bool(getattr(alert, "is_active", False)),
        )

        alert.farm_id = candidate["farm_id"]
        alert.category = candidate["category"]
        alert.severity = candidate["severity"]
        alert.title = candidate["title"]
        alert.detail = candidate["detail"]
        alert.action_url = candidate["action_url"]
        alert.is_active = True

        current_signature = (
            alert.severity,
            alert.title,
            alert.detail,
            alert.action_url,
            bool(alert.is_active),
        )
        became_active_or_changed = is_new or previous_signature != current_signature
        if became_active_or_changed:
            alert.is_read = False

        email_sent = False
        sms_sent = False
        if became_active_or_changed:
            if getattr(preferences, "email_alerts", False):
                email_sent, _ = send_alert_email(user, preferences, alert)
            if getattr(preferences, "sms_alerts", False):
                sms_sent, _ = send_alert_phone_message(user, preferences, alert)
            if email_sent or sms_sent:
                alert.last_notified_at = now
            alert.email_sent = bool(alert.email_sent or email_sent)
            alert.sms_sent = bool(alert.sms_sent or sms_sent)

    for stale_alert in AlertRecord.query.filter_by(user_id=user.id, is_active=True).all():
        if str(getattr(stale_alert, "alert_key", "") or "").startswith("task-reminder-"):
            continue
        if str(getattr(stale_alert, "alert_key", "") or "").startswith("order-status-"):
            continue
        if stale_alert.alert_key not in active_keys:
            stale_alert.is_active = False

    db.session.commit()
    return {
        "preferences": preferences,
        "weather": weather,
        "soil": soil,
        "crop_health": crop_health,
        "task_summary": task_summary,
        "active_alerts": AlertRecord.query.filter_by(user_id=user.id, is_active=True).order_by(AlertRecord.updated_at.desc()).all(),
        "history_alerts": AlertRecord.query.filter_by(user_id=user.id).order_by(AlertRecord.updated_at.desc()).limit(12).all(),
    }


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


def format_wallet_reason_label(reason):
    labels = {
        "referral_bonus": "Referral bonus received",
        "referral_signup_bonus": "Welcome bonus added",
        "subscription_wallet_applied": "Wallet used for subscription",
    }
    normalized = str(reason or "").strip()
    if normalized in labels:
        return labels[normalized]
    if not normalized:
        return "Wallet activity"
    return normalized.replace("_", " ").strip().title()


def build_village_module_context(user, module_key):
    primary_farm = (
        Farm.query.filter_by(user_id=user.id, is_primary=True).order_by(Farm.created_at.asc()).first()
        or Farm.query.filter_by(user_id=user.id).order_by(Farm.created_at.asc()).first()
    )
    farm_count = Farm.query.filter_by(user_id=user.id).count()
    task_summary = build_task_summary(user, limit=4)
    recent_activity = build_recent_activity(user, limit=5)
    preferences = UserPreference.query.filter_by(user_id=user.id).first()
    recent_scan_count = DiseaseHistory.query.filter_by(user_id=user.id).count()
    active_product_count = StoreProduct.query.filter_by(is_active=True).count()
    wallet_balance = int(user.wallet_balance or 0)
    loyalty_points = int(user.loyalty_points or 0)
    current_plan_key = normalize_plan_name(user.plan)
    current_plan = SUBSCRIPTION_PLANS.get(current_plan_key, SUBSCRIPTION_PLANS["free"])
    alert_count = sum(
        [
            1 if getattr(preferences, "crop_alerts", False) else 0,
            1 if getattr(preferences, "disease_alerts", False) else 0,
            1 if getattr(preferences, "weather_alerts", False) else 0,
            1 if getattr(preferences, "data_updates", False) else 0,
        ]
    )
    channel_count = sum(
        [
            1 if getattr(preferences, "email_alerts", False) else 0,
            1 if getattr(preferences, "sms_alerts", False) else 0,
            1 if getattr(preferences, "daily_briefing", False) else 0,
        ]
    )

    wallet_feed = []
    wallet_transactions = (
        WalletTransaction.query.filter_by(user_id=user.id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(6)
        .all()
    )
    for tx in wallet_transactions:
        amount = int(tx.amount_inr or 0)
        direction = str(tx.direction or "credit").strip().lower()
        wallet_feed.append(
            {
                "title": format_wallet_reason_label(tx.reason),
                "detail": "Added to your balance." if direction == "credit" else "Used from your balance.",
                "meta": format_relative_time(tx.created_at),
                "badge": f"{'+' if direction == 'credit' else '-'}Rs {amount}",
                "badge_tone": "positive" if direction == "credit" else "neutral",
            }
        )
    if not wallet_feed:
        wallet_feed.append(
            {
                "title": "No wallet activity yet",
                "detail": "Referral rewards and subscription credits will appear here.",
                "meta": "Start with Refer and Earn",
                "badge": f"Rs {wallet_balance}",
                "badge_tone": "neutral",
            }
        )

    notification_feed = []
    recent_alerts = AlertRecord.query.filter_by(user_id=user.id).order_by(AlertRecord.updated_at.desc()).limit(4).all()
    for alert in recent_alerts:
        serialized = serialize_alert_record(alert)
        notification_feed.append(
            {
                "title": serialized["title"],
                "detail": serialized["detail"],
                "meta": serialized["time_ago"],
                "badge": "New" if not serialized["is_read"] else "Alert",
                "badge_tone": "positive" if not serialized["is_read"] else "neutral",
            }
        )

    for item in recent_activity:
        if len(notification_feed) >= 4:
            break
        notification_feed.append(
            {
                "title": item["title"],
                "detail": item["detail"],
                "meta": item["time_ago"],
                "badge": str(item["tone"]).replace("_", " ").title(),
                "badge_tone": "neutral",
            }
        )
    if not notification_feed:
        notification_feed.append(
            {
                "title": "Notification center is ready",
                "detail": "Weather, disease, and farm task updates will be listed here.",
                "meta": "Connect alerts in settings",
                "badge": "Ready",
                "badge_tone": "positive",
            }
        )

    module_map = {
        "rent_tractor": {
            "active_page": "rent_tractor",
            "title": "Rent a Tractor",
            "badge": "Mechanization on demand",
            "description": "Plan field work faster with tractor, tiller, seed drill, and spraying support around your farm cluster.",
            "stats": [
                {"label": "Primary farm", "value": primary_farm.name if primary_farm else "Setup pending"},
                {"label": "Open tasks", "value": str(task_summary["open_count"])},
                {"label": "Location", "value": user.location or "Rural zone"},
            ],
            "panel_title": "Suggested setup",
            "panel_text": "Use your farm planner to mark land prep, sowing, or spray tasks first, then confirm machine timing from one place.",
            "actions": [
                {"label": "Open Farms", "href": "/farms"},
                {"label": "View Tools", "href": "/tools"},
            ],
            "cards": [
                {"icon": "fa-tractor", "title": "Quick machine booking", "detail": "Reserve tractors, cultivators, and rotavators for sowing, tilling, and residue management."},
                {"icon": "fa-clock", "title": "Hourly slot planning", "detail": "Match machine timing with your next open farm task to avoid labor delays."},
                {"icon": "fa-screwdriver-wrench", "title": "Equipment support", "detail": "Track which machine type fits land prep, spraying, or harvest support before dispatch."},
            ],
        },
        "land_lease": {
            "active_page": "land_lease",
            "title": "Land Lease",
            "badge": "Flexible acreage access",
            "description": "Organize short-term and seasonal land partnerships with clearer plot details, cropping intent, and contact readiness.",
            "stats": [
                {"label": "Farm count", "value": str(farm_count)},
                {"label": "Target crop", "value": user.crop_type or "Mixed crop"},
                {"label": "Farm size", "value": user.farm_size or "Flexible"},
            ],
            "panel_title": "Lease checklist",
            "panel_text": "Keep location, plot size, crop type, and contact number updated so land opportunities can be reviewed faster.",
            "actions": [
                {"label": "Update Profile", "href": "/profile"},
                {"label": "Manage Farms", "href": "/farms"},
            ],
            "cards": [
                {"icon": "fa-map", "title": "Plot listing readiness", "detail": "Capture location, acreage, and irrigation notes before you offer or request a lease."},
                {"icon": "fa-file-signature", "title": "Seasonal agreements", "detail": "Prepare simple lease terms for kharif, rabi, or mixed-cycle cultivation planning."},
                {"icon": "fa-people-arrows", "title": "Farmer-to-farmer matching", "detail": "Coordinate with nearby growers and village contacts for temporary land access."},
            ],
        },
        "rural_services": {
            "active_page": "rural_services",
            "title": "Rural Services",
            "badge": "Field support hub",
            "description": "Bundle practical services like soil testing, irrigation repair, spraying help, and local field assistance into one place.",
            "stats": [
                {"label": "Service lanes", "value": "6"},
                {"label": "Open support needs", "value": str(task_summary["open_count"])},
                {"label": "Village base", "value": user.location or "Service region"},
            ],
            "panel_title": "What to coordinate first",
            "panel_text": "Start with the field issue that is slowing work today, then assign the right service category and timing.",
            "actions": [
                {"label": "Open Settings", "href": "/settings"},
                {"label": "Farm Planner", "href": "/farms#task-planner"},
            ],
            "cards": [
                {"icon": "fa-flask-vial", "title": "Soil and water testing", "detail": "Track testing support for pH, nutrients, water source quality, and follow-up action notes."},
                {"icon": "fa-faucet-drip", "title": "Irrigation assistance", "detail": "Coordinate repair and setup requests for pumps, pipes, and flow distribution across plots."},
                {"icon": "fa-users-gear", "title": "On-field manpower", "detail": "Organize spraying, weeding, harvesting, and transport support when labor demand spikes."},
            ],
        },
        "govt_schemes": {
            "active_page": "govt_schemes",
            "title": "Govt Schemes",
            "badge": "Benefits and eligibility",
            "description": "Keep farmer profile details ready for subsidy discovery, seasonal support programs, and application reminders.",
            "stats": [
                {"label": "Current location", "value": user.location or "Not set"},
                {"label": "Profile status", "value": "Ready" if user.phone and user.email else "Needs update"},
                {"label": "Preferred crop", "value": user.crop_type or "General farming"},
            ],
            "panel_title": "Application readiness",
            "panel_text": "Accurate phone, email, crop, and location details make it easier to surface the right scheme pathway.",
            "actions": [
                {"label": "Open Govt Buddy AI", "href": "/govt-buddy-ai"},
                {"label": "Edit Profile", "href": "/profile"},
            ],
            "cards": [
                {"icon": "fa-building-columns", "title": "Scheme discovery", "detail": "Review support options for equipment, crop insurance, irrigation, and input assistance."},
                {"icon": "fa-id-card", "title": "Eligibility checklist", "detail": "Track which farmer details and documents should be complete before submission."},
                {"icon": "fa-calendar-check", "title": "Reminder flow", "detail": "Keep submission windows, verification steps, and follow-up actions visible in one module."},
            ],
        },
        "money_manager": {
            "active_page": "money_manager",
            "title": "Money Manager",
            "badge": "Farm finance overview",
            "description": "Monitor seasonal spending, wallet credits, and upgrade costs so cash planning stays visible alongside farm work.",
            "stats": [
                {"label": "Wallet balance", "value": f"Rs {wallet_balance}"},
                {"label": "Loyalty points", "value": str(loyalty_points)},
                {"label": "Current plan", "value": current_plan['label']},
            ],
            "panel_title": "Keep finance simple",
            "panel_text": "Use wallet credits, referral bonuses, and plan pricing together so monthly farm tools stay affordable.",
            "actions": [
                {"label": "Open My Wallet", "href": "/my-wallet"},
                {"label": "Upgrade Hub", "href": "/upgrade-hub"},
            ],
            "cards": [
                {"icon": "fa-wallet", "title": "Cashflow snapshot", "detail": "See how wallet credits and monthly plan fees affect your digital farming budget."},
                {"icon": "fa-chart-line", "title": "Expense planning", "detail": "Prepare for seeds, inputs, repairs, and services before the next major farm cycle starts."},
                {"icon": "fa-hand-holding-dollar", "title": "Savings opportunities", "detail": "Combine referrals, plan perks, and market offers to reduce repeat spending."},
            ],
        },
        "ai_crop_scan": {
            "active_page": "ai_crop_scan",
            "title": "AI Crop Scan",
            "badge": "Leaf photo diagnosis",
            "description": "Run image-based crop checks, compare symptom patterns, and jump directly into treatment guidance from one scan flow.",
            "stats": [
                {"label": "Scans completed", "value": str(recent_scan_count)},
                {"label": "Focus crop", "value": user.crop_type or "General crop"},
                {"label": "Best next step", "value": "Upload leaf"},
            ],
            "panel_title": "Start with one clear photo",
            "panel_text": "A close leaf image in natural light gives the disease engine a stronger signal and cleaner recommendation trail.",
            "actions": [
                {"label": "Open Live Scan", "href": "/disease-detection"},
                {"label": "Crop Library", "href": "/crop-library"},
            ],
            "cards": [
                {"icon": "fa-camera", "title": "Fast leaf analysis", "detail": "Upload crop photos and check likely disease patterns with AI-supported matching."},
                {"icon": "fa-stethoscope", "title": "Symptom guidance", "detail": "Compare disease signs, possible causes, and treatment direction before field action."},
                {"icon": "fa-seedling", "title": "Crop-specific context", "detail": "Connect each scan with crop type and local field conditions for better follow-up."},
            ],
        },
        "farming_solutions": {
            "active_page": "farming_solutions",
            "title": "Farming Solutions",
            "badge": "Integrated farm actions",
            "description": "Bring weather, soil, crop monitoring, and AI suggestions together so farm decisions feel connected instead of scattered.",
            "stats": [
                {"label": "Primary crop", "value": user.crop_type or "Farm mix"},
                {"label": "Farm records", "value": str(farm_count)},
                {"label": "Open actions", "value": str(task_summary["open_count"])},
            ],
            "panel_title": "One workflow, multiple signals",
            "panel_text": "Use the existing monitoring modules together to turn weather and field signals into practical work plans.",
            "actions": [
                {"label": "Weather", "href": "/weather"},
                {"label": "Soil Health", "href": "/soil-health"},
                {"label": "AI Insights", "href": "/ai-insights"},
            ],
            "cards": [
                {"icon": "fa-cloud-sun", "title": "Weather-led planning", "detail": "Check rain, heat, and humidity before irrigation, spraying, or transplanting work."},
                {"icon": "fa-leaf", "title": "Crop health monitoring", "detail": "Use monitoring tools and alerts to review plant stress and farm-level changes faster."},
                {"icon": "fa-brain", "title": "AI-backed recommendations", "detail": "Combine dashboard insights with next-step suggestions tailored to your active crop cycle."},
            ],
        },
        "agri_market": {
            "active_page": "agri_market",
            "title": "Agro Market",
            "badge": "Input and demand signals",
            "description": "Browse farm products, track mandi momentum, and connect market visibility with the buying decisions already in your app.",
            "stats": [
                {"label": "Active products", "value": str(active_product_count)},
                {"label": "Buyer location", "value": user.location or "India"},
                {"label": "Wallet ready", "value": f"Rs {wallet_balance}"},
            ],
            "panel_title": "Market workflow",
            "panel_text": "Use the store for immediate input buying, then compare product choices with your current crop stage and farm tasks.",
            "actions": [
                {"label": "Open Market", "href": "/market"},
                {"label": "Community", "href": "/community"},
            ],
            "cards": [
                {"icon": "fa-store", "title": "Input marketplace", "detail": "Browse seeds, tools, and crop-care products already available inside your website."},
                {"icon": "fa-chart-column", "title": "Rate awareness", "detail": "Use mandi rate signals and field demand context to time purchasing decisions more clearly."},
                {"icon": "fa-truck-fast", "title": "Procurement readiness", "detail": "Prepare for product ordering, delivery flow, and farm-level purchase planning from one view."},
            ],
        },
        "govt_buddy_ai": {
            "active_page": "govt_buddy_ai",
            "title": "Govt Buddy AI",
            "badge": "Scheme helper assistant",
            "description": "Ask for scheme direction, document preparation, and farmer benefit guidance in a simpler, more guided format.",
            "stats": [
                {"label": "Eligible crop region", "value": user.location or "Not selected"},
                {"label": "Alert readiness", "value": str(alert_count)},
                {"label": "Support channel", "value": "AI assisted"},
            ],
            "panel_title": "How to use it",
            "panel_text": "Start with your crop, village, or support goal. The assistant can narrow which scheme path to review next.",
            "actions": [
                {"label": "AI Insights", "href": "/ai-insights"},
                {"label": "Govt Schemes", "href": "/govt-schemes"},
            ],
            "cards": [
                {"icon": "fa-comments", "title": "Question and answer flow", "detail": "Ask about subsidy options, application readiness, and next steps without scanning many pages."},
                {"icon": "fa-folder-open", "title": "Document preparation", "detail": "Review the common identity, land, and farm data details usually needed before you apply."},
                {"icon": "fa-language", "title": "Farmer-friendly guidance", "detail": "Keep explanations simple for multilingual support and easier scheme understanding."},
            ],
        },
        "my_wallet": {
            "active_page": "my_wallet",
            "title": "My Wallet",
            "badge": "Credits, rewards, and balance",
            "description": "Track referral bonuses, subscription usage, and available wallet balance without leaving your dashboard flow.",
            "stats": [
                {"label": "Available balance", "value": f"Rs {wallet_balance}"},
                {"label": "Loyalty points", "value": str(loyalty_points)},
                {"label": "Current plan", "value": current_plan['label']},
            ],
            "panel_title": "Ways to grow balance",
            "panel_text": "Referral bonuses and wallet-based subscription discounts are already connected to your account activity.",
            "actions": [
                {"label": "Refer and Earn", "href": "/refer-and-earn"},
                {"label": "Upgrade Hub", "href": "/upgrade-hub"},
            ],
            "cards": [
                {"icon": "fa-wallet", "title": "Live balance view", "detail": "See how much wallet credit is available before you pay for plans and eligible checkouts."},
                {"icon": "fa-gift", "title": "Referral rewards", "detail": "Referral signups can add bonus value that reduces future digital farming costs."},
                {"icon": "fa-clock-rotate-left", "title": "Usage history", "detail": "Review recent credit and debit activity to understand where wallet value moved."},
            ],
            "feed_title": "Recent wallet activity",
            "feed_entries": wallet_feed,
        },
        "notifications": {
            "active_page": "notifications",
            "title": "Notifications",
            "badge": "Farm updates and reminders",
            "description": "Keep weather alerts, disease notices, task reminders, and update preferences visible from one notification center.",
            "stats": [
                {"label": "Alert types on", "value": str(alert_count)},
                {"label": "Channels active", "value": str(channel_count)},
                {"label": "Open tasks", "value": str(task_summary["open_count"])},
            ],
            "panel_title": "Control what reaches you",
            "panel_text": "Review notification settings and the latest farm activity together so you do not miss weather or crop changes.",
            "actions": [
                {"label": "Open Alerts", "href": "/alerts"},
                {"label": "Notification Settings", "href": "/settings"},
            ],
            "cards": [
                {"icon": "fa-bell", "title": "Critical alert center", "detail": "Review weather, disease, and field-triggered updates from a single hub."},
                {"icon": "fa-list-check", "title": "Task reminders", "detail": "Stay aware of pending field jobs, due dates, and actions that need same-day attention."},
                {"icon": "fa-sliders", "title": "Delivery controls", "detail": "Adjust which alerts are sent by email, SMS, and daily briefing preferences."},
            ],
            "feed_title": "Recent activity feed",
            "feed_entries": notification_feed,
        },
        "upgrade_hub": {
            "active_page": "upgrade_hub",
            "title": "Upgrade Hub",
            "badge": "Plans, perks, and premium access",
            "description": "Compare plans, use wallet credits, and unlock more advanced AI tools from a dedicated upgrade surface.",
            "stats": [
                {"label": "Current plan", "value": current_plan['label']},
                {"label": "Wallet support", "value": f"Rs {wallet_balance}"},
                {"label": "Referral code", "value": user.referral_code or "Invite ready"},
            ],
            "panel_title": "Upgrade path",
            "panel_text": "Choose the right plan for disease scans, AI insights, and premium monitoring without losing track of wallet savings.",
            "actions": [
                {"label": "View Plans", "href": "/subscriptions"},
                {"label": "Refer and Earn", "href": "/refer-and-earn"},
            ],
            "cards": [
                {"icon": "fa-crown", "title": "Plan comparison", "detail": "Review Free, Pro, and Premium access levels with pricing and feature lift."},
                {"icon": "fa-bolt", "title": "Premium AI tools", "detail": "Unlock stronger AI workflows for scanning, insight generation, and advanced monitoring."},
                {"icon": "fa-wallet", "title": "Wallet-assisted upgrade", "detail": "Apply available wallet balance during eligible subscription checkout flows."},
            ],
        },
    }

    module_page = module_map.get(module_key)
    if module_page is None:
        abort(404)
    return module_page


def render_village_module_page(module_key):
    user = get_current_user()
    if not user:
        return redirect("/login")
    if module_key in DISABLED_DASHBOARD_MODULES:
        abort(404)
    module_page = build_village_module_context(user, module_key)
    return render_template("village_module.html", user=user, module=module_page)


def resolve_rent_tractor_map_center(location_label):
    location_text = str(location_label or "").strip().lower()
    location_map = [
        ("puri", {"lat": 19.8135, "lng": 85.8312, "zoom": 14}),
        ("bhubaneswar", {"lat": 20.2961, "lng": 85.8245, "zoom": 14}),
        ("cuttack", {"lat": 20.4625, "lng": 85.8828, "zoom": 13}),
        ("odisha", {"lat": 20.2961, "lng": 85.8245, "zoom": 13}),
    ]
    for keyword, payload in location_map:
        if keyword in location_text:
            return payload
    return {"lat": 19.8135, "lng": 85.8312, "zoom": 14}


def resolve_dashboard_map_center(location_label, weather=None):
    location_text = str(location_label or "").strip().lower()
    fallback_options = [
        ("delhi", {"lat": 28.6139, "lng": 77.2090, "zoom": 12}),
        ("new delhi", {"lat": 28.6139, "lng": 77.2090, "zoom": 12}),
        ("mumbai", {"lat": 19.0760, "lng": 72.8777, "zoom": 12}),
        ("kolkata", {"lat": 22.5726, "lng": 88.3639, "zoom": 12}),
        ("chennai", {"lat": 13.0827, "lng": 80.2707, "zoom": 12}),
        ("bengaluru", {"lat": 12.9716, "lng": 77.5946, "zoom": 12}),
        ("bangalore", {"lat": 12.9716, "lng": 77.5946, "zoom": 12}),
        ("hyderabad", {"lat": 17.3850, "lng": 78.4867, "zoom": 12}),
        ("pune", {"lat": 18.5204, "lng": 73.8567, "zoom": 12}),
        ("bhubaneswar", {"lat": 20.2961, "lng": 85.8245, "zoom": 13}),
        ("malkangiri", {"lat": 18.3646, "lng": 81.8880, "zoom": 12}),
        ("koraput", {"lat": 18.8110, "lng": 82.7105, "zoom": 12}),
        ("jeypore", {"lat": 18.8563, "lng": 82.5716, "zoom": 13}),
        ("rayagada", {"lat": 19.1712, "lng": 83.4160, "zoom": 12}),
        ("berhampur", {"lat": 19.3149, "lng": 84.7941, "zoom": 12}),
        ("ganjam", {"lat": 19.3871, "lng": 85.0502, "zoom": 11}),
        ("cuttack", {"lat": 20.4625, "lng": 85.8828, "zoom": 12}),
        ("odisha", {"lat": 20.2961, "lng": 85.8245, "zoom": 13}),
    ]
    fallback_center = next((payload for keyword, payload in fallback_options if keyword in location_text), None)
    if fallback_center is None:
        fallback_center = {"lat": 20.5937, "lng": 78.9629, "zoom": 5}
    weather_payload = weather if isinstance(weather, dict) else {}
    lat_value = weather_payload.get("lat")
    lon_value = weather_payload.get("lon")

    try:
        lat = float(lat_value)
    except (TypeError, ValueError):
        lat = None

    try:
        lng = float(lon_value)
    except (TypeError, ValueError):
        lng = None

    if lat is None or lng is None:
        geocoded_center = geocode_location_label(location_label)
        if geocoded_center is not None:
            return geocoded_center
        lat = float(fallback_center["lat"])
        lng = float(fallback_center["lng"])

    return {
        "lat": lat,
        "lng": lng,
        "zoom": int(fallback_center.get("zoom") or 14),
    }


def haversine_distance_km(lat1, lng1, lat2, lng2):
    lat1_rad = radians(float(lat1))
    lng1_rad = radians(float(lng1))
    lat2_rad = radians(float(lat2))
    lng2_rad = radians(float(lng2))
    delta_lat = lat2_rad - lat1_rad
    delta_lng = lng2_rad - lng1_rad
    arc = sin(delta_lat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lng / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(max(arc, 0)))


def estimate_travel_minutes(distance_km):
    return max(6, int(round(float(distance_km) * 3.4 + 6)))


def format_travel_minutes(minutes):
    if int(minutes) >= 60:
        hours = round(float(minutes) / 60, 1)
        return f"{hours:g} hr away"
    return f"{int(minutes)} min away"


def get_tractor_slot_options():
    today = datetime.now().date()
    labels = ["06:00 AM", "08:00 AM", "10:00 AM", "12:00 PM", "02:00 PM", "04:00 PM", "06:00 PM"]
    options = []
    for offset in range(3):
        day_value = today + timedelta(days=offset)
        for label in labels:
            options.append(
                {
                    "value": f"{day_value.isoformat()}|{label}",
                    "date": day_value.isoformat(),
                    "label": label,
                    "display": f"{day_value.strftime('%d %b')} • {label}",
                }
            )
    return options


def get_tractor_category_meta(category_id):
    subtitle_map = {
        "all": "Browse every machine near your farm",
        "land_preparation": "Plough, Rotavator, Cultivator",
        "sowing": "Seed Drill, Planter, Transplanter",
        "harvesting": "Harvester, Reaper",
        "transport": "Trolley, Tanker, Trailer",
        "spraying": "Boom Sprayer, Drone",
    }
    title_map = {
        "all": "All Services",
        "land_preparation": "Land Preparation",
        "sowing": "Sowing",
        "harvesting": "Harvesting",
        "transport": "Transport",
        "spraying": "Spraying",
    }
    icon_map = {item["id"]: item["icon"] for item in TRACTOR_SERVICE_CATEGORIES}
    return {
        "id": category_id,
        "title": title_map.get(category_id, "Service"),
        "subtitle": subtitle_map.get(category_id, "Farm machinery"),
        "icon": icon_map.get(category_id, "fa-tractor"),
    }


def parse_booking_date_value(raw_value):
    raw = str(raw_value or "").strip()
    if not raw:
        return datetime.now().date()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return datetime.now().date()


def build_tractor_machine_catalog(user, center_lat, center_lng, selected_category="all", sort_option="nearest", booking_date=None):
    selected_date = booking_date or datetime.now().date()
    busy_pairs = {
        (item.machine_id, item.slot_label)
        for item in TractorBooking.query.filter_by(booking_date=selected_date).filter(TractorBooking.booking_status != "cancelled").all()
    }
    slot_options = get_tractor_slot_options()
    serialized = []

    for machine in TRACTOR_MARKETPLACE_MACHINES:
        if selected_category not in {"", "all"} and machine["category"] != selected_category:
            continue

        distance_km = haversine_distance_km(center_lat, center_lng, machine["lat"], machine["lng"])
        minutes = estimate_travel_minutes(distance_km)
        available_slots = [slot for slot in slot_options if slot["date"] == selected_date.isoformat() and (machine["id"], slot["label"]) not in busy_pairs]
        serialized.append(
            {
                **machine,
                "distance_km": round(distance_km, 1),
                "distance_label": format_travel_minutes(minutes),
                "distance_minutes": minutes,
                "price_label": f"Rs {int(machine['price_per_hour'])}",
                "availability_label": machine["availability"] if available_slots else "Busy for selected day",
                "is_available": bool(available_slots),
                "slot_count": len(available_slots),
                "next_slot": available_slots[0]["display"] if available_slots else "Try another date",
                "slot_options": available_slots,
                "category_meta": get_tractor_category_meta(machine["category"]),
            }
        )

    if sort_option == "price":
        serialized.sort(key=lambda item: (int(item["price_per_hour"]), item["distance_minutes"], item["name"].lower()))
    elif sort_option == "rating":
        serialized.sort(key=lambda item: (-float(item["rating"]), item["distance_minutes"], item["name"].lower()))
    else:
        serialized.sort(key=lambda item: (item["distance_minutes"], -float(item["rating"]), item["name"].lower()))

    return serialized


def build_tractor_ai_recommendation(user, machines, selected_category):
    crop_name = (user.crop_type or "your crop").strip()
    if not machines:
        return {
            "title": "AI recommendation",
            "detail": "No nearby machine found for this filter. Try switching service type or nearby distance priority.",
        }

    top_machine = machines[0]
    category_title = get_tractor_category_meta(selected_category if selected_category != "all" else top_machine["category"])["title"]
    crop_lower = crop_name.lower()
    if any(keyword in crop_lower for keyword in ["rice", "paddy"]):
        detail = f"For {crop_name}, {category_title.lower()} demand is usually time-sensitive. {top_machine['name']} looks strongest because it is {top_machine['distance_label'].lower()} with rating {top_machine['rating']}."
    elif any(keyword in crop_lower for keyword in ["wheat", "maize"]):
        detail = f"{crop_name.title()} fields benefit from scheduling the nearest machine first to avoid labor delays. {top_machine['name']} is the best quick-start option right now."
    else:
        detail = f"Nearest high-rated machine is {top_machine['name']}. Book the earliest slot to reduce waiting time and field idle hours."

    return {"title": "AI recommendation", "detail": detail}


def serialize_tractor_booking(booking):
    return {
        "id": booking.id,
        "machine_name": booking.machine_name,
        "category": get_tractor_category_meta(booking.category)["title"],
        "booking_date": booking.booking_date.strftime("%d %b %Y"),
        "slot_label": booking.slot_label,
        "duration_hours": int(booking.duration_hours or 1),
        "total_amount_inr": int(booking.total_amount_inr or 0),
        "payment_mode": str(booking.payment_mode or "pay_later").replace("_", " ").title(),
        "payment_status": str(booking.payment_status or "pending").title(),
        "booking_status": str(booking.booking_status or "confirmed").title(),
        "created_at": format_relative_time(booking.created_at),
    }


def build_tractor_service_types():
    items = []
    for category in TRACTOR_SERVICE_CATEGORIES:
        if category["id"] == "all":
            continue
        meta = get_tractor_category_meta(category["id"])
        sample_names = [item["name"] for item in TRACTOR_MARKETPLACE_MACHINES if item["category"] == category["id"]][:2]
        items.append(
            {
                "id": meta["id"],
                "title": meta["title"],
                "subtitle": meta["subtitle"],
                "icon": meta["icon"],
                "sample_names": sample_names,
            }
        )
    return items


def get_tractor_machine_by_id(machine_id):
    machine_key = str(machine_id or "").strip()
    return next((item for item in TRACTOR_MARKETPLACE_MACHINES if item["id"] == machine_key), None)


def build_tractor_marketplace_payload(user, category="all", sort_option="nearest", service_date=None, lat=None, lng=None):
    location_label = (user.location or "Puri").strip() or "Puri"
    map_center = resolve_rent_tractor_map_center(location_label)
    center_lat = float(lat if lat is not None else map_center["lat"])
    center_lng = float(lng if lng is not None else map_center["lng"])
    booking_date = parse_booking_date_value(service_date)
    machines = build_tractor_machine_catalog(
        user,
        center_lat,
        center_lng,
        selected_category=category,
        sort_option=sort_option,
        booking_date=booking_date,
    )
    markers = [
        {
            "lat": item["lat"],
            "lng": item["lng"],
            "title": item["name"],
            "active": index == 0,
            "category": item["category"],
        }
        for index, item in enumerate(machines[:8])
    ]
    return {
        "category": category,
        "sort": sort_option,
        "service_date": booking_date.isoformat(),
        "machines": machines,
        "markers": markers,
        "ai_recommendation": build_tractor_ai_recommendation(user, machines, category),
        "map_center": {"lat": center_lat, "lng": center_lng, "zoom": map_center["zoom"]},
    }


def build_rent_tractor_page_context(user):
    primary_farm = (
        Farm.query.filter_by(user_id=user.id, is_primary=True).order_by(Farm.created_at.asc()).first()
        or Farm.query.filter_by(user_id=user.id).order_by(Farm.created_at.asc()).first()
    )
    location_label = (
        (getattr(primary_farm, "location", "") or "").strip()
        or (getattr(user, "location", "") or "").strip()
        or "Puri"
    )
    map_center = resolve_rent_tractor_map_center(location_label)
    avatar_label = ((user.name or "Farmer").strip()[:1] or "F").upper()
    selected_category = str(request.args.get("category") or "all").strip().lower() or "all"
    if selected_category not in {item["id"] for item in TRACTOR_SERVICE_CATEGORIES}:
        selected_category = "all"
    sort_option = str(request.args.get("sort") or "nearest").strip().lower()
    if sort_option not in {"nearest", "price", "rating"}:
        sort_option = "nearest"
    selected_date = parse_booking_date_value(request.args.get("service_date"))
    user_lat = request.args.get("lat", type=float)
    user_lng = request.args.get("lng", type=float)
    center_lat = float(user_lat if user_lat is not None else map_center["lat"])
    center_lng = float(user_lng if user_lng is not None else map_center["lng"])
    machines = build_tractor_machine_catalog(user, center_lat, center_lng, selected_category=selected_category, sort_option=sort_option, booking_date=selected_date)
    ai_recommendation = build_tractor_ai_recommendation(user, machines, selected_category)
    service_types = build_tractor_service_types()
    markers = [
        {
            "lat": item["lat"],
            "lng": item["lng"],
            "title": item["name"],
            "active": index == 0,
            "category": item["category"],
        }
        for index, item in enumerate(machines[:8])
    ]
    booking_history = [
        serialize_tractor_booking(item)
        for item in TractorBooking.query.filter_by(user_id=user.id).order_by(TractorBooking.created_at.desc()).limit(5).all()
    ]
    booking_stats = {
        "active_bookings": TractorBooking.query.filter_by(user_id=user.id).filter(TractorBooking.booking_status.in_(["confirmed", "pending", "pending_payment"])).count(),
        "marketplace_machines": len(TRACTOR_MARKETPLACE_MACHINES),
        "categories": len(TRACTOR_SERVICE_CATEGORIES) - 1,
    }

    return {
        "title": "Rent a Tractor",
        "active_page": "rent_tractor",
        "location_label": location_label,
        "farm_name": primary_farm.name if primary_farm else "Primary Farm",
        "map_center": {"lat": center_lat, "lng": center_lng, "zoom": map_center["zoom"]},
        "markers": markers,
        "service_types": service_types,
        "categories": TRACTOR_SERVICE_CATEGORIES,
        "sort_options": [
            {"id": "nearest", "label": "Nearest"},
            {"id": "price", "label": "Lowest price"},
            {"id": "rating", "label": "Top rated"},
        ],
        "selected_category": selected_category,
        "selected_sort": sort_option,
        "selected_date": selected_date.isoformat(),
        "slot_options": get_tractor_slot_options(),
        "machines": machines,
        "default_service_id": selected_category if selected_category != "all" else (service_types[0]["id"] if service_types else "land_preparation"),
        "avatar_label": avatar_label,
        "ai_recommendation": ai_recommendation,
        "booking_history": booking_history,
        "booking_stats": booking_stats,
        "payment_enabled": bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET),
    }


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
    progress_pct = int(((completed_count / total_count) * 100) + 0.5) if total_count else 0

    return {
        "steps": steps,
        "completed_count": completed_count,
        "total_count": total_count,
        "progress_pct": progress_pct,
    }


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


def get_weather_cache_key(city):
    return str(city or "").strip().lower()


def get_cached_weather_api_payload(city):
    cache_key = get_weather_cache_key(city)
    cached = WEATHER_API_CACHE.get(cache_key)
    if not cached:
        return None

    if float(cached.get("expires_at", 0)) <= time.time():
        WEATHER_API_CACHE.pop(cache_key, None)
        return None

    return cached.get("payload")


def set_cached_weather_api_payload(city, payload):
    cache_key = get_weather_cache_key(city)
    if not cache_key:
        return

    WEATHER_API_CACHE[cache_key] = {
        "expires_at": time.time() + WEATHER_API_CACHE_TTL_SECONDS,
        "payload": payload,
    }


def format_weather_local_timestamp(timestamp_value, timezone_offset=0, fmt="%I %p"):
    try:
        normalized_timestamp = int(timestamp_value or 0) + int(timezone_offset or 0)
        label = datetime.fromtimestamp(normalized_timestamp, tz=timezone.utc).strftime(fmt)
        if "%I" in fmt:
            label = label.lstrip("0")
        return label
    except (TypeError, ValueError, OSError):
        return ""


def build_weather_date_key(timestamp_value, timezone_offset=0):
    return format_weather_local_timestamp(timestamp_value, timezone_offset, "%Y-%m-%d")


def map_aqi_index(aqi_value):
    try:
        normalized = int(aqi_value or 0)
    except (TypeError, ValueError):
        normalized = 0

    if normalized <= 1:
        return {"value": max(normalized, 1), "level": "Good", "color": "#22a06b", "bar_width": 28}
    if normalized <= 3:
        return {"value": normalized, "level": "Moderate", "color": "#f59e0b", "bar_width": 62}
    return {"value": normalized if normalized else 4, "level": "Poor", "color": "#dc2626", "bar_width": 100}


WEATHER_LOCATION_HINTS = {
    "bhubaneswar": "Bhubaneswar, Odisha, IN",
    "bhubaneshwar": "Bhubaneswar, Odisha, IN",
    "bhubanewar": "Bhubaneswar, Odisha, IN",
    "bhuvaneswar": "Bhubaneswar, Odisha, IN",
    "bbsr": "Bhubaneswar, Odisha, IN",
    "malkangiri": "Malkangiri, Odisha, IN",
    "malakangiri": "Malkangiri, Odisha, IN",
    "malakanagiri": "Malkangiri, Odisha, IN",
}


def normalize_weather_place_name(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def build_weather_location_queries(location_name):
    raw_value = str(location_name or "").strip()
    normalized_value = normalize_weather_place_name(raw_value)
    queries = []

    hinted_query = WEATHER_LOCATION_HINTS.get(normalized_value)
    if hinted_query:
        queries.append(hinted_query)

    if raw_value and "," not in raw_value:
        queries.append(f"{raw_value}, IN")
        queries.append(f"{raw_value}, India")

    if raw_value:
        queries.append(raw_value)

    deduped_queries = []
    seen_queries = set()
    for query in queries:
        query_key = normalize_weather_place_name(query)
        if not query_key or query_key in seen_queries:
            continue
        seen_queries.add(query_key)
        deduped_queries.append(query)

    return deduped_queries


def resolve_openweather_location(location_name):
    if not OPENWEATHER_API_KEY:
        return None

    normalized_query = normalize_weather_place_name(location_name)
    if not normalized_query:
        return None

    best_match = None
    best_score = -1

    for query in build_weather_location_queries(location_name):
        payload = fetch_json(
            "https://api.openweathermap.org/geo/1.0/direct",
            params={
                "q": query,
                "limit": 5,
                "appid": OPENWEATHER_API_KEY,
            },
        )
        if not isinstance(payload, list) or not payload:
            continue

        for candidate in payload:
            try:
                lat = float(candidate.get("lat"))
                lon = float(candidate.get("lon"))
            except (TypeError, ValueError):
                continue

            candidate_name = str(candidate.get("name") or "").strip()
            candidate_state = str(candidate.get("state") or "").strip()
            candidate_country = str(candidate.get("country") or "").strip().upper()
            candidate_name_norm = normalize_weather_place_name(candidate_name)

            score = 0
            if candidate_country == "IN":
                score += 6
            if candidate_name_norm == normalized_query:
                score += 10
            elif candidate_name_norm.startswith(normalized_query) or normalized_query.startswith(candidate_name_norm):
                score += 6

            local_names = candidate.get("local_names") or {}
            if isinstance(local_names, dict):
                local_matches = [
                    normalize_weather_place_name(local_name)
                    for local_name in local_names.values()
                    if str(local_name or "").strip()
                ]
                if normalized_query in local_matches:
                    score += 8

            state_norm = normalize_weather_place_name(candidate_state)
            if state_norm == "odisha":
                score += 3

            if score > best_score:
                display_name = candidate_name or str(location_name or "").strip().title()
                if candidate_state and candidate_state.lower() not in display_name.lower():
                    display_name = f"{display_name}, {candidate_state}"

                if normalize_weather_place_name(location_name) in WEATHER_LOCATION_HINTS:
                    display_name = "Malkangiri, Odisha"

                best_match = {
                    "lat": lat,
                    "lon": lon,
                    "name": display_name,
                    "country": candidate_country,
                    "state": candidate_state,
                }
                best_score = score

        if best_match and best_score >= 12:
            break

    return best_match


def build_weather_hourly_data(forecast_payload, timezone_offset=0):
    hourly_points = []
    forecast_list = (forecast_payload or {}).get("list") or []

    for item in forecast_list[:8]:
        weather_info = (item.get("weather") or [{}])[0]  # type: ignore
        hourly_points.append(
            {
                "time": format_weather_local_timestamp(item.get("dt"), timezone_offset, "%I %p"),
                "temp": round(float((item.get("main") or {}).get("temp", 0))),  # type: ignore
                "rain_prob": clamp(int(round(float(item.get("pop", 0)) * 100)), 0, 100),  # type: ignore
                "icon": build_weather_icon_url(weather_info.get("icon", "01d")),  # type: ignore
                "condition": weather_info.get("main", "Clear"),  # type: ignore
            }
        )

    return hourly_points


def build_weather_daily_data(current_payload, forecast_payload, timezone_offset=0):
    grouped_days = {}
    current_weather = (current_payload.get("weather") or [{}])[0] if isinstance(current_payload, dict) else {}
    current_main = current_payload.get("main") or {} if isinstance(current_payload, dict) else {}
    current_dt = current_payload.get("dt") if isinstance(current_payload, dict) else None
    current_key = build_weather_date_key(current_dt, timezone_offset) if current_dt else ""

    if current_key:
        grouped_days[current_key] = {
            "timestamp": current_dt,
            "temps": [float(current_main.get("temp_min", current_main.get("temp", 0))), float(current_main.get("temp_max", current_main.get("temp", 0)))],
            "icons": [current_weather.get("icon", "01d")],
            "conditions": [current_weather.get("main", "Clear")],
        }

    for item in ((forecast_payload or {}).get("list") or []):
        date_key = build_weather_date_key(item.get("dt"), timezone_offset)
        if not date_key:
            continue

        bucket = grouped_days.setdefault(
            date_key,
            {"timestamp": item.get("dt"), "temps": [], "icons": []},
        )
        temp_info = item.get("main") or {}
        bucket["temps"].append(float(temp_info.get("temp_min", temp_info.get("temp", 0))))
        bucket["temps"].append(float(temp_info.get("temp_max", temp_info.get("temp", 0))))
        bucket["icons"].append(((item.get("weather") or [{}])[0]).get("icon", "01d"))  # type: ignore
        bucket.setdefault("conditions", []).append(((item.get("weather") or [{}])[0]).get("main", "Clear"))  # type: ignore

    ordered_keys = sorted(grouped_days.keys())
    daily_points = []

    for date_key in ordered_keys[:7]:
        bucket = grouped_days[date_key]
        temps = bucket.get("temps") or [0]
        icons = bucket.get("icons") or ["01d"]
        conditions = bucket.get("conditions") or ["Clear"]
        day_label = format_weather_local_timestamp(bucket.get("timestamp"), timezone_offset, "%a")
        daily_points.append(
            {
                "day": day_label or "Day",
                "min": round(min(float(value) for value in temps)),
                "max": round(max(float(value) for value in temps)),
                "icon": build_weather_icon_url(icons[len(icons) // 2]),
                "condition": str(conditions[len(conditions) // 2] or "Clear"),
            }
        )

    while len(daily_points) < 7:
        last_point = daily_points[-1] if daily_points else {"day": "Day", "min": 24, "max": 31, "icon": build_weather_icon_url("01d")}
        next_index = len(daily_points)
        next_day = (datetime.now() + timedelta(days=next_index)).strftime("%a")
        variance = -1 if next_index % 2 == 0 else 1
        daily_points.append(
            {
                "day": next_day,
                "min": int(last_point["min"]) + variance,
                "max": int(last_point["max"]) + variance,
                "icon": last_point["icon"],
                "condition": last_point.get("condition", "Clear"),
            }
        )

    return daily_points[:7]


def build_weather_insights_payload(current_data, hourly_points, daily_points, aqi_payload):
    insights = []
    humidity_value = int(current_data.get("humidity", 0) or 0)
    temp_value = float(current_data.get("temp", 0) or 0)
    rain_peak = max((int(item.get("rain_prob", 0) or 0) for item in hourly_points), default=0)
    wind_speed = float(current_data.get("wind_speed", 0) or 0)

    if humidity_value > 80:
        insights.append("High humidity can increase fungal risk in the field.")
        insights.append("High pest risk due to humidity. Scout leaves and lower canopy today.")
    if rain_peak > 50:
        insights.append("Rain expected. Avoid spraying today and keep drainage ready.")
    if temp_value > 35:
        insights.append("Heat stress warning. Irrigate in early morning or evening hours.")
    if wind_speed >= 6:
        insights.append("Avoid spraying during stronger wind windows to reduce drift loss.")
    if rain_peak < 35 and temp_value < 34:
        insights.append("Good time for irrigation planning and field scouting.")
    if aqi_payload.get("level") == "Poor":
        insights.append("Air quality is poor. Limit long field exposure and use a mask if needed.")

    unique_insights = []
    for item in insights:
        if item not in unique_insights:
            unique_insights.append(item)

    return unique_insights[:4] or [
        "Stable weather window for routine farm activity.",
        "Continue regular scouting and irrigation planning.",
    ]


def build_openweather_monitor_payload(city):
    location_name = str(city or "").strip() or "Bhubaneswar"
    cached_payload = get_cached_weather_api_payload(location_name)
    if cached_payload is not None:
        cached_copy = dict(cached_payload)
        requested_location = str(cached_copy.get("requested_location") or location_name).strip() or location_name
        matched_location = str(cached_copy.get("matched_location") or cached_copy.get("location") or "").strip()
        cached_copy["requested_location"] = requested_location
        cached_copy["location"] = requested_location
        cached_copy["matched_location"] = (
            matched_location
            if matched_location
            and normalize_weather_place_name(matched_location) != normalize_weather_place_name(requested_location)
            else ""
        )
        return cached_copy

    fallback_weather = fetch_weather_bundle(location_name)
    fallback_now = datetime.now()
    fallback_base_temp = float(fallback_weather["temp"])
    fallback_condition = fallback_weather["description"]

    def format_hour_label(value):
        return value.strftime("%I %p").lstrip("0") or value.strftime("%I %p")

    fallback_payload = {
        "location": location_name,
        "requested_location": location_name,
        "matched_location": (
            fallback_weather["city"]
            if normalize_weather_place_name(fallback_weather["city"]) != normalize_weather_place_name(location_name)
            else ""
        ),
        "updated_at": fallback_weather["updated_at"],
        "current": {
            "temp": fallback_weather["temp"],
            "feels_like": fallback_weather["feels_like"],
            "humidity": fallback_weather["humidity"],
            "wind_speed": fallback_weather["wind_speed_kmh"],
            "wind_deg": fallback_weather["wind_deg"],
            "condition": fallback_weather["description"],
            "icon": fallback_weather["icon_url"],
            "pressure": fallback_weather["pressure"],
        },
        "hourly": [
            {
                "time": format_hour_label(fallback_now + timedelta(hours=index * 3)),
                "temp": round(fallback_base_temp + (-2 if index < 2 else min(index, 4) - 1)),
                "rain_prob": max(15, min(70, 18 + (index % 4) * 12)),
                "icon": fallback_weather["icon_url"],
                "condition": fallback_condition,
            }
            for index in range(8)
        ],
        "daily": [
            {
                "day": (fallback_now + timedelta(days=index)).strftime("%a"),
                "min": round(fallback_base_temp - 2 + (-1 if index % 2 == 0 else 0)),
                "max": round(fallback_base_temp + 2 + (1 if index % 3 == 0 else 0)),
                "icon": fallback_weather["icon_url"],
                "condition": fallback_condition,
            }
            for index in range(7)
        ],
        "aqi": {"value": 2, "level": "Moderate", "color": "#f59e0b", "bar_width": 58},
        "insights": [
            "Weather data is running in fallback mode right now.",
            "Use crop scouting and field checks before spray or irrigation decisions.",
        ],
        "source": "fallback",
    }

    if not OPENWEATHER_API_KEY:
        return fallback_payload

    resolved_location = resolve_openweather_location(location_name)
    current_params = {
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",
    }
    if resolved_location:
        current_params["lat"] = resolved_location["lat"]
        current_params["lon"] = resolved_location["lon"]
    else:
        current_params["q"] = location_name

    current_payload = fetch_json(
        "https://api.openweathermap.org/data/2.5/weather",
        params=current_params,
    )
    if not current_payload or str(current_payload.get("cod", "200")) != "200":
        return fallback_payload

    coord = current_payload.get("coord") or {}  # type: ignore
    lat = coord.get("lat")
    lon = coord.get("lon")
    timezone_offset = int(current_payload.get("timezone", 0) or 0)  # type: ignore
    forecast_payload = None
    aqi_source = None
    if lat is not None and lon is not None:
        forecast_payload = fetch_json(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={
                "lat": lat,
                "lon": lon,
                "appid": OPENWEATHER_API_KEY,
                "units": "metric",
            },
        )
        aqi_source = fetch_json(
            "https://api.openweathermap.org/data/2.5/air_pollution",
            params={
                "lat": lat,
                "lon": lon,
                "appid": OPENWEATHER_API_KEY,
            },
        )

    main = current_payload.get("main") or {}  # type: ignore
    wind = current_payload.get("wind") or {}  # type: ignore
    weather_info = (current_payload.get("weather") or [{}])[0]  # type: ignore
    hourly_points = build_weather_hourly_data(forecast_payload, timezone_offset)
    daily_points = build_weather_daily_data(current_payload, forecast_payload, timezone_offset)
    aqi_data = map_aqi_index((((aqi_source or {}).get("list") or [{}])[0].get("main") or {}).get("aqi"))  # type: ignore

    merged_payload = {
        "location": location_name,
        "requested_location": location_name,
        "matched_location": (
            (resolved_location or {}).get("name") or current_payload.get("name", location_name)  # type: ignore
        ),
        "updated_at": format_weather_local_timestamp(current_payload.get("dt"), timezone_offset, "%d %b, %I:%M %p"),
        "current": {
            "temp": round(float(main.get("temp", fallback_weather["temp"]))),  # type: ignore
            "feels_like": round(float(main.get("feels_like", fallback_weather["feels_like"]))),  # type: ignore
            "humidity": int(main.get("humidity", fallback_weather["humidity"])),  # type: ignore
            "wind_speed": round(float(wind.get("speed", fallback_weather["wind_speed"])) * 3.6, 1),  # type: ignore
            "wind_deg": int(wind.get("deg", fallback_weather["wind_deg"])),  # type: ignore
            "condition": weather_info.get("description", fallback_weather["description"]).title(),  # type: ignore
            "icon": build_weather_icon_url(weather_info.get("icon", fallback_weather["icon_code"])),  # type: ignore
            "pressure": int(main.get("pressure", fallback_weather["pressure"])),  # type: ignore
        },
        "hourly": hourly_points,
        "daily": daily_points,
        "aqi": aqi_data,
        "insights": build_weather_insights_payload(
            {
                "temp": round(float(main.get("temp", fallback_weather["temp"]))),  # type: ignore
                "humidity": int(main.get("humidity", fallback_weather["humidity"])),  # type: ignore
                "wind_speed": float(wind.get("speed", fallback_weather["wind_speed"])),  # type: ignore
            },
            hourly_points,
            daily_points,
            aqi_data,
        ),
        "source": "openweather",
    }
    if normalize_weather_place_name(str(merged_payload["matched_location"])) == normalize_weather_place_name(location_name):
        merged_payload["matched_location"] = ""
    set_cached_weather_api_payload(location_name, merged_payload)
    return merged_payload


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

    resolved_location = resolve_openweather_location(location)
    current_params = {
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",
    }
    if resolved_location:
        current_params["lat"] = resolved_location["lat"]
        current_params["lon"] = resolved_location["lon"]
    else:
        current_params["q"] = location

    current_data = fetch_json(
        "https://api.openweathermap.org/data/2.5/weather",
        params=current_params,
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
        "city": (resolved_location or {}).get("name") or current_data.get("name", location),  # type: ignore
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
    lat = float(weather.get("lat") or 20.0)
    lon = float(weather.get("lon") or 78.0)
    humidity = float(weather.get("humidity", 60) or 60)
    rainfall = float(weather.get("rainfall_mm", 0) or 0)
    temperature = float(weather.get("temp", 30) or 30)
    pressure = float(weather.get("pressure", 1008) or 1008)
    clouds = float(weather.get("clouds", 30) or 30)

    geo_bias = ((abs(lat) * 1.7) + (abs(lon) * 0.55) + (seed % 11)) % 8
    ph_value = float(
        int(
            clamp(
                5.5
                + (pressure - 1000) * 0.018
                - rainfall * 0.025
                + humidity * 0.004
                - abs(lat) * 0.006
                + geo_bias * 0.09,
                5.2,
                7.8,
            )
            * 10
        )
        / 10.0
    )
    nitrogen = clamp(
        int(
            26
            + humidity * 0.28
            + rainfall * 1.9
            - temperature * 0.42
            + clouds * 0.08
            + (abs(lat) % 9)
            + (seed % 7)
        ),
        18,
        96,
    )
    moisture = clamp(
        int(
            humidity * 0.72
            + rainfall * 3.6
            - temperature * 0.35
            + clouds * 0.12
            + (abs(lon) % 6)
        ),
        20,
        98,
    )

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
    lat = float(str(weather.get("lat") or 20.0))
    lon = float(str(weather.get("lon") or 78.0))
    humidity = float(str(weather.get("humidity", 60) or 60))
    rainfall = float(str(weather.get("rainfall_mm", 0) or 0))
    pressure = float(str(weather.get("pressure", 1008) or 1008))
    temperature = float(str(weather.get("temp", 30) or 30))

    phosphorus = clamp(
        int(
            18
            + float(soil["moisture"]) * 0.28
            + humidity * 0.12
            + (pressure - 1000) * 0.45
            - rainfall * 0.6
            + (abs(lat) % 10)
            + (seed % 5)
        ),
        20,
        96,
    )
    potassium = clamp(
        int(
            32
            + temperature * 1.1
            + humidity * 0.18
            + (abs(lon) % 14)
            + float(soil["nitrogen"]) * 0.18
            - abs(float(soil["ph"]) - 6.5) * 11
            + (seed % 6)
        ),
        35,
        132,
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
            "name": weather["city"] or user.location or weather["city"],
            "maps_search_url": "https://www.google.com/maps/search/?api=1&"
            + urlencode({"query": weather["city"] or user.location or weather["city"]}),
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

    if torch is None:
        return None, None

    try:
        device = torch.device('cpu') # type: ignore
        model_path = CROP_DISEASE_MODEL_PATH
        labels_path = CROP_DISEASE_LABELS_PATH

        if not model_path.exists():
            fallback_model_path = Path(app.root_path) / "models" / "crop_disease_model.pth"
            fallback_labels_path = Path(app.root_path) / "models" / "crop_disease_labels.json"
            if fallback_model_path.exists():
                model_path = fallback_model_path
            if fallback_labels_path.exists():
                labels_path = fallback_labels_path

        if not model_path.exists():
            return None, None

        model = torch.load( # type: ignore
            str(model_path),
            map_location=device,
            weights_only=False,
        )
        model.eval()
        DISEASE_MODEL_CACHE["model"] = model

        if labels_path.exists():
            with labels_path.open("r", encoding="utf-8") as handle:
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

        if str(predicted_class_name or "").strip().lower() in {"plantvillage", "unknown", ""}:
            return None, 0, None, 0
        if confidence < 58:
            return None, conf_float, predicted_class_name, confidence
        
        return class_index, conf_float, predicted_class_name, confidence
    except Exception as e:
        print(f"Error predicting with model: {e}")
        return None, 0, None, 0

from disease_knowledge import DISEASE_KNOWLEDGE, get_disease_info


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


def evaluate_leaf_upload(image):
    analysis_image = ImageOps.fit(image.convert("RGB"), (256, 256))
    image_array = np.asarray(analysis_image, dtype=np.float32)
    hue, saturation, value = rgb_to_hsv_channels(image_array)
    red = image_array[:, :, 0]
    green = image_array[:, :, 1]
    blue = image_array[:, :, 2]

    background_mask = ((red > 246) & (green > 246) & (blue > 246)) | ((red < 8) & (green < 8) & (blue < 8))
    subject_mask = ~background_mask
    subject_pixels = max(int(np.count_nonzero(subject_mask)), 1)
    total_pixels = int(subject_mask.size) or 1

    def masked_ratio(mask):
        return float(np.count_nonzero(mask & subject_mask)) / subject_pixels

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
        & (value > 0.14)
        & (value < 0.82)
    )
    yellow_mask = (
        (hue > 0.11)
        & (hue < 0.19)
        & (saturation > 0.18)
        & (value > 0.40)
    )
    cool_mask = (
        (hue > 0.48)
        & (hue < 0.72)
        & (saturation > 0.14)
        & (value > 0.16)
    )
    gray_mask = (saturation < 0.12) & (value > 0.18) & (value < 0.90)
    vivid_red_mask = (((hue < 0.04) | (hue > 0.94)) & (saturation > 0.28) & (value > 0.20))
    plant_like_mask = green_mask | brown_mask | yellow_mask

    subject_ratio = float(subject_pixels) / float(total_pixels)
    plant_like_ratio = masked_ratio(plant_like_mask)
    green_ratio = masked_ratio(green_mask)
    warm_ratio = masked_ratio(brown_mask | yellow_mask)
    cool_ratio = masked_ratio(cool_mask)
    gray_ratio = masked_ratio(gray_mask)
    red_ratio = masked_ratio(vivid_red_mask)
    texture_value = float(image_array[subject_mask].std()) if np.any(subject_mask) else float(image_array.std())

    score = 0.0
    if subject_ratio >= 0.12:
        score += 0.18
    if subject_ratio >= 0.20:
        score += 0.12
    if plant_like_ratio >= 0.18:
        score += 0.28
    if plant_like_ratio >= 0.28:
        score += 0.18
    if green_ratio >= 0.07 or warm_ratio >= 0.16:
        score += 0.14
    if 18.0 <= texture_value <= 75.0:
        score += 0.08
    if cool_ratio <= 0.26:
        score += 0.08
    if gray_ratio <= 0.58:
        score += 0.06
    if red_ratio <= 0.22:
        score += 0.06

    if subject_ratio < 0.08:
        score -= 0.25
    if plant_like_ratio < 0.10:
        score -= 0.22
    if cool_ratio > 0.34 and plant_like_ratio < 0.16:
        score -= 0.22
    if gray_ratio > 0.62 and plant_like_ratio < 0.16:
        score -= 0.20

    leaf_score = clamp(int(round(score * 100)), 0, 100)
    is_probable_leaf = (
        subject_ratio >= 0.08
        and plant_like_ratio >= 0.12
        and leaf_score >= 42
        and not (cool_ratio > 0.38 and plant_like_ratio < 0.18)
        and not (gray_ratio > 0.68 and warm_ratio < 0.10 and green_ratio < 0.08)
    )

    return {
        "is_probable_leaf": is_probable_leaf,
        "leaf_score": leaf_score,
        "subject_ratio": round(subject_ratio, 4),
        "plant_like_ratio": round(plant_like_ratio, 4),
        "green_ratio": round(green_ratio, 4),
        "warm_ratio": round(warm_ratio, 4),
        "cool_ratio": round(cool_ratio, 4),
        "gray_ratio": round(gray_ratio, 4),
        "texture_value": round(texture_value, 2),
    }


def build_masked_color_histogram(image):
    analysis_image = ImageOps.fit(image.convert("RGB"), (192, 192))
    image_array = np.asarray(analysis_image, dtype=np.float32)
    leaf_mask = build_leaf_mask(image_array)

    histograms = []
    for channel_index in range(3):
        channel = image_array[:, :, channel_index][leaf_mask]
        if channel.size == 0:
            channel = image_array[:, :, channel_index].reshape(-1)
        histogram, _ = np.histogram(channel, bins=12, range=(0, 255), density=False)
        histograms.append(histogram.astype(np.float32))

    flat_hist = np.concatenate(histograms)
    total = float(np.sum(flat_hist)) or 1.0
    return flat_hist / total


def compute_average_hash(image, hash_size=16):
    grayscale = ImageOps.fit(image.convert("L"), (hash_size, hash_size))
    pixels = np.asarray(grayscale, dtype=np.float32)
    threshold = float(pixels.mean())
    return (pixels > threshold).astype(np.uint8).flatten()


def compute_difference_hash(image, hash_size=16):
    grayscale = ImageOps.fit(image.convert("L"), (hash_size + 1, hash_size))
    pixels = np.asarray(grayscale, dtype=np.float32)
    diff = pixels[:, 1:] > pixels[:, :-1]
    return diff.astype(np.uint8).flatten()


def hash_similarity(left_hash, right_hash):
    left = np.asarray(left_hash, dtype=np.uint8).flatten()
    right = np.asarray(right_hash, dtype=np.uint8).flatten()
    if left.size == 0 or right.size == 0 or left.size != right.size:
        return 0.0
    return float(np.mean(left == right))


def score_feature_similarity(upload_features, ref_features):
    weights = {
        "brown_ratio": 1.4,
        "yellow_ratio": 1.4,
        "white_ratio": 1.2,
        "dark_ratio": 1.1,
        "gray_ratio": 1.0,
        "green_ratio": 1.2,
        "warm_spot_ratio": 1.0,
        "lesion_ratio": 1.5,
        "edge_damage": 0.9,
        "stripe_ratio": 0.9,
        "mottled_ratio": 0.8,
        "texture_value": 0.02,
    }
    distance = 0.0
    for key, weight in weights.items():
        distance += abs(float(upload_features.get(key, 0.0)) - float(ref_features.get(key, 0.0))) * weight
    return distance


def load_disease_reference_signatures():
    global DISEASE_REFERENCE_SIGNATURE_CACHE

    if DISEASE_REFERENCE_SIGNATURE_CACHE is not None:
        return DISEASE_REFERENCE_SIGNATURE_CACHE

    neutral_weather = {"humidity": 68, "temp": 28}
    signatures = []
    dataset = load_disease_dataset()

    for entry in dataset.values():
        image_url = resolve_library_disease_image(slugify_crop_name(entry["name"]), entry["name"])
        if not str(image_url).startswith("/static/library/diseases/"):
            continue

        file_path = Path(app.root_path) / str(image_url).lstrip("/")
        if not file_path.exists():
            continue

        try:
            ref_image = Image.open(file_path)
            ref_image = ImageOps.exif_transpose(ref_image).convert("RGB")
        except (UnidentifiedImageError, OSError, ValueError):
            continue

        ref_features, ref_signals, _ = extract_leaf_features(ref_image, neutral_weather)
        signatures.append(
            {
                "entry": entry,
                "features": ref_features,
                "signals": set(ref_signals),
                "histogram": build_masked_color_histogram(ref_image),
                "average_hash": compute_average_hash(ref_image),
                "difference_hash": compute_difference_hash(ref_image),
                "image_url": image_url,
            }
        )

    DISEASE_REFERENCE_SIGNATURE_CACHE = signatures
    return DISEASE_REFERENCE_SIGNATURE_CACHE


def build_reference_image_diagnosis(image, crop_name, weather):
    dataset = load_disease_dataset()
    healthy_entry = dataset.get("healthy")
    upload_features, upload_signals, base_confidence = extract_leaf_features(image, weather)

    if (
        healthy_entry is not None
        and float(upload_features.get("green_ratio", 0.0)) >= 0.58
        and float(upload_features.get("lesion_ratio", 0.0)) <= 0.055
        and float(upload_features.get("yellow_ratio", 0.0)) <= 0.04
        and float(upload_features.get("white_ratio", 0.0)) <= 0.03
        and float(upload_features.get("dark_ratio", 0.0)) <= 0.03
    ):
        return {
            "disease": healthy_entry["name"],
            "confidence": max(82, base_confidence),
            "cause": str(healthy_entry.get("etiology", {}).get("pathogen") or "None"),
            "symptoms": "; ".join(healthy_entry.get("symptoms", [])),
            "organic_solution": "; ".join((healthy_entry.get("solution") or {}).get("organic", [])),
            "chemical_solution": "; ".join((healthy_entry.get("solution") or {}).get("chemical", [])),
            "prevention": list(healthy_entry.get("prevention", [])),
            "diagnostic_reason": "Reference image matcher found strong healthy-leaf similarity.",
            "risk_level": "Low",
            "crop": crop_name or "Crop",
            "analysis_source": "Reference image matcher",
        }

    upload_hist = build_masked_color_histogram(image)
    upload_average_hash = compute_average_hash(image)
    upload_difference_hash = compute_difference_hash(image)
    ranked_matches = []

    for signature in load_disease_reference_signatures():
        entry = signature["entry"]
        feature_distance = score_feature_similarity(upload_features, signature["features"])
        histogram_similarity = float(np.dot(upload_hist, signature["histogram"]))
        average_hash_similarity = hash_similarity(upload_average_hash, signature["average_hash"])
        difference_hash_similarity = hash_similarity(upload_difference_hash, signature["difference_hash"])
        hash_score = (average_hash_similarity + difference_hash_similarity) / 2.0
        signal_overlap = len(set(upload_signals) & set(signature["signals"]))
        score = signal_overlap * 2.6 + histogram_similarity * 7.0 + hash_score * 8.0 - feature_distance * 3.4
        ranked_matches.append(
            {
                "entry": entry,
                "score": score,
                "signal_overlap": signal_overlap,
                "histogram_similarity": histogram_similarity,
                "feature_distance": feature_distance,
                "hash_similarity": hash_score,
            }
        )

    ranked_matches.sort(key=lambda item: item["score"], reverse=True)
    best_match = ranked_matches[0] if ranked_matches else None
    second_match = ranked_matches[1] if len(ranked_matches) > 1 else None

    if best_match is None:
        return None

    confidence = clamp(
        int(
            46
            + best_match["signal_overlap"] * 8
            + best_match["histogram_similarity"] * 35
            + best_match["hash_similarity"] * 18
            + max(0.0, 1.05 - best_match["feature_distance"]) * 22
        ),
        60,
        94,
    )
    score_margin = best_match["score"] - (second_match["score"] if second_match is not None else -999.0)
    if (
        best_match["score"] < 2.35
        or best_match["hash_similarity"] < 0.58
        or (second_match is not None and score_margin < 0.55 and best_match["hash_similarity"] < 0.76)
    ):
        return None

    matched_entry = best_match["entry"]
    return {
        "disease": matched_entry["name"],
        "confidence": max(confidence, base_confidence - 3),
        "cause": str(matched_entry.get("etiology", {}).get("pathogen") or "Reference image match"),
        "symptoms": "; ".join(matched_entry.get("symptoms", [])),
        "organic_solution": "; ".join((matched_entry.get("solution") or {}).get("organic", [])),
        "chemical_solution": "; ".join((matched_entry.get("solution") or {}).get("chemical", [])),
        "prevention": list(matched_entry.get("prevention", [])),
        "diagnostic_reason": "Uploaded image matched the closest disease reference image in the dataset.",
        "risk_level": "Low" if confidence >= 86 else "Medium" if confidence >= 72 else "High",
        "crop": crop_name or "Crop",
        "analysis_source": "Reference image matcher",
    }


def build_dataset_entry_diagnosis(entry, crop_name, analysis_source, diagnostic_reason, confidence_override=None):
    solution = entry.get("solution") or {}
    confidence_value = parse_percentage_value(entry.get("confidence"), 78)
    return {
        "disease": entry["name"],
        "confidence": confidence_override if confidence_override is not None else confidence_value,
        "cause": str(entry.get("etiology", {}).get("pathogen") or "Dataset diagnosis"),
        "symptoms": "; ".join(entry.get("symptoms", [])),
        "organic_solution": "; ".join(solution.get("organic", [])),
        "chemical_solution": "; ".join(solution.get("chemical", [])),
        "prevention": list(entry.get("prevention", [])),
        "etiology": dict(entry.get("etiology") or {}),
        "diagnostic_reason": diagnostic_reason,
        "risk_level": "Low" if confidence_value >= 86 else "Medium" if confidence_value >= 72 else "High",
        "crop": crop_name or "Crop",
        "analysis_source": analysis_source,
        "disease_dataset_found": True,
        "allow_non_dataset_result": False,
    }


def build_filename_dataset_diagnosis(filename, crop_name):
    filename_stem = Path(str(filename or "").strip()).stem
    if not filename_stem:
        return None

    normalized_name = normalize_disease_key(filename_stem.replace("_", " ").replace("-", " "))
    meaningful_tokens = [
        token for token in re.findall(r"[a-z0-9]+", normalized_name)
        if token not in {"img", "image", "photo", "leaf", "plant", "crop", "scan", "upload"}
    ]
    if len(meaningful_tokens) < 1:
        return None

    dataset_entry = find_disease_dataset_entry(normalized_name)
    if dataset_entry is None:
        return None

    entry_key = normalize_disease_key(dataset_entry["name"])
    similarity_ratio = SequenceMatcher(None, normalized_name, entry_key).ratio()
    token_overlap = len(set(meaningful_tokens) & set(re.findall(r"[a-z0-9]+", entry_key)))
    if token_overlap < 1 and similarity_ratio < 0.9:
        return None

    diagnosis = build_dataset_entry_diagnosis(
        dataset_entry,
        crop_name,
        "Filename dataset match",
        "Uploaded file name closely matched a disease name in disease_data.json.",
        confidence_override=max(parse_percentage_value(dataset_entry.get("confidence"), 80), 80),
    )
    diagnosis["matched_filename"] = str(filename)
    return diagnosis


def load_kaggle_reference_signatures():
    global KAGGLE_REFERENCE_SIGNATURE_CACHE

    if KAGGLE_REFERENCE_SIGNATURE_CACHE is not None:
        return KAGGLE_REFERENCE_SIGNATURE_CACHE

    signatures = []
    neutral_weather = {"humidity": 68, "temp": 28}
    seen_files = set()

    for dataset_dir in KAGGLE_REFERENCE_DATASET_DIRS:
        if not dataset_dir.exists():
            continue

        try:
            label_dirs = [item for item in dataset_dir.iterdir() if item.is_dir()]
        except OSError:
            continue

        for label_dir in label_dirs:
            image_paths = [
                path for path in sorted(label_dir.iterdir())
                if path.is_file() and path.suffix.lower() in ALLOWED_IMAGE_SUFFIXES
            ][:3]
            for image_path in image_paths:
                if image_path in seen_files:
                    continue
                seen_files.add(image_path)

                try:
                    ref_image = Image.open(image_path)
                    ref_image = ImageOps.exif_transpose(ref_image).convert("RGB")
                except (UnidentifiedImageError, OSError, ValueError):
                    continue

                ref_features, ref_signals, _ = extract_leaf_features(ref_image, neutral_weather)
                label_name = label_dir.name
                info = dict(DISEASE_KNOWLEDGE.get(label_name, DISEASE_KNOWLEDGE["DEFAULT"]))
                disease_name = str(info.get("disease") or label_name).strip() or label_name
                dataset_entry = find_disease_dataset_entry(disease_name)
                signatures.append(
                    {
                        "label": label_name,
                        "info": info,
                        "disease_name": disease_name,
                        "dataset_entry": dataset_entry,
                        "features": ref_features,
                        "signals": set(ref_signals),
                        "histogram": build_masked_color_histogram(ref_image),
                        "average_hash": compute_average_hash(ref_image),
                        "difference_hash": compute_difference_hash(ref_image),
                    }
                )

    KAGGLE_REFERENCE_SIGNATURE_CACHE = signatures
    return KAGGLE_REFERENCE_SIGNATURE_CACHE


def build_kaggle_reference_diagnosis(image, crop_name, weather):
    upload_features, upload_signals, base_confidence = extract_leaf_features(image, weather)
    upload_hist = build_masked_color_histogram(image)
    upload_average_hash = compute_average_hash(image)
    upload_difference_hash = compute_difference_hash(image)
    ranked_matches = []

    for signature in load_kaggle_reference_signatures():
        feature_distance = score_feature_similarity(upload_features, signature["features"])
        histogram_similarity = float(np.dot(upload_hist, signature["histogram"]))
        average_hash_similarity = hash_similarity(upload_average_hash, signature["average_hash"])
        difference_hash_similarity = hash_similarity(upload_difference_hash, signature["difference_hash"])
        hash_score = (average_hash_similarity + difference_hash_similarity) / 2.0
        signal_overlap = len(set(upload_signals) & set(signature["signals"]))
        score = signal_overlap * 2.2 + histogram_similarity * 6.8 + hash_score * 7.8 - feature_distance * 3.1
        ranked_matches.append(
            {
                "signature": signature,
                "score": score,
                "signal_overlap": signal_overlap,
                "histogram_similarity": histogram_similarity,
                "feature_distance": feature_distance,
                "hash_similarity": hash_score,
            }
        )

    ranked_matches.sort(key=lambda item: item["score"], reverse=True)
    best_match = ranked_matches[0] if ranked_matches else None
    second_match = ranked_matches[1] if len(ranked_matches) > 1 else None
    if best_match is None:
        return None

    score_margin = best_match["score"] - (second_match["score"] if second_match is not None else -999.0)
    if (
        best_match["score"] < 1.9
        or best_match["hash_similarity"] < 0.54
        or (second_match is not None and score_margin < 0.45 and best_match["hash_similarity"] < 0.7)
    ):
        return None

    confidence = clamp(
        int(
            42
            + best_match["signal_overlap"] * 8
            + best_match["histogram_similarity"] * 32
            + best_match["hash_similarity"] * 18
            + max(0.0, 1.02 - best_match["feature_distance"]) * 20
        ),
        58,
        92,
    )
    signature = best_match["signature"]
    dataset_entry = signature.get("dataset_entry")
    if dataset_entry is not None:
        diagnosis = build_dataset_entry_diagnosis(
            dataset_entry,
            crop_name,
            "Kaggle reference dataset",
            "Uploaded image matched the closest PlantVillage/Kaggle-style disease reference.",
            confidence_override=max(confidence, base_confidence - 2),
        )
        diagnosis["kaggle_label"] = signature["label"]
        return diagnosis

    info = dict(signature.get("info") or {})
    disease_name = str(info.get("disease") or signature["label"]).strip() or signature["label"]
    return {
        "disease": disease_name,
        "confidence": max(confidence, base_confidence - 2),
        "cause": info.get("cause") or "Matched with the closest Kaggle disease reference image.",
        "symptoms": info.get("symptoms") or "Visible disease markers matched a Kaggle reference leaf.",
        "organic_solution": info.get("organic_solution") or info.get("recommendation") or "Start with non-chemical field hygiene and scouting.",
        "chemical_solution": info.get("solution") or "Use a labeled crop protection spray only after confirming the disease.",
        "prevention": [info.get("recommendation") or "Continue close monitoring and compare with more samples."],
        "diagnostic_reason": "Uploaded image matched the closest PlantVillage/Kaggle-style disease reference.",
        "risk_level": "Low" if confidence >= 86 else "Medium" if confidence >= 72 else "High",
        "crop": crop_name or "Crop",
        "analysis_source": "Kaggle reference dataset",
        "kaggle_label": signature["label"],
        "disease_dataset_found": False,
        "allow_non_dataset_result": True,
    }


def build_kaggle_dataset_diagnosis(image, crop_name, weather):
    class_index, conf_float, class_name, confidence_pct = predict_with_pytorch(image)
    if class_name is not None and conf_float is not None and int(confidence_pct or 0) >= 58:
        disease_info = get_disease_info(class_index, conf_float)
        dataset_entry = find_disease_dataset_entry(disease_info["disease"])
        if dataset_entry is not None:
            diagnosis = build_dataset_entry_diagnosis(
                dataset_entry,
                crop_name,
                "Kaggle model",
                f"PlantVillage-trained model detected {class_name} ({confidence_pct}%).",
                confidence_override=confidence_pct,
            )
            diagnosis["kaggle_label"] = class_name
            return diagnosis

        if disease_info.get("disease") and disease_info.get("disease") != "Unknown Disease":
            return {
                "disease": disease_info["disease"],
                "confidence": confidence_pct,
                "cause": disease_info.get("cause"),
                "symptoms": disease_info.get("symptoms", ""),
                "organic_solution": disease_info.get("organic_solution") or disease_info.get("recommendation", ""),
                "chemical_solution": disease_info.get("solution"),
                "prevention": [disease_info.get("recommendation", "")],
                "diagnostic_reason": f"PlantVillage-trained model detected {class_name} ({confidence_pct}%).",
                "risk_level": "Low" if int(confidence_pct) > 85 else "Medium" if int(confidence_pct) > 70 else "High",
                "best_product": disease_info.get("best_product", ""),
                "product_link": disease_info.get("product_link", ""),
                "crop": crop_name or "Crop",
                "analysis_source": "Kaggle model",
                "kaggle_label": class_name,
                "disease_dataset_found": False,
                "allow_non_dataset_result": True,
            }

    return build_kaggle_reference_diagnosis(image, crop_name, weather)


def ask_groq_vision_diagnosis(image_bytes, filename, crop_name, weather):
    if not GROQ_API_KEY:
        return None

    suffix = Path(str(filename or "")).suffix.lower() or ".jpg"
    mime_type = {
        ".png": "image/png",
        ".webp": "image/webp",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(suffix, "image/jpeg")
    image_data_url = f"data:{mime_type};base64,{b64encode(image_bytes).decode('ascii')}"
    weather_context = (
        f"Humidity {weather.get('humidity')}%, temp {weather.get('temp')} C"
        if isinstance(weather, dict) else "Weather unknown"
    )

    response_data = fetch_json(
        "https://api.groq.com/openai/v1/chat/completions",
        method="POST",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json_body={
            "model": GROQ_VISION_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a plant disease vision assistant. Return only raw JSON.",
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Crop context: {crop_name or 'Crop'}. {weather_context}. "
                                "Return strict JSON with keys disease, confidence, symptoms, cause, "
                                "organic_solution, chemical_solution, prevention, diagnostic_reason, risk_level, crop."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
    )
    if not isinstance(response_data, dict):
        return None

    choices = response_data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict)
        )
    content = str(content or "").strip()
    if not content:
        return None

    try:
        diagnosis = json.loads(content)
    except (TypeError, ValueError):
        return None

    disease_name = str(diagnosis.get("disease") or "").strip()
    if not disease_name:
        return None

    dataset_entry = find_disease_dataset_entry(disease_name)
    if dataset_entry is not None:
        diagnosis["disease"] = dataset_entry["name"]
        diagnosis["disease_dataset_found"] = True
        diagnosis["allow_non_dataset_result"] = False
    else:
        diagnosis["disease_dataset_found"] = False
        diagnosis["allow_non_dataset_result"] = True

    prevention = diagnosis.get("prevention", [])
    if isinstance(prevention, str):
        prevention = [tip.strip() for tip in re.split(r"[;\n]+", prevention) if tip.strip()]
    diagnosis["prevention"] = prevention if isinstance(prevention, list) else []
    diagnosis["confidence"] = clamp(parse_percentage_value(diagnosis.get("confidence"), 68), 50, 96)
    diagnosis["analysis_source"] = "Groq vision"
    diagnosis["crop"] = diagnosis.get("crop") or crop_name or "Crop"
    diagnosis["diagnostic_reason"] = diagnosis.get("diagnostic_reason") or "Groq reviewed the uploaded leaf image."
    return diagnosis


def build_no_close_match_response(crop_name, preview_url):
    crop_label = str(crop_name or "Crop").strip() or "Crop"
    return {
        "success": True,
        "disease": "Needs Expert Review",
        "confidence": 54,
        "report_title": "Needs Expert Review",
        "confidence_display": "54%",
        "cause": "The uploaded image needs a clearer crop-focused diagnosis review.",
        "symptoms": "Current scan signals are mixed, so a more precise disease confirmation is needed.",
        "organic_solution": "Retake one clear close-up leaf image in daylight from the affected area only.",
        "chemical_solution": "Avoid full-field chemical spraying until the disease is confirmed.",
        "prevention": [
            "Upload a single affected leaf with a plain background.",
            "Avoid blurry, dark, or distant crop photos.",
            "Compare the leaf with the disease guide before treatment."
        ],
        "prevention_tips": [
            "Upload a single affected leaf with a plain background.",
            "Avoid blurry, dark, or distant crop photos.",
            "Compare the leaf with the disease guide before treatment."
        ],
        "etiology": {
            "pathogen": "Not confirmed from current dataset",
            "environment": "Needs a clearer disease-focused image",
            "transmission": "Unknown until a close match is found"
        },
        "symptoms_list": [
            "Image did not clearly match any supported disease reference.",
            "Another disease could look visually similar from this angle."
        ],
        "organic_solutions": [
            "Retake a closer image before making treatment decisions."
        ],
        "chemical_solutions": [
            "Do not start broad spraying until the diagnosis is more reliable."
        ],
        "do_now_checklist": [
            "Capture one close-up of the most affected leaf.",
            "Use daylight and avoid shadow or blur.",
            "Upload the new image and compare the result again."
        ],
        "suggested_products": [],
        "recommended_product": None,
        "best_product": "",
        "product_link": "/market",
        "image_url": preview_url,
        "crop": crop_label,
        "analysis_source": "Expert review fallback",
        "diagnostic_reason": "A safer review fallback was shown instead of forcing the wrong disease result.",
        "why_this_result": "A safer review fallback was shown instead of forcing the wrong disease result.",
        "risk_level": "Medium",
        "consult_expert": "Capture a clearer leaf image or verify manually before applying disease-specific treatment.",
        "matched_symptoms": [],
        "disease_dataset_found": False,
        "library_url": "/library/diseases",
    }


def build_invalid_leaf_upload_response(crop_name, preview_url, leaf_validation=None):
    response = build_no_close_match_response(crop_name, preview_url)
    response.update(
        {
            "disease": "Leaf Image Required",
            "confidence": 18,
            "report_title": "Leaf Image Required",
            "confidence_display": "18%",
            "cause": "The uploaded photo does not appear to be a crop leaf, so disease detection was stopped early.",
            "symptoms": "This image looks more like a non-leaf object or a mixed scene than a close-up crop leaf.",
            "organic_solution": "Upload one clear close-up of a single affected leaf in daylight with a simple background.",
            "chemical_solution": "Do not apply disease-specific spray based on this upload.",
            "prevention": [
                "Capture only the affected leaf, not tools, soil sensors, or packaging.",
                "Keep the leaf centered and fill most of the frame.",
                "Retake the photo in natural light before running the scan again."
            ],
            "prevention_tips": [
                "Capture only the affected leaf, not tools, soil sensors, or packaging.",
                "Keep the leaf centered and fill most of the frame.",
                "Retake the photo in natural light before running the scan again."
            ],
            "symptoms_list": [
                "Leaf validator could not confirm a crop-leaf subject in the uploaded image.",
                "The scan was blocked to avoid a false disease prediction."
            ],
            "organic_solutions": [
                "Retake a close crop-leaf image before relying on AI disease output."
            ],
            "chemical_solutions": [
                "Wait for a valid leaf scan or manual confirmation before treatment."
            ],
            "do_now_checklist": [
                "Pick one affected leaf and hold it against a plain background.",
                "Make sure the leaf is in focus and fills most of the frame.",
                "Upload the new image and scan again."
            ],
            "analysis_source": "Leaf image validator",
            "diagnostic_reason": "The upload was rejected before disease matching because it did not look like a crop leaf image.",
            "why_this_result": "The upload was rejected before disease matching because it did not look like a crop leaf image.",
            "risk_level": "Low",
            "consult_expert": "Retake the image with a clear leaf close-up first, then verify in the field if symptoms persist.",
        }
    )
    if isinstance(leaf_validation, dict):
        response["leaf_validation"] = leaf_validation
    return response


def save_scan_history(user, diagnosis):
    new_history = DiseaseHistory(
        user_id=user.id,
        crop_type=diagnosis.get("crop", user.crop_type or "Crop"),
        detected_disease=diagnosis.get("disease", "Unknown"),
        confidence=int(parse_percentage_value(diagnosis.get("confidence"), 80)),
    )
    db.session.add(new_history)
    db.session.commit()


def build_scan_response_payload(diagnosis, preview_url):
    chemical_solution = diagnosis.get("chemical_solution") or diagnosis.get("solution") or ""
    organic_solution = diagnosis.get("organic_solution") or ""
    prevention = diagnosis.get("prevention", [])
    if isinstance(prevention, str):
        prevention = [prevention]
    return {
        "success": True,
        "disease": diagnosis.get("disease"),
        "confidence": parse_percentage_value(diagnosis.get("confidence"), 80),
        "cause": diagnosis.get("cause"),
        "symptoms": diagnosis.get("symptoms"),
        "organic_solution": organic_solution,
        "chemical_solution": chemical_solution,
        "prevention": prevention,
        "explanation_hinglish": diagnosis.get("explanation_hinglish"),
        "diagnostic_reason": diagnosis.get("diagnostic_reason", "Visual cues identified."),
        "risk_level": diagnosis.get("risk_level"),
        "image_url": preview_url,
        "crop": diagnosis.get("crop", "Crop"),
        "analysis_source": diagnosis.get("analysis_source", "AI diagnosis"),
        "best_product": diagnosis.get("best_product", ""),
        "product_link": diagnosis.get("product_link", ""),
    }


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
    alert_context = sync_user_alerts(user)
    dashboard_map_center = resolve_dashboard_map_center(
        (primary_farm.location if primary_farm and primary_farm.location else user.location) or "Bhubaneswar",
        weather,
    )
    soil = build_soil_profile(user, weather)
    crop_health = build_crop_health(user, weather, soil)
    recommendations = build_recommendations(user, weather, soil, crop_health)
    active_alerts = alert_context.get("active_alerts", [])
    if not isinstance(active_alerts, list):
        active_alerts = []
    
    alerts = []
    for i in range(min(len(active_alerts), 3)):
        alerts.append(serialize_alert_record(active_alerts[i]))
    if not alerts:
        alerts = build_alerts(weather, soil, crop_health)

    ndvi_params = {}
    if dashboard_map_center["lat"] is not None and dashboard_map_center["lng"] is not None:
        ndvi_params = {"lat": dashboard_map_center["lat"], "lon": dashboard_map_center["lng"]}  # type: ignore

    return {
        "weather": weather,
        "soil": soil,
        "crop_health": crop_health,
        "recommendations": recommendations,
        "alerts": alerts,
        "yield_prediction": crop_health.get("yield_prediction") or "Prediction pending",
        "lat": dashboard_map_center["lat"],
        "lon": dashboard_map_center["lng"],
        "map_embed_url": build_map_embed_url(user.location, dashboard_map_center["lat"], dashboard_map_center["lng"]),
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
    map_location = (primary_farm.location if primary_farm and primary_farm.location else user.location) or "Bhubaneswar"
    weather = fetch_weather_bundle(map_location)
    map_center = resolve_dashboard_map_center(map_location, weather)

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
        "primary_map": {
            "lat": map_center["lat"],
            "lng": map_center["lng"],
            "zoom": map_center["zoom"],
            "location": map_location,
        },
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
            return render_template("login.html", error="Security check failed. Please refresh and try again.")

        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        # Shortcut: allow admin credentials on the normal user login form.
        if email.strip().lower() == ADMIN_EMAIL and check_admin_password(password):
            session["admin_authed"] = True
            session["admin_email"] = ADMIN_EMAIL
            return redirect("/admin")

        user = User.query.filter(User.email.ilike(email)).first()
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
            return render_template("register.html", error="Security check failed. Please refresh and try again.")

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
        session["pending_user"] = {
            "name": name,
            "email": email,
            "password_hash": hash_password(password),
            "location": location,
            "crop": crop,
            "phone": phone,
            "profile_photo": profile_photo,
            "referred_by": referral_code
        }
        
        otp = generate_otp()
        email_sent, failure_reason = send_otp_email(email, otp)
        whatsapp_sent, whatsapp_reason = send_otp_whatsapp(phone, otp)
        update_otp_session_state(
            otp,
            email,
            "register",
            email_sent=(email_sent or whatsapp_sent),
            notice=build_otp_notice(
                email_sent,
                failure_reason,
                whatsapp_sent=whatsapp_sent,
                whatsapp_reason=whatsapp_reason,
            ),
        )
        return redirect("/verify-otp")

    return render_template("register.html")


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    if (
        "otp" not in session
        or session.get("otp_type") != "register"
        or "pending_user" not in session
    ):
        clear_otp_session_state()
        return redirect("/login")
        
    error = None
    if request.method == "POST":
        csrf_resp = require_csrf()
        if csrf_resp is not None:
            return render_template("verify_otp.html", **get_otp_page_context(error="Security check failed. Please refresh and try again."))

        user_otp = re.sub(r"\D", "", request.form.get("otp", ""))
        
        # Check expiry
        if datetime.now(timezone.utc).timestamp() > session.get("otp_expiry", 0):
            error = "OTP has expired. Please try again."
        elif user_otp == session["otp"]:
            if "pending_user" in session:
                data = session["pending_user"]
                new_user = User( # type: ignore
                    name=data["name"],
                    email=data["email"],
                    password=data.get("password_hash") or hash_password(data.get("password", "")),
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
                        # - Referrer wallet +₹20
                        # - New user wallet +₹10 (can be used for subscription discount)
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
            error = "Invalid OTP. Please check and try again."
            
    return render_template("verify_otp.html", **get_otp_page_context(error=error))


@app.route("/resend-otp", methods=["POST"])
def resend_otp():
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

    pending_user = session.get("pending_user", {})
    target_phone = pending_user.get("phone") if isinstance(pending_user, dict) else ""
    email_sent, failure_reason = send_otp_email(target_email, otp)
    whatsapp_sent, whatsapp_reason = send_otp_whatsapp(target_phone, otp)
    update_otp_session_state(
        otp,
        target_email,
        otp_type,
        email_sent=(email_sent or whatsapp_sent),
        notice=build_otp_notice(
            email_sent,
            failure_reason,
            whatsapp_sent=whatsapp_sent,
            whatsapp_reason=whatsapp_reason,
        ),
    )
    return render_template("verify_otp.html", **get_otp_page_context())


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


@app.route("/rent-a-tractor")
def rent_a_tractor_page():
    user = get_current_user()
    if not user:
        return redirect("/login")
    if "rent_tractor" in DISABLED_DASHBOARD_MODULES:
        abort(404)
    tractor_page = build_rent_tractor_page_context(user)
    return render_template("rent_tractor.html", user=user, tractor_page=tractor_page)


@app.route("/api/tractor-marketplace")
def api_tractor_marketplace():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    category = str(request.args.get("category") or "all").strip().lower()
    if category not in {item["id"] for item in TRACTOR_SERVICE_CATEGORIES}:
        category = "all"
    sort_option = str(request.args.get("sort") or "nearest").strip().lower()
    if sort_option not in {"nearest", "price", "rating"}:
        sort_option = "nearest"

    payload = build_tractor_marketplace_payload(
        user,
        category=category,
        sort_option=sort_option,
        service_date=request.args.get("service_date"),
        lat=request.args.get("lat", type=float),
        lng=request.args.get("lng", type=float),
    )
    return jsonify({"success": True, **payload})


@app.route("/api/tractor-bookings", methods=["POST"])
def api_tractor_bookings():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    machine = get_tractor_machine_by_id(payload.get("machine_id"))
    if machine is None:
        return jsonify({"success": False, "error": "Machine not found."}), 404

    booking_date = parse_booking_date_value(payload.get("service_date"))
    slot_label = str(payload.get("slot_label") or "").strip()
    valid_slot_labels = {item["label"] for item in get_tractor_slot_options() if item["date"] == booking_date.isoformat()}
    if slot_label not in valid_slot_labels:
        return jsonify({"success": False, "error": "Select a valid time slot."}), 400

    existing_booking = TractorBooking.query.filter_by(
        machine_id=machine["id"],
        booking_date=booking_date,
        slot_label=slot_label,
    ).filter(TractorBooking.booking_status != "cancelled").first()
    if existing_booking is not None:
        return jsonify({"success": False, "error": "This slot was just booked. Please choose another slot."}), 409

    try:
        duration_hours = int(payload.get("duration_hours") or 1)
    except (TypeError, ValueError):
        duration_hours = 1
    duration_hours = max(1, min(duration_hours, 12))

    payment_mode = str(payload.get("payment_mode") or "pay_later").strip().lower()
    if payment_mode not in {"pay_later", "online"}:
        payment_mode = "pay_later"

    try:
        farm_lat = float(payload.get("farm_lat")) if payload.get("farm_lat") is not None else None
    except (TypeError, ValueError):
        farm_lat = None
    try:
        farm_lng = float(payload.get("farm_lng")) if payload.get("farm_lng") is not None else None
    except (TypeError, ValueError):
        farm_lng = None

    total_amount_inr = int(machine["price_per_hour"]) * duration_hours
    booking = TractorBooking(  # type: ignore
        user_id=user.id,
        machine_id=machine["id"],
        machine_name=machine["name"],
        category=machine["category"],
        farm_location=(payload.get("farm_location") or user.location or "").strip(),
        farm_lat=farm_lat,
        farm_lng=farm_lng,
        booking_date=booking_date,
        slot_label=slot_label,
        duration_hours=duration_hours,
        price_per_hour=int(machine["price_per_hour"]),
        total_amount_inr=total_amount_inr,
        payment_mode=payment_mode,
        payment_status="pending" if payment_mode == "online" else "cash_on_service",
        booking_status="confirmed" if payment_mode == "pay_later" else "pending_payment",
        notes=(payload.get("notes") or "").strip(),
    )
    db.session.add(booking)
    db.session.commit()

    payment_order = None
    payment_note = ""
    if payment_mode == "online":
        razorpay_order, error = create_razorpay_order_amount_inr(
            total_amount_inr,
            receipt=f"tractor_{booking.id}",
            notes={"booking_id": str(booking.id), "machine_id": machine["id"], "source": "tractor_service"},
        )
        if razorpay_order:
            payment_order = {
                "order_id": razorpay_order.get("id"),
                "amount": razorpay_order.get("amount"),
                "currency": razorpay_order.get("currency"),
                "key_id": RAZORPAY_KEY_ID,
            }
            payment_note = "Online payment order created."
        else:
            payment_note = error or "Online payment gateway is not available right now."

    return jsonify(
        {
            "success": True,
            "message": f"{machine['name']} booked for {booking.booking_date.strftime('%d %b')} at {slot_label}.",
            "booking": serialize_tractor_booking(booking),
            "payment_order": payment_order,
            "payment_note": payment_note,
        }
    )


@app.route("/land-lease")
def land_lease_page():
    return render_village_module_page("land_lease")


@app.route("/rural-services")
def rural_services_page():
    return render_village_module_page("rural_services")


@app.route("/govt-schemes")
def govt_schemes_page():
    return render_village_module_page("govt_schemes")


@app.route("/money-manager")
def money_manager_page():
    return render_village_module_page("money_manager")


@app.route("/ai-crop-scan")
def ai_crop_scan_page():
    return render_village_module_page("ai_crop_scan")


@app.route("/farming-solutions")
def farming_solutions_page():
    return render_village_module_page("farming_solutions")


@app.route("/agri-market")
def agri_market_page():
    return render_village_module_page("agri_market")


@app.route("/govt-buddy-ai")
def govt_buddy_ai_page():
    return render_village_module_page("govt_buddy_ai")


@app.route("/my-wallet")
def my_wallet_page():
    return render_village_module_page("my_wallet")


@app.route("/notifications")
def notifications_page():
    return render_village_module_page("notifications")


@app.route("/upgrade-hub")
def upgrade_hub_page():
    return render_village_module_page("upgrade_hub")


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
    upsert_task_reminder_alert(task, force_notify=False, send_channels=False)
    send_task_status_email(user, task, "created")

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

    previous_status = str(task.status or "todo")
    task.status = new_status
    task.completed_at = datetime.now(timezone.utc) if new_status == "done" else None
    db.session.commit()
    if new_status == "done":
        deactivate_task_reminder_alert(task.id, user_id=user.id)
        if previous_status != "done":
            send_task_status_email(user, task, "completed")
    else:
        upsert_task_reminder_alert(task, force_notify=False, send_channels=False)
        if new_status == "in_progress" and previous_status != "in_progress":
            send_task_status_email(user, task, "started")

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
    deactivate_task_reminder_alert(task.id, user_id=user.id, commit=False)
    db.session.delete(task)
    db.session.commit()
    remember_notice("farms_notice", f"Task '{deleted_title}' deleted.")
    return redirect("/farms#task-planner")


@app.route("/weather")
def weather_monitoring():
    user = get_current_user()
    if not user:
        return redirect("/login")

    initial_city = (request.args.get("city") or user.location or "Bhubaneswar").strip() or "Bhubaneswar"
    return render_template("weather.html", user=user, initial_weather_city=initial_city)


@app.route("/download/weather-report")
def download_weather_report():
    user = get_current_user()
    if not user:
        return redirect("/login")

    requested_city = (request.args.get("city") or user.location or "Bhubaneswar").strip() or "Bhubaneswar"
    weather_payload = build_openweather_monitor_payload(requested_city)
    current = weather_payload.get("current", {})
    current_temp = current.get("temp", "-")
    current_condition = current.get("condition", "Not available")
    current_humidity = current.get("humidity", "-")
    current_wind = current.get("wind_speed", "-")
    current_pressure = current.get("pressure", "-")
    aqi = weather_payload.get("aqi", {})

    hourly_items = [
        f"{item.get('time', 'Time')}: {item.get('temp', '-')}" + " C, "
        + f"rain {item.get('rain_prob', '-')}%, {item.get('condition', 'forecast')}"
        for item in (weather_payload.get("hourly") or [])[:8]
    ]
    daily_items = [
        f"{item.get('day', 'Day')}: {item.get('min', '-')}" + " C to "
        + f"{item.get('max', '-')}" + f" C, {item.get('condition', 'forecast')}"
        for item in (weather_payload.get("daily") or [])[:7]
    ]

    return build_pdf_download_response(
        filename=f"weather-report-{weather_payload.get('location') or requested_city}",
        title=f"Weather Report - {weather_payload.get('location') or requested_city}",
        meta_lines=[
            f"Farmer: {user.name or 'User'}",
            f"Location: {weather_payload.get('location') or requested_city}",
            f"Updated: {weather_payload.get('updated_at') or datetime.now().strftime('%d %b %Y, %I:%M %p')}",
            f"Source: {'Live OpenWeather' if weather_payload.get('source') == 'openweather' else 'Fallback weather model'}",
        ],
        sections=[
            {
                "heading": "Current Conditions",
                "items": [
                    f"Temperature: {current_temp} C",
                    f"Condition: {current_condition}",
                    f"Feels like: {current.get('feels_like', '-')} C",
                    f"Humidity: {current_humidity}%",
                    f"Wind: {current_wind} km/h",
                    f"Pressure: {current_pressure} hPa",
                    f"AQI: {aqi.get('level', 'Unknown')} ({aqi.get('value', '-')})",
                ],
            },
            {"heading": "Hourly Outlook", "items": hourly_items or ["Hourly forecast unavailable."]},
            {"heading": "Weekly Outlook", "items": daily_items or ["Daily forecast unavailable."]},
            {
                "heading": "AI Advisory",
                "items": compact_pdf_list(weather_payload.get("insights") or [], limit=6, item_length=180)
                or ["No advisory available right now."],
            },
        ],
    )


@app.route("/api/weather")
def api_weather_monitoring():
    requested_city = (request.args.get("city") or "").strip()
    user = get_current_user()
    fallback_city = user.location if user else ""
    city = requested_city or fallback_city or "Bhubaneswar"
    payload = build_openweather_monitor_payload(city)

    if user is not None and requested_city:
        persisted_location = requested_city
        if persisted_location:
            changed = False
            if persisted_location != str(user.location or "").strip():
                user.location = persisted_location
                changed = True

            primary_farm = (
                Farm.query.filter_by(user_id=user.id, is_primary=True)
                .order_by(Farm.created_at.asc())
                .first()
            )
            if primary_farm is not None and persisted_location != str(primary_farm.location or "").strip():
                primary_farm.location = persisted_location
                changed = True

            if changed:
                db.session.commit()
    return jsonify(payload)


@app.route("/soil-health", methods=["GET", "POST"])
def soil_health_monitoring():
    user = get_current_user()
    if not user:
        return redirect("/login")

    submitted_location = ""
    if request.method == "POST":
        submitted_location = str(request.form.get("location") or "").strip()
    elif (request.args.get("apply_location") or "").strip() == "1":
        submitted_location = str(request.args.get("location") or "").strip()

    update_requested = bool(submitted_location) or request.method == "POST"
    if update_requested:
        new_location = submitted_location
        if new_location:
            user.location = new_location
            primary_farm = (
                Farm.query.filter_by(user_id=user.id, is_primary=True)
                .order_by(Farm.created_at.asc())
                .first()
            )
            if primary_farm is not None:
                primary_farm.location = new_location
            db.session.commit()
            db.session.refresh(user)
            return redirect("/soil-health?updated=1")
        return redirect("/soil-health?updated=0")

    location_notice = None
    update_flag = (request.args.get("updated") or "").strip()
    if update_flag == "1":
        location_notice = {
            "tone": "success",
            "text": "Location update ho gaya. Soil pH, NPK, moisture, aur weather metrics naye place ke hisaab se refresh ho gaye."
        }
    elif update_flag == "0":
        location_notice = {
            "tone": "warning",
            "text": "Please ek valid location enter karo."
        }

    soil_page = build_soil_page_context(user)
    return render_template("soil.html", user=user, soil_page=soil_page, location_notice=location_notice)


@app.route("/download/soil-report")
def download_soil_report():
    user = get_current_user()
    if not user:
        return redirect("/login")

    soil_page = build_soil_page_context(user)
    location_name = soil_page["location"]["name"]
    nutrient_items = [
        f"{card['title']}: {card['value']} {card['unit']} ({card['label']})"
        for card in soil_page.get("nutrient_cards", [])
    ]
    summary_items = [f"{item['label']}: {item['value']}" for item in soil_page.get("summary_metrics", [])]
    legend_items = [f"{item['label']}: {item['value']}" for item in soil_page.get("map_card", {}).get("legend", [])]
    recommendation_items = compact_pdf_list(
        soil_page.get("health_panel", {}).get("recommendations", []),
        limit=5,
        item_length=180,
    )

    return build_pdf_download_response(
        filename=f"soil-report-{location_name}",
        title=f"Soil Health Report - {location_name}",
        meta_lines=[
            f"Farmer: {user.name or 'User'}",
            f"Location: {location_name}",
            f"Crop focus: {user.crop_type or 'Mixed Crop'}",
            f"Weather window: {soil_page.get('weather', {}).get('description', 'Current conditions')}",
            f"Generated: {datetime.now().strftime('%d %b %Y, %I:%M %p')}",
        ],
        sections=[
            {
                "heading": "Current Field Snapshot",
                "items": [
                    f"Soil pH: {soil_page['gauge']['value']:.1f} ({soil_page['gauge']['label']})",
                    f"Field moisture: {soil_page['soil']['moisture']}%",
                    f"Nitrogen estimate: {soil_page['soil']['nitrogen']} kg/ha",
                    *[f"{item['label']}: {item['value']}" for item in soil_page.get("toolbar_items", [])],
                ],
            },
            {"heading": "Nutrient Snapshot", "items": nutrient_items},
            {"heading": "Recommendations", "items": recommendation_items or ["Continue weekly sampling and compare zone-wise readings."]},
            {"heading": "Summary Metrics", "items": summary_items},
            {"heading": "Map Legend", "items": legend_items},
        ],
    )


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

    crops = get_library_tips_crop_options()
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


def build_track_order_context(user):
    orders = StoreOrder.query.filter_by(user_id=user.id).order_by(StoreOrder.created_at.desc()).all()
    counts = {
        "total": len(orders),
        "awaiting_payment": 0,
        "pending": 0,
        "confirmed": 0,
        "delivered": 0,
    }
    order_cards = []

    for order in orders:
        product = getattr(order, "product", None)
        payment_status = str(getattr(order, "status", "created") or "created").strip().lower()
        fulfillment_status = get_fulfillment_status(order)
        if payment_status == "paid":
            counts[fulfillment_status] += 1
        else:
            counts["awaiting_payment"] += 1

        timeline = build_order_timeline(order)
        completed_steps = sum(1 for item in timeline if item["completed"])
        progress_percent = int(round((completed_steps / max(len(timeline), 1)) * 100))
        created_at = normalize_timestamp(getattr(order, "created_at", None))
        last_update = normalize_timestamp(getattr(order, "updated_at", None) or getattr(order, "created_at", None))

        if payment_status != "paid":
            status_label = "Awaiting payment"
            status_tone = "payment"
            helper_text = "Payment is still pending, so admin confirmation has not started yet."
        elif fulfillment_status == "pending":
            status_label = "Pending confirmation"
            status_tone = "pending"
            helper_text = "Your payment is received and the admin team still needs to confirm the order."
        elif fulfillment_status == "confirmed":
            status_label = "Confirmed"
            status_tone = "confirmed"
            helper_text = "The admin team has confirmed the order. Delivery is the next stage."
        else:
            status_label = "Delivered"
            status_tone = "delivered"
            helper_text = "This order has been marked as delivered."

        order_cards.append(
            {
                "id": order.id,
                "product_name": get_order_product_name(order, product),
                "image_url": str(getattr(product, "image_url", "") or "/static/brand/agrovision-email-logo.png"),
                "amount_label": build_order_amount_label(order),
                "payment_status_label": payment_status.replace("_", " ").title(),
                "status_label": status_label,
                "status_tone": status_tone,
                "helper_text": helper_text,
                "progress_percent": progress_percent,
                "created_at_label": created_at.strftime("%d %b %Y, %I:%M %p") if created_at else "Just now",
                "last_update_label": format_relative_time(last_update),
                "timeline": [
                    {
                        "label": item["label"],
                        "detail": item["detail"],
                        "completed": item["completed"],
                        "timestamp_label": (
                            item["timestamp"].strftime("%d %b %Y, %I:%M %p")
                            if item["completed"] and item.get("timestamp")
                            else "Waiting"
                        ),
                    }
                    for item in timeline
                ],
            }
        )

    return {
        "active_page": "track_order",
        "title": "Track Order",
        "description": "See whether each product order is pending, confirmed, or delivered from one place.",
        "counts": counts,
        "orders": order_cards,
    }


@app.route("/farm-twin")
def farm_twin():
    return redirect("/track-order")


@app.route("/track-order")
def track_order():
    user = get_current_user()
    if not user:
        return redirect("/login")

    track_order_page = build_track_order_context(user)
    return render_template("track_order.html", user=user, track_order=track_order_page)


@app.route("/download/order-invoice/<int:order_id>")
def download_order_invoice(order_id):
    user = get_current_user()
    if not user:
        return redirect("/login")

    order = StoreOrder.query.filter_by(id=order_id, user_id=user.id).first()
    if order is None:
        abort(404)

    order_card = next(
        (item for item in build_track_order_context(user)["orders"] if int(item.get("id") or 0) == int(order_id)),
        None,
    )
    if order_card is None:
        abort(404)

    payment_reference = str(
        getattr(order, "razorpay_payment_id", "")
        or getattr(order, "razorpay_order_id", "")
        or f"order-{order_id}"
    ).strip()

    return build_pdf_download_response(
        filename=f"order-invoice-{order_id}",
        title=f"Order Invoice - #{order_id}",
        meta_lines=[
            f"Customer: {user.name or 'User'}",
            f"Email: {user.email or 'Not available'}",
            f"Order ID: #{order_id}",
            f"Generated: {datetime.now().strftime('%d %b %Y, %I:%M %p')}",
        ],
        sections=[
            {
                "heading": "Order Summary",
                "items": [
                    f"Product: {order_card['product_name']}",
                    f"Amount paid: {order_card['amount_label']}",
                    f"Payment status: {order_card['payment_status_label']}",
                    f"Fulfillment status: {order_card['status_label']}",
                    f"Order placed: {order_card['created_at_label']}",
                    f"Last update: {order_card['last_update_label']}",
                    f"Payment reference: {payment_reference}",
                ],
            },
            {
                "heading": "Status Guidance",
                "paragraphs": [order_card["helper_text"]],
                "items": [f"{item['label']}: {item['detail']} ({item['timestamp_label']})" for item in order_card.get("timeline", [])],
            },
        ],
    )


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
    cached_disease_report = session.get("last_disease_report_pdf")
    has_cached_pdf = (
        isinstance(cached_disease_report, dict)
        and int(cached_disease_report.get("user_id", 0) or 0) == int(user.id)
    ) or bool(history_records)
    return render_template(
        "disease_detection.html",
        user=user,
        disease_page=disease_page,
        history=history_records,
        has_cached_pdf=has_cached_pdf,
    )


@app.route("/download/disease-report")
def download_disease_report():
    user = get_current_user()
    if not user:
        return redirect("/login")

    cached_payload = session.get("last_disease_report_pdf")
    is_valid_cache = isinstance(cached_payload, dict)
    if is_valid_cache:
        cached_user_id = int(str(cached_payload.get("user_id") or 0))
        if cached_user_id != int(user.id):
            is_valid_cache = False

    if not is_valid_cache:
        cached_payload = build_fallback_disease_pdf_payload(user)
    if not cached_payload:
        abort(404)

    disease_name = cached_payload.get("disease") or "crop-disease"
    sections = [
        {
            "heading": "Diagnosis Summary",
            "items": [
                f"Crop: {cached_payload.get('crop') or user.crop_type or 'Crop'}",
                f"Disease: {disease_name}",
                f"Confidence: {cached_payload.get('confidence') or 'Not available'}",
                f"Risk level: {cached_payload.get('risk_level') or 'Needs review'}",
                f"Source: {cached_payload.get('analysis_source') or 'AI diagnosis'}",
            ],
            "paragraphs": [cached_payload.get("diagnostic_reason") or "Diagnosis summary not available."],
        },
        {
            "heading": "Etiology",
            "items": [
                f"Cause: {cached_payload.get('cause') or 'Field confirmation required'}",
                f"Pathogen: {cached_payload.get('pathogen') or 'Not available'}",
                f"Environment: {cached_payload.get('environment') or 'Not available'}",
                f"Transmission: {cached_payload.get('transmission') or 'Monitor nearby plants'}",
            ],
        },
        {"heading": "Symptoms", "items": cached_payload.get("symptoms") or ["Symptoms were not cached in the last report."]},
        {"heading": "Organic Solution", "items": cached_payload.get("organic_solutions") or ["Organic guidance not available."]},
        {"heading": "Chemical Solution", "items": cached_payload.get("chemical_solutions") or ["Chemical guidance not available."]},
        {"heading": "Prevention Tips", "items": cached_payload.get("prevention_tips") or ["Review field hygiene and rescout the patch."]},
        {"heading": "Do This Now", "items": cached_payload.get("do_now_checklist") or ["Inspect nearby leaves before treating the full block."]},
    ]
    if cached_payload.get("suggested_products"):
        sections.append({"heading": "Suggested Products", "items": cached_payload["suggested_products"]})

    return build_pdf_download_response(
        filename=f"disease-report-{disease_name}",
        title=cached_payload.get("report_title") or f"{disease_name} Disease Report",
        meta_lines=[
            f"Farmer: {user.name or 'User'}",
            f"Location: {cached_payload.get('location') or user.location or 'Unknown'}",
            f"Generated: {cached_payload.get('generated_at') or datetime.now().strftime('%d %b %Y, %I:%M %p')}",
        ],
        sections=sections,
    )


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


@app.route("/market/recommendation/<product_slug>", methods=["GET"])
def market_recommendation_detail(product_slug):
    user = get_current_user()
    if not user:
        return redirect("/login")

    asset_name = (request.args.get("asset") or product_slug).strip()
    disease_name = (request.args.get("disease") or "").strip()
    crop_name = (request.args.get("crop") or user.crop_type or "Crop").strip() or "Crop"
    disease_entry = find_disease_dataset_entry(disease_name) or {
        "name": disease_name or format_product_asset_name(asset_name),
        "etiology": build_fallback_etiology({"disease": disease_name, "crop": crop_name}),
        "symptoms": [],
        "solution": {"organic": [], "chemical": []},
        "prevention": [],
    }
    product_data = build_virtual_store_product(
        asset_name,
        disease_entry,
        {"disease": disease_name or disease_entry.get("name"), "crop": crop_name},
    )
    raw_related = build_store_page_context(search_query=product_data["name"]).get("products", [])
    related_products = []
    if isinstance(raw_related, list):
        related_products = [raw_related[i] for i in range(min(len(raw_related), 4))]
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

    was_paid = str(getattr(order_record, "status", "") or "").strip().lower() == "paid"
    notes = get_order_notes(order_record)
    notes.setdefault("fulfillment_status", "pending")

    razorpay_order_id = str(payload.get("razorpay_order_id") or order_record.razorpay_order_id or "")
    razorpay_payment_id = str(payload.get("razorpay_payment_id") or f"demo_pay_{uuid.uuid4().hex[:10]}")
    razorpay_signature = str(payload.get("razorpay_signature") or "")
    signature_verified = verify_razorpay_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature)

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
    if not get_order_status_timestamps(order_record).get("pending"):
        set_order_status_timestamp(order_record, "pending")

    if not was_paid:
        user.loyalty_points = int(user.loyalty_points or 0) + max(5, int(product.price / 40))
    db.session.commit()

    if not was_paid:
        send_admin_order_email(order_record, user, product)
        send_user_order_email(order_record, user, product, "placed")
        upsert_order_status_alert(user, order_record, product, "placed")

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
    error = (request.args.get("error") or "").strip() or None
    success = (request.args.get("success") or "").strip() or None

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
    product_summary = {
        "total": len(products),
        "organic": sum(1 for product in products if (product.category or "") == "Organic"),
        "active": sum(1 for product in products if bool(product.is_active)),
        "low_stock": sum(1 for product in products if int(product.stock or 0) <= 5),
    }
    return render_template(
        "admin/products.html",
        products=products,
        categories=[c for c in STORE_CATEGORY_ORDER if c != "All"],
        product_summary=product_summary,
        error=error,
        success=success,
    )


@app.route("/admin/products/sync", methods=["POST"])
@admin_required
def admin_sync_products():
    try:
        seeded_count = seed_store_products()
        message = f"Agro Market dataset synced. {seeded_count} products refreshed from database seed."
        return redirect(f"/admin/products?success={quote(message)}")
    except Exception as exc:
        db.session.rollback()
        message = f"Sync failed: {exc}"
        return redirect(f"/admin/products?error={quote(message)}")


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
    previous_status = get_fulfillment_status(order)
    try:
        set_fulfillment_status(order, new_status)
    except ValueError:
        return redirect("/admin/orders")

    if previous_status != new_status:
        set_order_status_timestamp(order, new_status)
    db.session.commit()
    if previous_status != new_status and new_status in {"confirmed", "delivered"}:
        send_user_order_email(order, getattr(order, "buyer", None), getattr(order, "product", None), new_status)
        upsert_order_status_alert(getattr(order, "buyer", None), order, getattr(order, "product", None), new_status)
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


@app.route("/predict-disease", methods=["POST"])
def predict_disease():
    import io
    import json
    from pathlib import Path

    from PIL import Image, ImageOps, UnidentifiedImageError  # type: ignore
    
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
    leaf_validation = evaluate_leaf_upload(image)
    if not bool(leaf_validation.get("is_probable_leaf")):
        return jsonify(build_invalid_leaf_upload_response(user.crop_type or "Crop", preview_url, leaf_validation))

    filename_diagnosis = build_filename_dataset_diagnosis(uploaded_file.filename, user.crop_type or "Crop")
    if filename_diagnosis is not None:
        save_scan_history(user, filename_diagnosis)
        response_payload = attach_store_recommendation(
            build_scan_response_payload(filename_diagnosis, preview_url),
            filename_diagnosis.get("best_product", ""),
        )
        return jsonify_disease_result(user, response_payload)

    reference_diagnosis = build_reference_image_diagnosis(image, user.crop_type or "Crop", weather)
    if reference_diagnosis is not None and int(str(reference_diagnosis.get("confidence") or 0)) >= 66:
        if isinstance(reference_diagnosis, dict):
            reference_diagnosis["explanation_hinglish"] = "Diagnosis generated from uploaded leaf pattern and dataset reference images."
        save_scan_history(user, reference_diagnosis)
        response_payload = attach_store_recommendation(
            build_scan_response_payload(reference_diagnosis, preview_url),
            reference_diagnosis.get("best_product", ""),
        )
        return jsonify_disease_result(user, response_payload)

    kaggle_diagnosis = build_kaggle_dataset_diagnosis(image, user.crop_type or "Crop", weather)
    if kaggle_diagnosis is not None and int(kaggle_diagnosis.get("confidence") or 0) >= 58:
        kaggle_diagnosis.setdefault("explanation_hinglish", "Diagnosis generated from Kaggle/PlantVillage disease references.")
        save_scan_history(user, kaggle_diagnosis)
        response_payload = attach_store_recommendation(
            build_scan_response_payload(kaggle_diagnosis, preview_url),
            kaggle_diagnosis.get("best_product", ""),
        )
        return jsonify_disease_result(user, response_payload)
    
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
    groq_used = False
    groq_diagnosis = ask_groq_vision_diagnosis(image_bytes, uploaded_file.filename, user.crop_type or "Crop", weather)
    if groq_diagnosis is not None:
        diagnosis = groq_diagnosis
        success = True
        groq_used = True

    try:
        if success:
            raise StopIteration("Groq diagnosis already available.")
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
    except StopIteration:
        pass
    except Exception as e:
        print(f"AI Detection failed: {e}")
        features, signals, base_confidence = extract_leaf_features(image, weather)
        seed_value = int(sha1(image_bytes[:512]).hexdigest(), 16) % 65537
        best_entry, crop_display, confidence = select_visual_disease_entry(
            user.crop_type,
            features,
            weather,
            signals,
            seed=seed_value,
        )

        diagnosis = {
            "disease": best_entry["name"],
            "confidence": confidence or base_confidence,
            "symptoms": "; ".join(best_entry.get("symptoms", [])) or "Visible spots and stress markers observed on leaf.",
            "cause": best_entry["cause"],
            "organic_solution": best_entry["solution"],
            "chemical_solution": best_entry["solution"],
            "prevention": best_entry["prevention_tips"],
            "explanation_hinglish": "Detailed diagnosis generated using visual fallback analysis.",
            "diagnostic_reason": "Visual feature analysis matched the uploaded leaf against disease patterns.",
            "risk_level": "Low" if int(confidence) > 85 else "Medium" if int(confidence) > 70 else "High",
            "crop": crop_display,
        }

    dataset_entry = find_disease_dataset_entry(diagnosis.get("disease"))
    allow_non_dataset_result = bool(diagnosis.get("allow_non_dataset_result"))
    if dataset_entry is None and not allow_non_dataset_result:
        return jsonify(build_no_close_match_response(user.crop_type or "Crop", preview_url))

    if dataset_entry is not None:
        diagnosis["disease"] = dataset_entry["name"]

    diagnosis["analysis_source"] = diagnosis.get("analysis_source") or ("Groq vision" if groq_used else "Expert AI" if success else "Visual fallback")
    save_scan_history(user, diagnosis)
    response_payload = attach_store_recommendation(
        build_scan_response_payload(diagnosis, preview_url),
        diagnosis.get("best_product", ""),
    )
    return jsonify_disease_result(user, response_payload)


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
    map_location = (primary_farm.location if primary_farm and primary_farm.location else user.location) or "Bhubaneswar"
    weather = fetch_weather_bundle(map_location)
    soil = build_soil_profile(user, weather)
    crop_health = build_crop_health(user, weather, soil)
    map_center = resolve_dashboard_map_center(map_location, weather)
    
    # Safe data parsing
    health_score = int(crop_health.get('score', 80))
    soil_ph = float(soil.get('ph', 6.5))
    soil_n = int(soil.get('nitrogen', 50))
    lat = float(map_center["lat"])
    lon = float(map_center["lng"])
    
    fields = [
        {
            "id": 1,
            "name": f"{(primary_farm.name if primary_farm else (user.crop_type or 'Farm'))} - North",
            "health": f"{health_score}%",
            "soil": f"pH {soil_ph}, N {soil_n}%",
            "alerts": "No critical alerts" if health_score > 70 else "Low nitrogen detected",
            "coords": [lat, lon]
        },
        {
            "id": 2,
            "name": f"{(primary_farm.name if primary_farm else (user.crop_type or 'Farm'))} - South",
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

    alert_context = sync_user_alerts(user)
    recommendations = build_recommendations(
        user,
        alert_context["weather"],
        alert_context["soil"],
        alert_context["crop_health"],
    )
    active_alerts = alert_context["active_alerts"]
    history_alerts = alert_context["history_alerts"]
    preferences = alert_context["preferences"]
    mobile_delivery_ready = bool(
        getattr(preferences, "sms_alerts", False)
        and (TWILIO_SMS_FROM or TWILIO_WHATSAPP_FROM)
        and TWILIO_ACCOUNT_SID
        and TWILIO_AUTH_TOKEN
        and (getattr(preferences, "alert_phone", "") or getattr(user, "phone", ""))
    )
    email_delivery_ready = bool(
        getattr(preferences, "email_alerts", False)
        and SMTP_EMAIL
        and SMTP_PASSWORD
        and (getattr(preferences, "alert_email", "") or getattr(user, "email", ""))
    )

    alerts_data = {
        "alert_cards": [serialize_alert_record(item) for item in active_alerts],
        "recommendations": recommendations[:3],  # type: ignore
        "history": [serialize_alert_record(item) for item in history_alerts[:6]],
        "history_chart": build_alert_history_chart(history_alerts, days=7),
        "unread_count": sum(1 for item in active_alerts if not bool(getattr(item, "is_read", False))),
        "delivery_status": {
            "email_ready": email_delivery_ready,
            "mobile_ready": mobile_delivery_ready,
            "mobile_label": "WhatsApp" if TWILIO_USE_WHATSAPP else "SMS",
        },
    }

    return render_template("alerts.html", user=user, alerts_page=alerts_data)


@app.route("/alerts/mark-all-read", methods=["POST"])
def alerts_mark_all_read():
    user = get_current_user()
    if not user:
        return redirect("/login")

    csrf_resp = require_csrf()
    if csrf_resp is not None:
        return csrf_resp

    AlertRecord.query.filter_by(user_id=user.id, is_active=True, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect("/alerts")


@app.route("/alerts/<int:alert_id>/open")
def alerts_open(alert_id):
    user = get_current_user()
    if not user:
        return redirect("/login")

    alert = AlertRecord.query.filter_by(id=alert_id, user_id=user.id).first()
    if alert is None:
        return redirect("/alerts")

    alert.is_read = True
    db.session.commit()
    return redirect(alert.action_url or "/alerts")


@app.route("/api/ai-chat", methods=["POST"])
def ai_chat():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query") or "").strip()
    if not query:
        return jsonify({"success": False, "error": "No query provided"}), 400

    result = resolve_ai_chat_response(user, query, payload.get("history"))
    return jsonify({"success": True, "response": result["response"], "provider": result["provider"]})


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
        {"icon": "fertilizer", "title": "Fertilizer Management", "detail": "Customized fertilizer recommendations for nitrogen deficiency"},
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
    return render_template(
        "subscriptions.html",
        user=user,
        plans=SUBSCRIPTION_PLANS,
        trial_active=is_trial_active(user),
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
                "Authorization": f"Basic {b64encode(f'{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}'.encode()).decode('ascii')}",
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


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
