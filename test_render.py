from flask import Flask, render_template

app = Flask(__name__)

class DummyUser:
    name = "Test User"
    email = "t@test.com"
    location = "Loc"
    crop_type = "Wheat"
    profile_photo = None
    farm_size = "1"
    
class DummyDashboard:
    weather = {"city": "X", "temp": 20, "description": "clear", "slider_percent": 50, "rainfall_mm": 0, "humidity": 50, "updated_at": "now"}
    farm_stats = {"count": 1}
    task_summary = {"open_count": 0}
    soil = {"metrics": []}
    crop_health = {"score": 90, "label": "Good", "crop_name": "Wheat"}
    ndvi_preview_url = ""

@app.route('/test-dashboard')
def test_dash():
    return render_template(
        "dashboard.html", 
        user=DummyUser(), 
        dashboard=DummyDashboard()
    )

if __name__ == '__main__':
    app.run(port=5007)
