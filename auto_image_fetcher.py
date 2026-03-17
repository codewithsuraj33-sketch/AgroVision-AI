import os
import time
from pathlib import Path
from urllib.parse import quote_plus

try:
    import requests  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: requests. Install it with:\n"
        "  pip install requests tqdm\n"
    ) from exc

try:
    from tqdm import tqdm  # type: ignore
except Exception:  # pragma: no cover
    tqdm = None

try:
    from PIL import Image, ImageOps  # type: ignore
except Exception:  # pragma: no cover
    Image = None
    ImageOps = None


SAVE_DIR = Path("static") / "products"
SAVE_DIR.mkdir(parents=True, exist_ok=True)


def _get_api_key() -> str:
    key = (os.getenv("PEXELS_API_KEY") or os.getenv("PIXELS_API_KEY") or "").strip()
    if not key:
        raise SystemExit(
            "PEXELS_API_KEY env var is not set.\n"
            "Set it in your terminal, or put it in .env as:\n"
            "  PEXELS_API_KEY=your_key_here\n"
        )
    return key


def pexels_search(query: str, api_key: str) -> str | None:
    url = f"https://api.pexels.com/v1/search?query={quote_plus(query)}&per_page=1"
    res = requests.get(url, headers={"Authorization": api_key}, timeout=30)
    res.raise_for_status()
    data = res.json()
    photos = data.get("photos") or []
    if not photos:
        return None
    src = (photos[0].get("src") or {}).get("large")
    return str(src).strip() if src else None


def download_file(url: str, out_path: Path) -> bool:
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        with requests.get(url, stream=True, timeout=45) as r:
            r.raise_for_status()
            content_type = (r.headers.get("Content-Type") or "").lower()
            if "image/" not in content_type:
                return False
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        f.write(chunk)
        tmp_path.replace(out_path)
        return True
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


TARGETS: list[tuple[str, str]] = [
    # Pesticides
    ("fungicide.jpg", "fungicide spray bottle"),
    ("neem_oil.jpg", "neem oil pesticide bottle"),
    ("sprayer.jpg", "pesticide sprayer"),
    ("insecticide.jpg", "insecticide bottle"),
    ("leaf_spray.jpg", "leaf protection spray bottle"),
    ("pest_kit.jpg", "pest control kit agriculture"),
    # Fertilizers
    ("fertilizer.jpg", "fertilizer bag agriculture"),
    ("npk.jpg", "npk fertilizer bag"),
    ("nitrogen.jpg", "nitrogen fertilizer bag"),
    ("compost.jpg", "compost fertilizer"),
    ("bio_fertilizer.jpg", "bio fertilizer pack"),
    ("potassium.jpg", "potassium fertilizer bag"),
    # Seeds
    ("rice_seeds.jpg", "rice seeds packet"),
    ("wheat_seeds.jpg", "wheat seeds packet"),
    ("tomato_seeds.jpg", "tomato seeds packet"),
    ("corn_seeds.jpg", "corn seeds packet"),
    ("veg_seeds.jpg", "vegetable seeds packet"),
    ("seed_kit.jpg", "seed kit packet"),
    # Tools
    ("sensor.jpg", "soil moisture sensor"),
    ("irrigation.jpg", "smart irrigation system"),
    ("seeder.jpg", "manual seeder tool"),
    ("sprayer_tool.jpg", "sprayer machine agriculture"),
    ("drip.jpg", "drip irrigation pipe"),
    ("weather_sensor.jpg", "weather sensor agriculture"),
    # Organic
    ("growth.jpg", "organic growth booster"),
    ("compost_pack.jpg", "organic compost pack"),
    ("organic_spray.jpg", "organic pest control spray"),
    ("soil_booster.jpg", "soil booster organic"),
    ("eco_kit.jpg", "eco farming kit"),
    ("plant_care.jpg", "plant care kit"),
]


def main() -> None:
    api_key = _get_api_key()
    items = TARGETS
    iterator = tqdm(items, desc="Downloading", unit="img") if tqdm else items

    ok = 0
    missing = 0
    for filename, query in iterator:
        out_path = SAVE_DIR / filename
        try:
            img_url = pexels_search(query, api_key)
            if not img_url:
                missing += 1
                print(f"Not found: {query} -> {filename}")
                continue
            if download_file(img_url, out_path):
                ok += 1
                print(f"Saved: {out_path.as_posix()}")
            else:
                missing += 1
                print(f"Bad content: {query} -> {filename}")
        except requests.HTTPError as e:
            missing += 1
            print(f"HTTP error for {query} -> {filename}: {e}")
        except requests.RequestException as e:
            missing += 1
            print(f"Network error for {query} -> {filename}: {e}")

        # Be gentle with API rate limits.
        time.sleep(0.25)

    print(f"\nDone. Downloaded {ok} images. Missing/failed: {missing}.")
    if Image is not None:
        size = int(os.getenv("PRODUCT_IMAGE_SIZE") or "900")
        print(f"Normalizing to {size}x{size}...")
        for filename, _ in items:
            path = SAVE_DIR / filename
            if not path.exists():
                continue
            try:
                with Image.open(path) as img:
                    img = ImageOps.exif_transpose(img)
                    img = img.convert("RGB")
                    img = ImageOps.fit(img, (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
                    img.save(path, format="JPEG", quality=86, optimize=True, progressive=True)
            except Exception as exc:
                print(f"Normalize failed for {filename}: {exc}")
    print("Tip: Hard refresh in browser (Ctrl+F5) after downloading.")


if __name__ == "__main__":
    main()
