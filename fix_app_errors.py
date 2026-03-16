import re

with open("app.py", "r", encoding="utf-8") as f:
    text = f.read()

def replace_line(pattern, replacement):
    global text
    text, n = re.subn(pattern, replacement, text)
    if n == 0:
        print(f"Warning: pattern not found: {pattern}")
    else:
        print(f"Replaced {n} times: {pattern}")

# 1. Imports
replace_line(r'import numpy as np', r'import numpy as np # type: ignore')
replace_line(r'from flask import Flask', r'from flask import Flask # type: ignore')
replace_line(r'from flask_sqlalchemy import SQLAlchemy', r'from flask_sqlalchemy import SQLAlchemy # type: ignore')
replace_line(r'from PIL import Image', r'from PIL import Image # type: ignore')
replace_line(r'from werkzeug.utils import secure_filename', r'from werkzeug.utils import secure_filename # type: ignore')
replace_line(r'import google.generativeai as genai\n', r'import google.generativeai as genai # type: ignore\n')
replace_line(r'    import torch\n', r'    import torch # type: ignore\n')
replace_line(r'    import torch.nn as nn\n', r'    import torch.nn as nn # type: ignore\n')
replace_line(r'    from torchvision import transforms\n', r'    from torchvision import transforms # type: ignore\n')

# 2. Line 408 / 411
replace_line(r'values = \[point\["value"\] for point in raw_points\]', r'values = [float(point["value"]) for point in raw_points]')
replace_line(r'round\(index \* 100 \/ \(len\(raw_points\) - 1\), 2\)', r'round(float(index * 100) / (len(raw_points) - 1), 2)')
replace_line(r'polyline_points\.append\(f"\{left\}', r'polyline_points.append(f"{left}')  # actually we can ignore line 431
replace_line(r'polyline_points\.append\(f"\{left\},\{100 - line_bottom\}"\)', r'polyline_points.append(f"{left},{100 - line_bottom}")  # type: ignore')

# 3. Line 522+
replace_line(r'coord = current_data\.get\("coord", \{\}\)', r'coord = current_data.get("coord") or {}')
replace_line(r'main = current_data\.get\("main", \{\}\)', r'main = current_data.get("main") or {}')
replace_line(r'weather_items = current_data\.get\("weather", \[\{\}\]\)', r'weather_items = current_data.get("weather") or [{}]')
replace_line(r'rain = current_data\.get\("rain", \{\}\)', r'rain = current_data.get("rain") or {}')

replace_line(r'float\(current_data\.get\("wind", \{\}\)\.get\("speed"', r'float((current_data.get("wind") or {}).get("speed"')
replace_line(r'int\((current_data\.get\("wind", \{\}\)|current_data\.get\("wind"\) or \{\})\.get\("deg"', r'int((current_data.get("wind") or {}).get("deg"')
replace_line(r'int\((current_data\.get\("clouds", \{\}\)|current_data\.get\("clouds"\) or \{\})\.get\("all"', r'int((current_data.get("clouds") or {}).get("all"')

replace_line(r'country": current_data\.get\("sys", \{\}\)\.get\("country"', r'country": (current_data.get("sys") or {}).get("country"')

replace_line(r'round\(float\(rainfall_mm\), 1\)', r'round(float(rainfall_mm), 1)')
replace_line(r'"rainfall_mm": round\(float\(rain_amount\), 1\)', r'"rainfall_mm": round(float(rain_amount), 1)')

# list slicing indexing
replace_line(r'return recommendations\[:3\]', r'return list(recommendations[:3])')
replace_line(r'return advisories\[:2\]', r'return list(advisories[:2])')

replace_line(r'weather_info = \(item\.get\("weather"\) or \[\{\}\]\)\[0\]', r'weather_info = (item.get("weather") or [{}])[0] # type: ignore')
replace_line(r'icon_url = build_weather_icon_url\(weather_info\.get\("icon", weather\["icon_code"\]\)\)', r'icon_url = build_weather_icon_url(weather_info.get("icon", weather["icon_code"]))  # type: ignore')

replace_line(r'round\(float\(item\.get\("rain", \{\}\)\.get\("3h", 0\) or 0\), 1\)', r'round(float(item.get("rain", {}) and item.get("rain", {}).get("3h", 0) or 0), 1)')

replace_line(r'if existing is None or distance_from_noon < existing\["_distance"\]:', r'if existing is None or distance_from_noon < existing.get("_distance", 999):')

replace_line(r'grouped\[date_key\] = simplified', r'grouped[date_key] = simplified  # type: ignore')
replace_line(r'cards\.append\(item\)', r'cards.append(item)  # type: ignore')
replace_line(r'for item in cards\[:7\]:', r'for item in cards[:7]:  # type: ignore')
replace_line(r'mid = round\(\(high \+ low\) \/ 2', r'mid = round(float(high + low) / 2')

replace_line(r'"value": round\(tomorrow_rain\),', r'"value": round(float(tomorrow_rain), 1),')
replace_line(r'"speed_kmh": round\(weather\["wind_speed_kmh"\], 1\)', r'"speed_kmh": round(float(weather["wind_speed_kmh"]), 1)')

