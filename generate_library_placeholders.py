import json
import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps  # type: ignore


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "dataset" / "disease_data.json"
OUT_DIR = ROOT / "static" / "library" / "diseases"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _slug_color(slug: str) -> tuple[int, int, int]:
    digest = hashlib.md5(slug.encode("utf-8")).digest()
    # Keep colors soft/premium.
    r = 60 + digest[0] % 140
    g = 70 + digest[1] % 150
    b = 80 + digest[2] % 150
    return r, g, b


def _wrap_text(text: str, width: int = 18) -> list[str]:
    words = (text or "").strip().split()
    lines: list[str] = []
    buf: list[str] = []
    for word in words:
        next_buf = " ".join(buf + [word])
        if len(next_buf) <= width or not buf:
            buf.append(word)
        else:
            lines.append(" ".join(buf))
            buf = [word]
    if buf:
        lines.append(" ".join(buf))
    return lines[:4]


def generate_placeholder(slug: str, title: str, subtitle: str = "") -> Image.Image:
    size = 900
    base = _slug_color(slug)
    img = Image.new("RGB", (size, size), base)

    # Simple diagonal gradient overlay.
    overlay = Image.new("RGB", (size, size), (255, 255, 255))
    mask = Image.new("L", (size, size))
    for y in range(size):
        # 0..180 alpha, heavier at top-left.
        alpha = int(160 - (y / size) * 120)
        ImageDraw.Draw(mask).line([(0, y), (size, y)], fill=max(0, min(180, alpha)))
    overlay = ImageOps.colorize(mask, black=(0, 0, 0), white=(255, 255, 255))
    img = Image.blend(img, overlay, 0.22)

    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    # Title block.
    title_lines = _wrap_text(title, width=18)
    sub_lines = _wrap_text(subtitle, width=28) if subtitle else []

    # Compute block height.
    line_h = 22
    title_h = line_h * len(title_lines)
    sub_h = 18 * len(sub_lines)
    total_h = title_h + (14 if sub_lines else 0) + sub_h

    y0 = (size - total_h) // 2

    # Background blur-like card.
    pad_x = 58
    pad_y = 34
    card_w = size - pad_x * 2
    card_h = total_h + pad_y * 2
    card_x0 = pad_x
    card_y0 = y0 - pad_y
    card_x1 = card_x0 + card_w
    card_y1 = card_y0 + card_h

    draw.rounded_rectangle(
        [card_x0, card_y0, card_x1, card_y1],
        radius=34,
        fill=(255, 255, 255),
        outline=(230, 240, 255),
        width=3,
    )

    y = y0
    for line in title_lines:
        w = draw.textlength(line, font=font)
        draw.text(((size - w) / 2, y), line, fill=(11, 37, 64), font=font)
        y += line_h

    if sub_lines:
        y += 14
        for line in sub_lines:
            w = draw.textlength(line, font=font)
            draw.text(((size - w) / 2, y), line, fill=(60, 90, 120), font=font)
            y += 18

    return img


def main() -> None:
    if not DATA_PATH.exists():
        raise SystemExit(f"Missing: {DATA_PATH}")

    items = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise SystemExit("disease_data.json must be a list.")

    changed = False
    created = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        name = str(item.get("name") or "").strip()
        if not slug or not name:
            continue

        crops = item.get("crops") or []
        subtitle = ""
        if isinstance(crops, list) and crops:
            subtitle = "Crops: " + ", ".join([str(c) for c in crops[:2]])

        dst = OUT_DIR / f"{slug}.jpg"
        expected_url = f"/static/library/diseases/{slug}.jpg"
        if str(item.get("image") or "").strip() != expected_url:
            item["image"] = expected_url
            changed = True

        if not dst.exists():
            img = generate_placeholder(slug, name, subtitle)
            img.save(dst, format="JPEG", quality=86, optimize=True, progressive=True)
            created += 1

    if changed:
        DATA_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Done. Created {created} placeholder images in {OUT_DIR}.")


if __name__ == "__main__":
    main()

