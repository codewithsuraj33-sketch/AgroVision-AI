import os

css_rules = """
/* Language Switcher */
.language-switcher {
    position: absolute;
    top: 20px;
    right: 30px;
    z-index: 1000;
}

#google_translate_element select {
    padding: 8px 12px;
    border-radius: 8px;
    border: none;
    background: #0f3f3f;
    color: white;
    font-size: 14px;
    cursor: pointer;
}
"""

style_file = "static/style.css"
if os.path.exists(style_file):
    with open(style_file, "r", encoding="utf-8") as f:
        content = f.read()
    if ".language-switcher" not in content:
        with open(style_file, "a", encoding="utf-8") as f:
            f.write(css_rules)
        print("CSS added to static/style.css")

html_div = """
      <!-- Language Switcher -->
      <div class="language-switcher">
        <div id="google_translate_element"></div>
      </div>
"""

script_tag = """
  <!-- Google Translate -->
  <script>
    function googleTranslateElementInit() {
      new google.translate.TranslateElement(
        {
          pageLanguage: 'en',
          includedLanguages: 'en,hi,or',
          layout: google.translate.TranslateElement.InlineLayout.SIMPLE
        },
        'google_translate_element'
      );
    }
  </script>
  <script src="//translate.google.com/translate_a/element.js?cb=googleTranslateElementInit"></script>
"""

templates = [
    "dashboard.html", "farms.html", "weather.html", "soil.html", 
    "crop_monitoring.html", "farm_twin.html", "ai_insights.html", 
    "tools.html", "disease_detection.html", "alerts.html", 
    "settings.html", "community.html", "market.html", "refer_and_earn.html"
]

for tpl in templates:
    filepath = os.path.join("templates", tpl)
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        continue
    
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
        
    if "google_translate_element" in content:
        print(f"Skipping {tpl}, already has translate widget.")
        continue

    # Insert div right after <section class="dashboard-main">
    if '<section class="dashboard-main">' in content:
        content = content.replace('<section class="dashboard-main">', '<section class="dashboard-main">' + html_div, 1)
    elif '<main class="dashboard-shell">' in content:
        content = content.replace('<main class="dashboard-shell">', '<main class="dashboard-shell">' + html_div, 1)
    else:
        # refer_and_earn might have page-container
        if '<div class="page-container">' in content:
            content = content.replace('<div class="page-container">', '<div class="page-container">' + html_div, 1)
        else:
            print(f"Skipped {tpl} div insertion due to unknown structure.")

    # Insert script right before </body>
    if "</body>" in content:
        content = content.replace("</body>", script_tag + "\n</body>", 1)
        
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
        
    print(f"Updated {tpl}")
