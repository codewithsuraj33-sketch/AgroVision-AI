import os
import re

TEMPLATES_DIR = r"c:\Users\suraj\OneDrive\Desktop\New folder (4)\templates"

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content
    modified = False

    # 1. Fix tojson in JS (Chart.js data arrays)
    # data: {{ ... | tojson }} -> data: JSON.parse('{{ ... | tojson | forceescape }}')
    # Actually, Jinja string escaping inside JS quotes is tricky. 
    # Let's use `data: JSON.parse('{{ ... | tojson | string | replace("\'", "\\'") }}')` 
    # Wait, simpler: use HTML data attributes for JSON data too!
    
    # 2. Fix JS float parsing (lat/lon)
    # var lat = {{ ... }}; -> var lat = parseFloat("{{ ... }}");
    content = re.sub(
        r'(var|let|const)\s+([a-zA-Z0-9_]+)\s*=\s*\{\{\s*([^\}]+)\s*\}\};',
        r'\1 \2 = parseFloat("\{\{ \3 \}\}");',
        content
    )

    # 3. Fix JS string parsing
    # .bindPopup('{{ ... }}') -> bindPopup("{{ ... }}")
    # Actually, if it's already in quotes, it doesn't cause JS error.
    # The error in bindPopup is probably due to line 265 in dashboard.html:
    # L.marker([lat, lng]).addTo(map).bindPopup('{{ user.location|default("Your Farm") }}');
    # Wait! The quotes might be creating a syntax error if Jinja contains single quotes.
    # Wait, in the JS: `L.marker([lat, lng]).addTo(map).bindPopup('{{ user.location|default("Your Farm") }}');`
    # The IDE complains: "Property assignment expected." "',' expected."
    # Because there are double quotes inside single quotes? `("Your Farm")` is valid string inside `''`.
    # Why did it error on bindPopup?
    
    # Let's just wrap `{{ ... }}` in quotes anywhere they are naked in JS.
    # We already did variable assignment. What about function calls?
    content = re.sub(
        r'(\w+)\(\{\{\s*([^\}]+)\s*\}\}\)',
        r'\1("\{\{ \2 \}\}")',
        content
    )

    # 4. Fix inline styles containing Jinja
    # style="width: {{ ... }}%;" -> data-style-width="{{ ... }}%"
    # Need a dynamic regex for style="[property]: {{ ... }}[unit];"
    
    def style_replacer(match):
        style_content = match.group(1)
        if '{{' not in style_content:
            return match.group(0) # Keep original
        
        # Parse CSS properties
        props = style_content.split(';')
        new_attrs = []
        keep_styles = []
        for prop in props:
            prop = prop.strip()
            if not prop:
                continue
            if '{{' in prop:
                if ':' in prop:
                    k, v = prop.split(':', 1)
                    # Convert to data-style-xxx
                    new_attrs.append(f'data-style-{k.strip()}="{v.strip()}"') # type: ignore
            else:
                keep_styles.append(prop)
        
        res = " ".join(new_attrs)
        if keep_styles:
            res += f' style="{"; ".join(keep_styles)}"'
        return res

    content = re.sub(r'style="([^"]+)"', style_replacer, content)

    # 5. Add universal inline style applicator script at the end of body
    # Only if we added data-style attrs
    if 'data-style-' in content and 'function applyDataStyles()' not in content:
        script_to_add = """
  <!-- Fix IDE Parsing: Apply dynamic styles via JS -->
  <script>
    function applyDataStyles() {
      document.querySelectorAll('*').forEach(el => {
        Array.from(el.attributes).forEach(attr => {
          if (attr.name.startsWith('data-style-')) {
            const cssProp = attr.name.slice(11); // 'data-style-'.length == 11
            el.style[cssProp] = attr.value;
          }
        });
      });
    }
    document.addEventListener('DOMContentLoaded', applyDataStyles);
  </script>
</body>"""
        content = content.replace('</body>', script_to_add)

    # Specific fix for ai_insights.html chart data (and other chart data)
    # data: {{ ... | tojson }} -> data: JSON.parse('{{ ... | tojson }}')
    def chart_data_replacer(match):
        return f"data: JSON.parse('{match.group(1)}')"
    
    # Wait, `tojson` inside JSON.parse string might have unescaped double quotes!
    # Jinja `tojson` returns `["abc", "def"]`. Inside JS `'["abc", "def"]'`, this is valid!
    # But if Jinja output contains a single quote, it's NOT valid inside `''`. 
    # e.g., `["Don't"]` -> `'["Don't"]'` breaks string.
    # To fix this, use backticks: `JSON.parse(`{{ ... | tojson }}`)`
    # No, backticks in JS evaluate `${}`, Jinja has `{{ }}`...
    content = re.sub(r'data:\s*(\{\{[^\}]+\|?\s*tojson\s*\}\})', r'data: JSON.parse(`\1`)', content)

    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Fixed IDE parsing errors in {os.path.basename(filepath)}")

for filename in os.listdir(TEMPLATES_DIR):
    if filename.endswith(".html"):
        process_file(os.path.join(TEMPLATES_DIR, filename))