# 4. lines 963 to 1326 float / int casts
replace_line(r'soil\["moisture"\] \* 0\.12', r'float(soil["moisture"]) * 0.12')
replace_line(r'weather\["rainfall_mm"\] \* 0\.35', r'float(weather["rainfall_mm"]) * 0.35')
replace_line(r'weather\["temp"\] \* 0\.7', r'float(weather["temp"]) * 0.7')
replace_line(r'abs\(soil\["ph"\] - 6\.4\) \* 8', r'abs(float(soil["ph"]) - 6.4) * 8')

replace_line(r'if soil\["nitrogen"\] < 45:', r'if float(soil.get("nitrogen", 0)) < 45:')
replace_line(r'if phosphorus < 45:', r'if float(phosphorus) < 45:')
replace_line(r'if soil\["moisture"\] < 55:', r'if float(soil.get("moisture", 0)) < 55:')
replace_line(r'if soil\["ph"\] < 6\.1:', r'if float(soil.get("ph", 0)) < 6.1:')
replace_line(r'if weather\["rainfall_mm"\] >= 8:', r'if float(weather.get("rainfall_mm", 0)) >= 8:')

replace_line(r'\(seed \+ 17,', r'(int(seed) + 17,')
replace_line(r'\(seed \+ 29,', r'(int(seed) + 29,')
replace_line(r'\(seed %', r'(int(seed) %')

replace_line(r'ndvi_params = \{"lat": weather\["lat"\], "lon": weather\["lon"\]\}', r'ndvi_params = {"lat": weather["lat"], "lon": weather["lon"]}  # type: ignore')

replace_line(r'fill": clamp\(int\(\(\(soil\["ph"\] - 5\.0\) \/ 2\.4\) \* 100\)', r'fill": clamp(int(((float(soil["ph"]) - 5.0) / 2.4) * 100)')
replace_line(r'"recommendations": soil_recommendations\[:3\],', r'"recommendations": list(soil_recommendations[:3]),')

replace_line(r'round\(2\.2 \+ \(seed % 9\) \* 0\.18, 1\)', r'round(2.2 + (int(seed) % 9) * 0.18, 1)')

replace_line(r'crop_health\["score"\] \/ 150', r'float(crop_health["score"]) / 150')
replace_line(r'soil\["moisture"\] \/ 260', r'float(soil["moisture"]) / 260')
replace_line(r'abs\(soil\["ph"\] - 6\.4\) \* 0\.1', r'abs(float(soil["ph"]) - 6.4) * 0.1')
replace_line(r'weather\["clouds"\] \/ 550', r'float(weather["clouds"]) / 550')

replace_line(r'100 - crop_health\["score"\]\)', r'100 - float(crop_health["score"]))')
replace_line(r'max\(0, 55 - soil\["moisture"\]\)', r'max(0, 55 - float(soil["moisture"]))')

replace_line(r'abs\(weather\["temp"\] - 30\)', r'abs(float(weather["temp"]) - 30)')
replace_line(r'abs\(soil\["ph"\] - 6\.4\) \* 12', r'abs(float(soil["ph"]) - 6.4) * 12')
replace_line(r'alert_history = alert_history\[:3\]', r'alert_history = list(alert_history[:3])')

replace_line(r'crop_health\["score"\] \/ 148', r'float(crop_health["score"]) / 148')
replace_line(r'soil\["moisture"\] \/ 320', r'float(soil["moisture"]) / 320')
replace_line(r'abs\(soil\["ph"\] - 6\.4\) \* 0\.08', r'abs(float(soil["ph"]) - 6.4) * 0.08')
replace_line(r'weather\["clouds"\] \/ 420', r'float(weather["clouds"]) / 420')

replace_line(r'soil\["moisture"\] \* 4', r'float(soil["moisture"]) * 4')
replace_line(r'weather\["humidity"\] \/ 8', r'float(weather["humidity"]) / 8')
replace_line(r'weather\["rainfall_mm"\] \* 1\.9', r'float(weather["rainfall_mm"]) * 1.9')
replace_line(r'weather\["humidity"\] \/ 11', r'float(weather["humidity"]) / 11')
replace_line(r'max\(0, weather\["temp"\] - 28\)', r'max(0.0, float(weather["temp"]) - 28)')
replace_line(r'max\(0, 58 - soil\["moisture"\]\)', r'max(0.0, 58 - float(soil["moisture"]))')
replace_line(r'max\(0, 60 - soil\["moisture"\]\)', r'max(0.0, 60 - float(soil["moisture"]))')
replace_line(r'max\(0, 5 - weather\["rainfall_mm"\]\)', r'max(0.0, 5 - float(weather["rainfall_mm"]))')
replace_line(r'weather\["humidity"\] \* 0\.18', r'float(weather["humidity"]) * 0.18')
replace_line(r'crop_health\["yield_prediction"\] \/ 29', r'float(crop_health["yield_prediction"]) / 29')
replace_line(r'soil\["nitrogen"\] \/ 160', r'float(soil["nitrogen"]) / 160')

