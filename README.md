# AgroVision AI

AgroVision AI is a Flask-based agriculture platform with dashboards for weather, soil health, crop monitoring, disease detection, alerts, and an admin panel for store products/orders.

## Key Features
- Crop disease detection (optional PyTorch model)
- Weather and soil insights
- Smart alerts and recommendations
- Store + orders + admin management
- Light/Dark mode UI

## Quick Start

1) Create a virtualenv and install deps:

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt

2) Run the app:

    python app.py

Then open the printed local URL and log in.

## Configuration (Env)
Common variables:
- `FLASK_SECRET_KEY`: session signing key
- `ADMIN_EMAIL`, `ADMIN_PASSWORD`: admin login
- `SMTP_EMAIL`, `SMTP_PASSWORD`, `SMTP_SERVER`, `SMTP_PORT`: OTP email
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`): AI assistant features

## Development

Install dev tools:

    pip install -r requirements-dev.txt

Run checks:

    ruff check .
    black --check .
    pytest -q

## Optional: PyTorch
Disease prediction uses PyTorch if installed. The app falls back gracefully if `torch/torchvision` are missing.

