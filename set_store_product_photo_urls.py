import json
import re
from pathlib import Path


DATA_PATH = Path("dataset") / "store_products.json"


PHOTO_URLS = {
    "default": "https://images.unsplash.com/photo-1592982537447-7440770cbfc9?auto=format&fit=crop&q=80&w=900&sig={sig}",
    "seeds": "https://images.unsplash.com/photo-1523348837708-15d4a09cfac2?auto=format&fit=crop&q=80&w=900&sig={sig}",
    "fertilizers": "https://images.unsplash.com/photo-1628352081506-83c43123ed6d?auto=format&fit=crop&q=80&w=900&sig={sig}",
    "tools": "https://images.unsplash.com/photo-1598902108854-10e335adac99?auto=format&fit=crop&q=80&w=900&sig={sig}",
}


def pick_photo_url(name: str, category: str, tags: list[str], sig: int) -> str:
    text = " ".join([name or "", category or "", " ".join(tags or [])]).lower()
    cat = (category or "").strip().lower()

    if "seed" in cat or "seed" in text:
        return PHOTO_URLS["seeds"].format(sig=sig)
    if "fertilizer" in cat or "fertilizer" in text or "compost" in text or "manure" in text:
        return PHOTO_URLS["fertilizers"].format(sig=sig)
    if "tool" in cat or "tool" in text or "sprayer" in text or "drip" in text or "pipe" in text:
        return PHOTO_URLS["tools"].format(sig=sig)
    return PHOTO_URLS["default"].format(sig=sig)


def main() -> None:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Missing: {DATA_PATH}")

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("store_products.json must be a list")

    updated = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        product_id = int(item.get("id") or 0)
        name = str(item.get("name") or "").strip()
        category = str(item.get("category") or "").strip()
        tags = item.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        tags = [re.sub(r"\s+", " ", str(t or "").strip()) for t in tags if str(t or "").strip()]

        if product_id <= 0:
            continue

        item["image"] = pick_photo_url(name, category, tags, sig=product_id)
        updated += 1

    DATA_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Updated images for {updated} products in {DATA_PATH}")


if __name__ == "__main__":
    main()

