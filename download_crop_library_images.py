import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DATASET_PATH = ROOT / "dataset" / "crop_library.json"
IMAGE_DIR = ROOT / "static" / "images" / "crops"
COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "AgroVisionAI/1.0 Crop Library Image Downloader"
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
REQUEST_DELAY_SECONDS = 2.0
FAILURE_DELAY_SECONDS = 5.0
DISALLOWED_TOKENS = {
    "icon",
    "logo",
    "diagram",
    "map",
    "flag",
    "symbol",
    "coat of arms",
    "emblem",
    "illustration",
    "vector",
    "drawing",
    "chart",
}

SEARCH_OVERRIDES = {
    "Rice": ["rice crop plant", "paddy field crop"],
    "Maize": ["maize crop plant", "corn field crop"],
    "Chili": ["chili pepper plant agriculture", "red chili crop plant"],
    "Brinjal": ["eggplant crop plant", "brinjal agriculture"],
    "Groundnut": ["groundnut crop plant", "peanut crop field"],
    "Pigeon Pea": ["pigeon pea plant agriculture", "toor dal crop"],
    "Green Gram": ["mung bean crop plant", "green gram agriculture"],
    "Black Gram": ["black gram crop plant", "urad crop agriculture"],
    "Muskmelon": ["muskmelon crop plant", "cantaloupe field crop"],
    "Coriander": ["coriander crop plant", "coriander agriculture"],
    "Fenugreek": ["fenugreek crop plant", "methi plant agriculture"],
    "Tea": ["tea plantation crop", "tea plant agriculture"],
    "Coffee": ["coffee plant crop", "coffee plantation agriculture"],
}

WIKIPEDIA_TITLE_OVERRIDES = {
    "Mango": "Mangifera indica",
    "Papaya": "Carica papaya",
    "Chili": "Chili pepper",
    "Brinjal": "Eggplant",
    "Groundnut": "Peanut",
    "Pigeon Pea": "Pigeon pea",
    "Green Gram": "Mung bean",
    "Black Gram": "Black gram",
    "Sweet Potato": "Sweet potato",
    "Muskmelon": "Cantaloupe",
    "Guava": "Psidium guajava",
    "Coffee": "Coffea",
    "Jute": "Jute",
}


def slugify(name):
    return "-".join(re.findall(r"[a-z0-9]+", (name or "").lower())) or "crop"


def load_crops():
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def build_queries(name):
    return SEARCH_OVERRIDES.get(name, []) + [
        f"{name} crop plant agriculture",
        f"{name} field crop",
        f"{name} plant agriculture",
    ]


def fetch_json(url):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=25) as response:
        return json.load(response)


def choose_wikipedia_image_url(name):
    title = WIKIPEDIA_TITLE_OVERRIDES.get(name, name)
    params = {
        "action": "query",
        "titles": title,
        "redirects": 1,
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": 1280,
        "format": "json",
    }
    url = f"{WIKIPEDIA_API_URL}?{urlencode(params)}"
    payload = fetch_json(url)
    pages = (payload.get("query") or {}).get("pages") or {}
    for page in pages.values():
        image_url = ((page.get("thumbnail") or {}).get("source") or "").strip()
        suffix = Path(urlparse(image_url).path).suffix.lower()
        if image_url and suffix in ALLOWED_SUFFIXES:
            return image_url
    return None


def choose_commons_image_url(name):
    name_lower = name.lower()
    for query in build_queries(name):
        params = {
            "action": "query",
            "generator": "search",
            "gsrnamespace": 6,
            "gsrlimit": 10,
            "gsrsearch": f"{query} filetype:bitmap",
            "prop": "imageinfo",
            "iiprop": "url",
            "iiurlwidth": 1400,
            "format": "json",
        }
        url = f"{COMMONS_API_URL}?{urlencode(params)}"
        payload = fetch_json(url)
        pages = (payload.get("query") or {}).get("pages") or {}
        candidates = []

        for page in pages.values():
            title = str(page.get("title") or "")
            title_lower = title.lower()
            if any(token in title_lower for token in DISALLOWED_TOKENS):
                continue

            image_info = (page.get("imageinfo") or [{}])[0]
            image_url = image_info.get("thumburl") or image_info.get("url")
            if not image_url:
                continue

            suffix = Path(urlparse(image_url).path).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                continue

            score = 0
            if name_lower in title_lower:
                score += 5
            if "crop" in title_lower or "field" in title_lower or "plant" in title_lower:
                score += 2
            if suffix in {".jpg", ".jpeg", ".webp"}:
                score += 1

            candidates.append((score, title, image_url))

        if candidates:
            candidates.sort(key=lambda item: (-item[0], item[1]))
            return candidates[0][2]

        time.sleep(REQUEST_DELAY_SECONDS)

    return None


def choose_image_url(name):
    image_url = choose_wikipedia_image_url(name)
    if image_url:
        return image_url
    return choose_commons_image_url(name)


def download_image(name, image_url):
    request = Request(image_url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        data = response.read()
        suffix = Path(urlparse(response.geturl()).path).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            suffix = ".jpg"
    output_path = IMAGE_DIR / f"{slugify(name)}{suffix}"
    output_path.write_bytes(data)
    return output_path


def main():
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    crops = load_crops()
    downloaded = 0
    failed = []

    for crop in crops:
        name = crop["name"]
        slug = slugify(name)
        if any((IMAGE_DIR / f"{slug}{suffix}").exists() for suffix in ALLOWED_SUFFIXES):
            print(f"skip  {name}")
            continue

        try:
            image_url = choose_image_url(name)
            if not image_url:
                failed.append(name)
                print(f"miss  {name}")
                time.sleep(FAILURE_DELAY_SECONDS)
                continue

            output_path = download_image(name, image_url)
            downloaded += 1
            print(f"saved {name} -> {output_path.name}")
            time.sleep(REQUEST_DELAY_SECONDS)
        except Exception as exc:
            failed.append(name)
            print(f"fail  {name} -> {exc}")
            time.sleep(FAILURE_DELAY_SECONDS)

    print(f"downloaded={downloaded}")
    if failed:
        print("failed=" + ", ".join(failed))


if __name__ == "__main__":
    main()
