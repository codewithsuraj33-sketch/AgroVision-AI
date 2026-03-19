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
    weather = Obj(temp=28, description="Clear", slider_percent=70, rainfall_mm=0, humidity=60, updated_at="now")
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
