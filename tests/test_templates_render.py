from __future__ import annotations

from flask import Flask, render_template


class Obj:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def make_app() -> Flask:
    app = Flask(__name__, template_folder="../templates")
    app.jinja_env.globals["csrf_token"] = lambda: "test"
    return app


def test_admin_product_edit_renders():
    app = make_app()
    product = Obj(
        id=1,
        name="Demo Product",
        price=100,
        category="Tools",
        stock=10,
        image_url="",
        description="",
        is_active=True,
    )
    with app.test_request_context("/admin/products/1/edit"):
        html = render_template(
            "admin/product_edit.html",
            product=product,
            categories=["Tools", "Seeds"],
            error=None,
            success=None,
        )
    assert "Edit Product" in html


def test_dashboard_renders():
    app = make_app()

    user = Obj(name="Test User", location="Testville", crop_type="Wheat")
    weather = Obj(
        temp=28, description="Clear", slider_percent=70, rainfall_mm=0, humidity=60, updated_at="now"
    )
    soil_metrics = [
        Obj(label="pH", tone="blue", fill=65, value_display="6.5"),
        Obj(label="Nitrogen", tone="green", fill=40, value_display="40%"),
        Obj(label="Moisture", tone="blue", fill=55, value_display="55%"),
    ]
    dashboard = Obj(
        primary_farm_name="Primary Farm",
        weather=weather,
        farm_stats=Obj(count=1),
        task_summary=Obj(open_count=0),
        soil=Obj(metrics=soil_metrics, moisture=44),
        crop_health=Obj(score=90, label="Good", crop_name="Wheat"),
        ndvi_preview_url="https://example.com/x.png",
        recommendations=[Obj(title="Tip", detail="Do X")],
        alerts=[Obj(severity="low", title="All good", detail="No issues")],
    )
    carbon = Obj(co2_tonnes=1.2, credits=10, impact_level="Good")

    with app.test_request_context("/dashboard"):
        html = render_template("dashboard.html", user=user, dashboard=dashboard, carbon=carbon)

    assert "AgroVisionAI Dashboard" in html
    assert "Soil Data" in html


def test_verify_otp_renders():
    app = make_app()
    with app.test_request_context("/verify-otp"):
        html = render_template(
            "verify_otp.html",
            target="test@example.com",
            error=None,
            notice="Notice",
            dev_otp="123456",
        )
    assert "Security Check" in html


def test_village_module_renders():
    app = make_app()
    user = Obj(
        name="Village User",
        location="Cuttack",
        crop_type="Rice",
        plan="free",
        wallet_balance=25,
        loyalty_points=10,
    )
    module = {
        "active_page": "notifications",
        "title": "Notifications",
        "badge": "Farm updates and reminders",
        "description": "Notification center for weather, disease, and task activity.",
        "stats": [
            {"label": "Alert types on", "value": "3"},
            {"label": "Channels active", "value": "2"},
            {"label": "Open tasks", "value": "1"},
        ],
        "panel_title": "Control what reaches you",
        "panel_text": "Review recent events and settings from one page.",
        "actions": [
            {"label": "Open Alerts", "href": "/alerts"},
            {"label": "Notification Settings", "href": "/settings"},
        ],
        "cards": [
            {"icon": "fa-bell", "title": "Critical alert center", "detail": "Review farm-triggered updates."},
            {"icon": "fa-list-check", "title": "Task reminders", "detail": "Stay ahead of pending work."},
            {"icon": "fa-sliders", "title": "Delivery controls", "detail": "Tune how alerts are delivered."},
        ],
        "feed_title": "Recent activity feed",
        "feed_entries": [
            {
                "title": "Weather warning ready",
                "detail": "Rainfall alert available.",
                "meta": "1h ago",
                "badge": "Ready",
                "badge_tone": "positive",
            }
        ],
    }

    with app.test_request_context("/notifications"):
        html = render_template("village_module.html", user=user, module=module)

    assert "Notifications - AgroVision AI" in html
    assert "Recent activity feed" in html


def test_rent_tractor_template_renders():
    app = make_app()
    user = Obj(name="Bikash", location="Puri", crop_type="Paddy")
    tractor_page = {
        "title": "Rent a Tractor",
        "active_page": "rent_tractor",
        "location_label": "Puri",
        "farm_name": "Demo Farm",
        "map_center": {"lat": 19.8135, "lng": 85.8312, "zoom": 14},
        "markers": [
            {"lat": 19.8135, "lng": 85.8312, "title": "Nearest partner", "active": True},
        ],
        "categories": [
            {"id": "all", "label": "All Services"},
            {"id": "land_preparation", "label": "Land Preparation"},
        ],
        "sort_options": [
            {"id": "nearest", "label": "Nearest"},
            {"id": "price", "label": "Lowest price"},
        ],
        "selected_category": "land_preparation",
        "selected_sort": "nearest",
        "selected_date": "2026-03-19",
        "booking_stats": {"marketplace_machines": 12, "categories": 5, "active_bookings": 1},
        "booking_history": [],
        "ai_recommendation": {"title": "AI recommendation", "detail": "Use the nearest rotavator first."},
        "payment_enabled": False,
        "service_types": [
            {
                "id": "land_preparation",
                "title": "Land Preparation",
                "subtitle": "Plough, Rotavator, Cultivator",
                "icon": "fa-tractor",
                "machines": [
                    {
                        "name": "Rotavator (7ft)",
                        "power": "45HP+",
                        "eta": "10 min away",
                        "price_inr": 800,
                        "unit": "/hr",
                    },
                ],
            }
        ],
        "machines": [
            {
                "id": "rotavator-7ft-puri",
                "name": "Rotavator (7ft)",
                "hp": "45HP+",
                "distance_label": "10 min away",
                "distance_km": 2.5,
                "price_per_hour": 800,
                "availability_label": "Available now",
                "rating": 4.8,
                "rating_count": 124,
                "features": ["Fast soil turning"],
                "is_available": True,
                "slot_options": [{"date": "2026-03-19", "label": "08:00 AM", "display": "19 Mar • 08:00 AM"}],
                "category_meta": {"title": "Land Preparation", "icon": "fa-tractor"},
            }
        ],
        "default_service_id": "land_preparation",
        "avatar_label": "B",
    }

    with app.test_request_context("/rent-a-tractor"):
        html = render_template("rent_tractor.html", user=user, tractor_page=tractor_page)

    assert "Farm location for service?" in html
    assert "Select Service Type" in html
    assert "Land Preparation" in html