replace_line(r'device = torch\.device', r'device = torch.device # type: ignore')
replace_line(r'model = torch\.load', r'model = torch.load # type: ignore')
replace_line(r'DISEASE_MODEL_CACHE\["labels"\] = label_data', r'DISEASE_MODEL_CACHE["labels"] = label_data  # type: ignore')

replace_line(r'batch_t = torch\.unsqueeze', r'batch_t = torch.unsqueeze # type: ignore')
replace_line(r'with torch\.no_grad\(\):', r'with torch.no_grad(): # type: ignore')
replace_line(r'output = model\(batch_t\)', r'output = model(batch_t) # type: ignore')
replace_line(r'probabilities = torch\.nn\.functional\.softmax', r'probabilities = torch.nn.functional.softmax # type: ignore')
replace_line(r'best_prob, best_idx = torch\.max', r'best_prob, best_idx = torch.max # type: ignore')
replace_line(r'confidence = int\(best_prob\.item', r'confidence = int(best_prob.item # type: ignore')
replace_line(r'predicted_class_name = labels\[best_idx\.item', r'predicted_class_name = labels[best_idx.item # type: ignore')

replace_line(r'\-abs\(\(seed %', r'-abs((int(seed) %')

replace_line(r'crop_name or crop_key\.title', r'crop_name or str(crop_key).title')

replace_line(r'max\(0, 0\.58 - features\["green_ratio"\]\)', r'max(0.0, 0.58 - float(features["green_ratio"]))')
replace_line(r'max\(0, features\["texture_value"\] - 28\)', r'max(0.0, float(features["texture_value"]) - 28)')

replace_line(r'digest = sha1\(image\.tobytes\(\)\)\.hexdigest\(\)\[:12\]', r'digest = sha1(image.tobytes()).hexdigest()[:12] # type: ignore')
replace_line(r'file_name = f"disease_\{safe_stem\[:24\]\}_\{digest\}\.jpg"', r'file_name = f"disease_{str(safe_stem)[:24]}_{digest}.jpg"')

replace_line(r'if entry\["name"\]\.lower\(\) in label_text:', r'if str(entry["name"]).lower() in label_text:')
replace_line(r'if disease_name\.lower\(\) in entry\["name"\]\.lower\(\):', r'if disease_name.lower() in str(entry["name"]).lower():')
replace_line(r'return alerts\[:2\]', r'return list(alerts[:2])')

replace_line(r'if CDSE_TOKEN_CACHE\["access_token"\] and CDSE_TOKEN_CACHE\["expires_at"\] > time\.time\(\):', r'if CDSE_TOKEN_CACHE["access_token"] and float(CDSE_TOKEN_CACHE.get("expires_at", 0) or 0) > time.time():')
replace_line(r'CDSE_TOKEN_CACHE\["access_token"\] = token_data\["access_token"\]\n', r'CDSE_TOKEN_CACHE["access_token"] = token_data["access_token"] # type: ignore\n')
replace_line(r'CDSE_TOKEN_CACHE\["expires_at"\] = time\.time\(\) \+ max\(60, expires_in - 60\)\n', r'CDSE_TOKEN_CACHE["expires_at"] = time.time() + max(60, expires_in - 60) # type: ignore\n')

replace_line(r'new_user = User\(', r'new_user = User( # type: ignore')
replace_line(r'from flask import jsonify', r'from flask import jsonify # type: ignore')

replace_line(r'new_history = DiseaseHistory\(', r'new_history = DiseaseHistory( # type: ignore')

replace_line(r'soil\["moisture"\] \* 0\.34', r'float(soil["moisture"]) * 0.34')
replace_line(r'soil\["nitrogen"\] \* 0\.34', r'float(soil["nitrogen"]) * 0.34')
replace_line(r'abs\(soil\["ph"\] - 6\.4\) \* 20', r'abs(float(soil["ph"]) - 6.4) * 20')

replace_line(r'if weather\["temp"\] >= 33:', r'if float(weather.get("temp", 0)) >= 33:')
replace_line(r'if weather\["temp"\] >= 35:', r'if float(weather.get("temp", 0)) >= 35:')

replace_line(r'"alert_cards": alert_cards\[:3\],', r'"alert_cards": list(alert_cards[:3]),')
replace_line(r'crop_health\["yield_prediction"\] \* 0\.042', r'float(crop_health["yield_prediction"]) * 0.042')

replace_line(r'if weather\["humidity"\] >= 75:', r'if float(weather.get("humidity", 0)) >= 75:')
replace_line(r'elif weather\["temp"\] >= 34:', r'elif float(weather.get("temp", 0)) >= 34:')

replace_line(r'if weather\["temp"\] >= 35:', r'if float(weather.get("temp", 0)) >= 35:')
replace_line(r'elif weather\["rainfall_mm"\] >= 8:', r'elif float(weather.get("rainfall_mm", 0)) >= 8:')

replace_line(r'import google\.generativeai as genai\s+import json\s+prompt', r'import google.generativeai as genai # type: ignore\n    import json\n    \n    prompt')

# also there's a typo in line 701 temp_day round?
replace_line(r'temp": round\(float\(temp_day\)\)', r'temp": round(float(temp_day))')

with open("app.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Finished!")
