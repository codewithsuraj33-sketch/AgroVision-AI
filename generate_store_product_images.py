import json
import re
from html import escape
from pathlib import Path


DATA_PATH = Path("dataset") / "store_products.json"
OUT_DIR = Path("static") / "images" / "store"


def slugify(text: str) -> str:
    parts = re.findall(r"[a-z0-9]+", (text or "").lower())
    return "-".join(parts)


def wrap_words(text: str, max_len: int, max_lines: int) -> list[str]:
    words = [w for w in (text or "").strip().split() if w]
    if not words:
        return []
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = (" ".join(current + [word])).strip()
        if len(candidate) <= max_len or not current:
            current.append(word)
            continue
        lines.append(" ".join(current))
        current = [word]
        if len(lines) >= max_lines:
            break
    if len(lines) < max_lines and current:
        lines.append(" ".join(current))
    return lines[:max_lines]


def truncate(text: str, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def category_theme(category: str) -> tuple[str, str, str]:
    cat = (category or "").strip().lower()
    if "pesticide" in cat:
        return ("#ffcb45", "#ff4d5f", "#09162a")
    if "fertilizer" in cat:
        return ("#7dff2e", "#30e6bf", "#081a13")
    if "seed" in cat:
        return ("#46c4ff", "#30e6bf", "#071323")
    if "tool" in cat:
        return ("#c7d6ff", "#46c4ff", "#071323")
    return ("#30e6bf", "#7dff2e", "#071323")


def build_svg(product_name: str, description: str, category: str, unit: str, price: int) -> str:
    c1, c2, base = category_theme(category)
    name_lines = wrap_words(product_name, max_len=18, max_lines=2)
    desc_line = truncate(description, 64)
    meta = f"{category} • {unit} • INR {price}"

    # Layout coordinates are tuned for 600x600.
    y = 210
    name_text = ""
    for line in name_lines:
        name_text += f'<text x="48" y="{y}" fill="rgba(255,255,255,0.96)" font-size="48" font-weight="900">{escape(line)}</text>\n'
        y += 58

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600" viewBox="0 0 600 600">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{c1}" stop-opacity="0.95"/>
      <stop offset="1" stop-color="{c2}" stop-opacity="0.85"/>
    </linearGradient>
    <radialGradient id="r" cx="0.2" cy="0.15" r="0.9">
      <stop offset="0" stop-color="white" stop-opacity="0.16"/>
      <stop offset="1" stop-color="white" stop-opacity="0"/>
    </radialGradient>
    <filter id="soft" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="14"/>
    </filter>
  </defs>

  <rect width="600" height="600" rx="36" fill="{base}"/>
  <rect width="600" height="600" rx="36" fill="url(#g)" opacity="0.18"/>
  <circle cx="110" cy="110" r="130" fill="url(#r)"/>
  <circle cx="520" cy="540" r="160" fill="url(#r)"/>

  <g filter="url(#soft)" opacity="0.34">
    <path d="M70,420 C180,350 260,510 340,450 C420,390 480,450 560,400" fill="none" stroke="{c2}" stroke-width="18" stroke-linecap="round"/>
  </g>

  <text x="48" y="78" fill="rgba(255,255,255,0.72)" font-size="14" font-weight="900" letter-spacing="0.18em">
    AGROVISION STORE
  </text>
  <text x="48" y="116" fill="rgba(255,255,255,0.70)" font-size="13" font-weight="800">
    {escape(meta)}
  </text>

  {name_text.strip()}

  <rect x="44" y="470" width="512" height="1" fill="rgba(255,255,255,0.14)"/>
  <text x="48" y="510" fill="rgba(255,255,255,0.78)" font-size="16" font-weight="800">
    {escape(desc_line)}
  </text>
</svg>
"""


def main() -> None:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Missing: {DATA_PATH}")

    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("store_products.json must be a list")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    updated = 0
    for item in raw:
        if not isinstance(item, dict):
            continue

        product_id = int(item.get("id") or 0)
        name = str(item.get("name") or f"Product {product_id}").strip()
        category = str(item.get("category") or "Organic").strip() or "Organic"
        description = str(item.get("description") or "").strip()
        unit = str(item.get("unit") or "Pack").strip() or "Pack"
        price = int(item.get("price") or 0)

        base_slug = slugify(name) or f"product-{product_id}"
        file_name = f"{base_slug}-{product_id}.svg"
        out_path = OUT_DIR / file_name

        svg = build_svg(name, description, category, unit, price)
        out_path.write_text(svg, encoding="utf-8")

        item["image"] = f"/static/images/store/{file_name}"
        updated += 1

    DATA_PATH.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Updated {updated} products. Images in {OUT_DIR}")


if __name__ == "__main__":
    main()
