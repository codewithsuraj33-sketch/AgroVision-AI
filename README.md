# AgroVision AI

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10+-1F6FEB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Web_App-0F172A?style=for-the-badge&logo=flask&logoColor=white)
![SQLite/Postgres](https://img.shields.io/badge/DB-SQLite%20%7C%20Postgres-2E7D32?style=for-the-badge&logo=postgresql&logoColor=white)
![Railway Ready](https://img.shields.io/badge/Deploy-Railway-0B1020?style=for-the-badge&logo=railway&logoColor=white)
![AI Enabled](https://img.shields.io/badge/AI-Gemini%20Enabled-C48A00?style=for-the-badge)

**A full-stack smart farming platform for weather, soil health, crop monitoring, disease detection, farm operations, agri-commerce, and AI-guided decisions.**

</div>

---

## Overview

AgroVision AI is a Flask-based agriculture platform built to bring multiple farm workflows into one system. It combines live weather assistance, soil health insights, NDVI-style crop monitoring, disease detection, AI chat guidance, farm tracking, orders, subscriptions, alerts, admin controls, and downloadable PDF reports.

The project is designed like a practical farmer dashboard instead of a single-feature demo. It includes both user-facing tools and admin/store management so it can work as a complete agriculture product foundation.

## Visual Palette

This README follows the same product direction as the app: clean agriculture tones, strong contrast, and modern dashboard styling.

| Role | Color | Hex |
| --- | --- | --- |
| Primary Green | ![#2E7D32](https://via.placeholder.com/18/2E7D32/2E7D32.png) | `#2E7D32` |
| Deep Forest | ![#163A24](https://via.placeholder.com/18/163A24/163A24.png) | `#163A24` |
| Soft Lime | ![#B8E26B](https://via.placeholder.com/18/B8E26B/B8E26B.png) | `#B8E26B` |
| Soil Gold | ![#C48A00](https://via.placeholder.com/18/C48A00/C48A00.png) | `#C48A00` |
| Sky Blue | ![#1F6FEB](https://via.placeholder.com/18/1F6FEB/1F6FEB.png) | `#1F6FEB` |
| Slate Dark | ![#0F172A](https://via.placeholder.com/18/0F172A/0F172A.png) | `#0F172A` |

## Core Modules

| Module | What it covers |
| --- | --- |
| Dashboard | Unified farm summary with weather, tasks, signals, and quick actions |
| Weather Monitoring | Location-based weather view with PDF export |
| Soil Health | Soil pH, NPK, moisture, map signals, location updates, and PDF report |
| Crop Monitoring | NDVI-style monitoring, trend insights, and health-oriented cards |
| Disease Detection | Leaf disease prediction flow with downloadable report |
| Crop & Disease Library | Crop info, disease details, tips, and alert knowledge pages |
| Farms | Farm records, primary farm selection, tasks, and management tools |
| Market & Orders | Storefront, recommendations, checkout, order tracking, and invoices |
| AI Insights | AI chat and insight features powered by Gemini-style integration |
| Alerts & Notifications | Field alerts, read-state flow, and monitoring context |
| Subscriptions & Wallet | Upgrade flows, wallet handling, payments, and referral features |
| Admin Panel | Admin login, products, mappings, orders, and sync utilities |

## Highlights

- Weather, soil, crop monitoring, market, and alerts in one app
- AI Crop Doctor style Q&A with local dataset fallback
- Disease detection with optional PyTorch support
- PDF downloads for weather, soil, order invoice, and disease reports
- Farm and task management workflow
- Admin store and order operations
- Subscription and payment flow
- Railway-ready deployment setup

## Project Structure

```text
AgroVision-AI/
|-- app.py
|-- templates/
|-- static/
|-- dataset/
|-- models/
|-- tests/
|-- requirements.txt
|-- requirements-dev.txt
|-- Procfile
|-- README.md
```

## Tech Stack

| Layer | Stack |
| --- | --- |
| Backend | Flask, Flask-SQLAlchemy, Jinja2 |
| Database | SQLite by default, PostgreSQL-ready via `DATABASE_URL` |
| Frontend | HTML, CSS, JavaScript, Jinja templates |
| AI | Gemini API integration and local crop-doctor knowledge fallback |
| ML | Optional PyTorch/Torchvision disease model support |
| Payments | Razorpay flow integration |
| Email | SMTP-based OTP / notifications |
| Deployment | Railway + Gunicorn |
| Quality | Pytest, Ruff, Black |

## Main Routes

| Route | Purpose |
| --- | --- |
| `/dashboard` | Main smart farming dashboard |
| `/weather` | Weather monitoring |
| `/soil-health` | Soil monitoring and location-based signals |
| `/crop-monitoring` | Crop health monitoring |
| `/disease-detection` | Disease scan UI |
| `/farms` | Farm and task management |
| `/market` | Product marketplace |
| `/track-order` | Order tracking and invoice access |
| `/library` | Crop and disease knowledge library |
| `/ai-insights` | AI assistant and insights |
| `/subscriptions` | Premium plan and payment flow |
| `/admin` | Admin dashboard |

## Local Setup

### 1. Create and activate virtual environment

```powershell
python -m venv .venv
.venv\Scripts\activate
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure environment

Create a `.env` file or use your local environment variables.

```env
FLASK_SECRET_KEY=your_secret_key
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=strong_password
GEMINI_API_KEY=your_api_key
SMTP_EMAIL=your_email@example.com
SMTP_PASSWORD=your_email_app_password
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
DATABASE_URL=
```

### 4. Run the app

```powershell
python app.py
```

Then open the local URL shown in the terminal.

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `FLASK_SECRET_KEY` | Session and app secret |
| `ADMIN_EMAIL` | Admin login email |
| `ADMIN_PASSWORD` | Admin login password |
| `GEMINI_API_KEY` | AI features key |
| `GOOGLE_API_KEY` | Alternate AI key if used |
| `SMTP_EMAIL` | Outgoing email account |
| `SMTP_PASSWORD` | SMTP password or app password |
| `SMTP_SERVER` | SMTP host |
| `SMTP_PORT` | SMTP port |
| `DATABASE_URL` | PostgreSQL or external DB connection string |

## Development

Install development tools:

```powershell
pip install -r requirements-dev.txt
```

Run checks:

```powershell
python -m ruff check tests test_ai_crop_doctor_local_qa.py disease_knowledge.py
python -m black --check tests test_ai_crop_doctor_local_qa.py disease_knowledge.py
python -m py_compile app.py
python -m pytest tests test_ai_crop_doctor_local_qa.py -q
```

## Deployment on Railway

This repo is already prepared for Railway deployment using Gunicorn and `Procfile`.

### Basic Railway flow

1. Push the project to GitHub.
2. Create a new Railway project.
3. Choose `Deploy from GitHub Repo`.
4. Select this repository.
5. Add environment variables in Railway.
6. Generate a public domain.
7. Optionally attach PostgreSQL and map `DATABASE_URL`.

### Start command

```text
gunicorn app:app --bind 0.0.0.0:$PORT
```

### Database for deploy

- Local development can use SQLite.
- Production should use PostgreSQL on Railway.
- The app supports `DATABASE_URL`, so you can attach a Railway Postgres service directly.

## Optional ML Support

Disease prediction can use PyTorch if installed. If `torch` and `torchvision` are unavailable, the app can still run with graceful fallback behavior for non-ML parts.

## Suggested Demo Flow

If you are showing the project to someone, this is a strong walkthrough path:

1. Open dashboard
2. Show weather monitoring
3. Change soil location and export PDF
4. Open crop monitoring
5. Run disease detection
6. Visit market and track order
7. Show AI insights
8. Open admin panel

## Why This Project Stands Out

- It is not a single isolated ML page
- It connects field monitoring, AI help, commerce, and operations
- It is deployable as a real product base
- It includes both user and admin workflows
- It has room for live APIs, satellite data, and production scaling

## Future Improvement Areas

- Real satellite-backed crop monitoring
- Stronger live soil and geospatial data sources
- Better background jobs for alerts
- Persistent production media storage
- Richer analytics and reports
- Full PostgreSQL-first production setup

## Screenshots

You can later add product screenshots here:

```md
![Dashboard](docs/screenshots/dashboard.png)
![Soil Health](docs/screenshots/soil-health.png)
![Crop Monitoring](docs/screenshots/crop-monitoring.png)
```

## License

This project is currently maintained as a custom application workspace. Add your preferred license here if you plan to open-source it publicly.

---

<div align="center">

**AgroVision AI**  
Smart farming workflows, AI assistance, and agriculture operations in one platform.

</div>
