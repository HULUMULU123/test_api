import hashlib
import json
import logging
import random
import re
import time
from dataclasses import asdict, dataclass, field
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright


RAW_HTML_DIR = "raw_html_farpost"

HEADLESS = False

REQUEST_DELAY_MIN = 2.0
REQUEST_DELAY_MAX = 5.0

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ключевой момент: регионы — это поддомены
CITY_DOMAINS = {
    "vladivostok": "vladivostok.farpost.ru",
    "khabarovsk": "khabarovsk.farpost.ru",
    "habarovsk": "khabarovsk.farpost.ru",
    "ussuriysk": "ussuriysk.farpost.ru",
}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("farpost_parser")


@dataclass
class Listing:
    url: str
    title: str | None = None
    price: int | None = None
    address: str | None = None
    description: str | None = None
    seller: str | None = None
    params: dict = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    raw_html_path: str | None = None


def random_delay():
    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))


def retryable(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last_error = e
                time.sleep(RETRY_BASE_DELAY * attempt)
        raise last_error
    return wrapper


def normalize_text(value):
    if not value:
        return None
    return re.sub(r"\s+", " ", value).strip()


def parse_price(value):
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def save_raw_html(url, html):
    Path(RAW_HTML_DIR).mkdir(exist_ok=True)
    fname = hashlib.sha256(url.encode()).hexdigest() + ".html"
    path = Path(RAW_HTML_DIR) / fname
    path.write_text(html, encoding="utf-8")
    return str(path)


def get_domain(city: str):
    city = city.lower().strip()
    if city not in CITY_DOMAINS:
        raise ValueError(f"Неизвестный город для farpost: {city}")
    return CITY_DOMAINS[city]


def build_search_url(domain, query, price_min, price_max, page):
    params = {
        "query": query,
        "pmin": price_min,
        "pmax": price_max,
        "page": page,
    }

    params = {k: v for k, v in params.items() if v}

    return f"https://{domain}/realty/?{urlencode(params)}"


class FarpostCrawler:
    def __init__(self, page, domain, query, price_min, price_max, max_pages, max_cards):
        self.page = page
        self.domain = domain
        self.query = query
        self.price_min = price_min
        self.price_max = price_max
        self.max_pages = max_pages
        self.max_cards = max_cards

    def collect_urls(self):
        urls = []
        seen = set()

        for page_num in range(1, self.max_pages + 1):
            if len(urls) >= self.max_cards:
                break

            url = build_search_url(
                self.domain,
                self.query,
                self.price_min,
                self.price_max,
                page_num,
            )

            logger.info(f"Open {url}")

            html = self._load(url)
            soup = BeautifulSoup(html, "lxml")

            for a in soup.select("a[href*='/realty/']"):
                href = a.get("href")
                if not href:
                    continue

                full = urljoin(f"https://{self.domain}", href)

                if not re.search(r"\d+\.html", full):
                    continue

                if full not in seen:
                    seen.add(full)
                    urls.append(full)

                if len(urls) >= self.max_cards:
                    break

            random_delay()

        return urls

    @retryable
    def _load(self, url):
        self.page.goto(url, timeout=60000)
        self.page.wait_for_timeout(3000)
        return self.page.content()


class FarpostParser:
    def __init__(self, page):
        self.page = page

    def parse(self, url):
        html = self._load(url)
        soup = BeautifulSoup(html, "lxml")

        json_ld = self._extract_json_ld(soup)

        return Listing(
            url=url,
            title=self._extract_title(soup, json_ld),
            price=self._extract_price(soup, json_ld),
            address=self._extract_address(soup, json_ld),
            description=self._extract_description(soup, json_ld),
            seller=self._extract_seller(soup),
            params=self._extract_params(soup),
            images=self._get_images(soup, url),
            raw_html_path=save_raw_html(url, html),
        )

    @retryable
    def _load(self, url):
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(5000)

        try:
            self.page.wait_for_selector("h1, body", timeout=15000)
        except Exception:
            pass

        return self.page.content()

    def _get_text(self, soup, selectors):
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = normalize_text(node.get_text(" ", strip=True))
                if text:
                    return text
        return None

    def _extract_title(self, soup, json_ld):
        title = self._get_text(
            soup,
            [
                "h1",
                ".bulletinView_title",
                ".viewbull-title",
                ".item-title",
                "[class*='title']",
            ],
        )

        if title:
            return title

        og_title = soup.select_one("meta[property='og:title']")
        if og_title and og_title.get("content"):
            return normalize_text(og_title.get("content"))

        if isinstance(json_ld, dict):
            return json_ld.get("name")

        return None

    def _extract_price(self, soup, json_ld):
        selectors = [
            ".price",
            ".cost",
            ".bulletinView_price",
            ".viewbull-price",
            ".item-price",
            "[class*='price']",
            "[class*='cost']",
            "[itemprop='price']",
        ]

        for selector in selectors:
            node = soup.select_one(selector)
            if not node:
                continue

            value = node.get("content") or node.get_text(" ", strip=True)
            price = parse_price(value)

            if price:
                return price

        meta_price = soup.select_one("meta[itemprop='price']")
        if meta_price and meta_price.get("content"):
            price = parse_price(meta_price.get("content"))
            if price:
                return price

        if isinstance(json_ld, dict):
            offers = json_ld.get("offers")
            if isinstance(offers, dict):
                price = parse_price(str(offers.get("price")))
                if price:
                    return price

        page_text = normalize_text(soup.get_text(" ", strip=True)) or ""

        patterns = [
            r"(\d[\d\s]{3,})\s*₽",
            r"(\d[\d\s]{3,})\s*руб",
            r"Цена\s*:?\s*(\d[\d\s]{3,})",
        ]

        for pattern in patterns:
            match = re.search(pattern, page_text, flags=re.IGNORECASE)
            if match:
                return parse_price(match.group(1))

        return None

    def _extract_address(self, soup, json_ld):
        address = self._get_text(
            soup,
            [
                ".address",
                ".geo",
                ".bulletinView_address",
                ".viewbull-address",
                ".item-address",
                "[class*='address']",
                "[class*='geo']",
                "[itemprop='address']",
            ],
        )

        if address:
            return address

        if isinstance(json_ld, dict):
            address_data = json_ld.get("address")

            if isinstance(address_data, str):
                return normalize_text(address_data)

            if isinstance(address_data, dict):
                parts = [
                    address_data.get("addressRegion"),
                    address_data.get("addressLocality"),
                    address_data.get("streetAddress"),
                ]
                return normalize_text(", ".join([p for p in parts if p]))

        page_text = normalize_text(soup.get_text(" ", strip=True)) or ""

        patterns = [
            r"Адрес\s*:?\s*([^\.]+)",
            r"Район\s*:?\s*([^\.]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, page_text, flags=re.IGNORECASE)
            if match:
                return normalize_text(match.group(1))

        return None

    def _extract_description(self, soup, json_ld):
        description = self._get_text(
            soup,
            [
                ".description",
                ".bulletinView_description",
                ".viewbull-description",
                ".item-description",
                "#description",
                "#textContent",
                "[class*='description']",
                "[itemprop='description']",
            ],
        )

        if description:
            return description

        meta_desc = soup.select_one("meta[name='description']")
        if meta_desc and meta_desc.get("content"):
            return normalize_text(meta_desc.get("content"))

        og_desc = soup.select_one("meta[property='og:description']")
        if og_desc and og_desc.get("content"):
            return normalize_text(og_desc.get("content"))

        if isinstance(json_ld, dict):
            return normalize_text(json_ld.get("description"))

        return None

    def _extract_seller(self, soup):
        seller = self._get_text(
            soup,
            [
                ".seller",
                ".user-name",
                ".username",
                ".contact-name",
                ".bulletinView_author",
                ".viewbull-author",
                "[class*='seller']",
                "[class*='user']",
                "[class*='author']",
                "[class*='contact']",
            ],
        )

        if seller:
            return seller

        page_text = normalize_text(soup.get_text(" ", strip=True)) or ""

        patterns = [
            r"Продавец\s*:?\s*([А-Яа-яA-Za-z0-9\s\-\.]+)",
            r"Автор\s*:?\s*([А-Яа-яA-Za-z0-9\s\-\.]+)",
            r"Контактное лицо\s*:?\s*([А-Яа-яA-Za-z0-9\s\-\.]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, page_text, flags=re.IGNORECASE)
            if match:
                return normalize_text(match.group(1))

        return None

    def _extract_params(self, soup):
        params = {}

        for row in soup.select("tr"):
            cells = row.select("td, th")
            if len(cells) < 2:
                continue

            key = normalize_text(cells[0].get_text(" ", strip=True))
            value = normalize_text(cells[1].get_text(" ", strip=True))

            if key and value and len(key) <= 80:
                params[key] = value

        for dt in soup.select("dt"):
            dd = dt.find_next_sibling("dd")
            if not dd:
                continue

            key = normalize_text(dt.get_text(" ", strip=True))
            value = normalize_text(dd.get_text(" ", strip=True))

            if key and value:
                params[key] = value

        selectors = [
            ".params li",
            ".properties li",
            ".characteristics li",
            ".bulletinView_params li",
            "[class*='params'] li",
            "[class*='characteristics'] li",
            "[class*='properties'] li",
        ]

        for selector in selectors:
            for node in soup.select(selector):
                text = normalize_text(node.get_text(" ", strip=True))
                parsed = self._parse_param_line(text)

                if parsed:
                    key, value = parsed
                    params[key] = value

        return params

    def _parse_param_line(self, text):
        if not text:
            return None

        if ":" in text:
            key, value = text.split(":", 1)
            key = normalize_text(key)
            value = normalize_text(value)

            if key and value:
                return key, value

        patterns = [
            r"^(Площадь)\s+(.+)$",
            r"^(Общая площадь)\s+(.+)$",
            r"^(Жилая площадь)\s+(.+)$",
            r"^(Площадь кухни)\s+(.+)$",
            r"^(Этаж)\s+(.+)$",
            r"^(Комнат)\s+(.+)$",
            r"^(Количество комнат)\s+(.+)$",
            r"^(Тип дома)\s+(.+)$",
            r"^(Материал стен)\s+(.+)$",
            r"^(Ремонт)\s+(.+)$",
            r"^(Санузел)\s+(.+)$",
        ]

        for pattern in patterns:
            match = re.match(pattern, text, flags=re.IGNORECASE)
            if match:
                return normalize_text(match.group(1)), normalize_text(match.group(2))

        return None

    def _get_images(self, soup, card_url):
        images = []
        seen = set()

        for img in soup.select("img"):
            candidates = [
                img.get("src"),
                img.get("data-src"),
                img.get("data-lazy"),
                img.get("data-original"),
                img.get("data-url"),
                img.get("data-full"),
            ]

            srcset = img.get("srcset")
            if srcset:
                for part in srcset.split(","):
                    candidates.append(part.strip().split(" ")[0])

            for src in candidates:
                if not src:
                    continue

                if src.startswith("//"):
                    src = "https:" + src

                src = urljoin(card_url, src)

                if not src.startswith("http"):
                    continue

                lowered = src.lower()

                if any(x in lowered for x in ["logo", "icon", "sprite", "avatar", "captcha"]):
                    continue

                if src not in seen:
                    seen.add(src)
                    images.append(src)

        og_image = soup.select_one("meta[property='og:image']")
        if og_image and og_image.get("content"):
            src = urljoin(card_url, og_image.get("content"))
            if src not in seen:
                images.append(src)

        return images

    def _extract_json_ld(self, soup):
        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.string or "")

                if isinstance(data, dict):
                    return data

                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            return item

            except Exception:
                continue

        return {}


def parse_farpost_realty(
    price_min,
    price_max,
    city,
    search_query,
    max_items,
    *,
    max_pages=5,
    headless=HEADLESS,
):
    if max_items <= 0:
        return []

    domain = get_domain(city)

    result = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()

        crawler = FarpostCrawler(
            page,
            domain,
            search_query,
            price_min,
            price_max,
            max_pages,
            max_items,
        )

        parser = FarpostParser(page)

        urls = crawler.collect_urls()

        for i, url in enumerate(urls):
            logger.info(f"{i+1}/{len(urls)} {url}")

            item = parser.parse(url)
            result.append(asdict(item))

            if len(result) >= max_items:
                break

        browser.close()

    return result


if __name__ == "__main__":
    data = parse_farpost_realty(
        price_min=3_000_000,
        price_max=10_000_000,
        city="khabarovsk",
        search_query="квартира",
        max_items=10,
        headless=False,
    )

    print(json.dumps(data, ensure_ascii=False, indent=2))
