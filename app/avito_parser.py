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


AVITO_BASE_URL = "https://www.avito.ru"
RAW_HTML_DIR = "raw_html"

# Для локального ручного запуска можно поставить False.
# Для FastAPI/сервера лучше True, иначе браузер может не стартовать без GUI.
HEADLESS = True

REQUEST_DELAY_MIN = 2.5
REQUEST_DELAY_MAX = 6.0

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("avito_parser")


@dataclass
class Listing:
    url: str
    title: str | None = None
    price: int | None = None
    address: str | None = None
    description: str | None = None
    seller: str | None = None
    params: dict[str, str] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    raw_html_path: str | None = None
    error: str | None = None


def random_delay() -> None:
    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))


def retryable(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        last_error = None

        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_error = exc
                sleep_time = RETRY_BASE_DELAY * attempt + random.uniform(0, 1.5)

                logger.warning(
                    "Retry %s/%s failed in %s: %s",
                    attempt,
                    RETRY_ATTEMPTS,
                    fn.__name__,
                    exc,
                )

                time.sleep(sleep_time)

        raise last_error

    return wrapper


def normalize_text(value: str | None) -> str | None:
    if not value:
        return None

    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def parse_price(value: str | None) -> int | None:
    if not value:
        return None

    digits = re.sub(r"[^\d]", "", value)

    if not digits:
        return None

    return int(digits)


def safe_filename_from_url(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"{digest}.html"


def save_raw_html(url: str, html: str, raw_dir: str = RAW_HTML_DIR) -> str:
    Path(raw_dir).mkdir(parents=True, exist_ok=True)

    path = Path(raw_dir) / safe_filename_from_url(url)
    path.write_text(html, encoding="utf-8")

    return str(path)


def build_search_url(
    city: str,
    search_query: str,
    price_min: int | None,
    price_max: int | None,
    page: int = 1,
    category: str = "nedvizhimost",
) -> str:
    city = city.strip().strip("/")
    category = category.strip().strip("/")

    query = {
        "q": search_query.strip(),
        "pmin": price_min,
        "pmax": price_max,
    }

    if page > 1:
        query["p"] = page

    query = {k: v for k, v in query.items() if v not in (None, "")}

    return f"{AVITO_BASE_URL}/{city}/{category}?{urlencode(query)}"


class AvitoCrawler:
    def __init__(
        self,
        page: Page,
        city: str,
        search_query: str,
        price_min: int | None,
        price_max: int | None,
        max_pages: int,
        max_cards: int,
        category: str = "nedvizhimost",
    ):
        self.page = page
        self.city = city
        self.search_query = search_query
        self.price_min = price_min
        self.price_max = price_max
        self.max_pages = max_pages
        self.max_cards = max_cards
        self.category = category

    def collect_listing_urls(self) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()

        for page_num in range(1, self.max_pages + 1):
            if len(result) >= self.max_cards:
                break

            search_url = build_search_url(
                city=self.city,
                search_query=self.search_query,
                price_min=self.price_min,
                price_max=self.price_max,
                page=page_num,
                category=self.category,
            )

            logger.info("Open search page %s: %s", page_num, search_url)

            try:
                html = self._load_search_page(search_url)
                urls = self._extract_listing_urls(html)

                logger.info("Found %s URLs on page %s", len(urls), page_num)

                for url in urls:
                    if url not in seen:
                        seen.add(url)
                        result.append(url)

                    if len(result) >= self.max_cards:
                        break

            except Exception as exc:
                logger.exception("Failed to process search page %s: %s", page_num, exc)

            random_delay()

        return result[: self.max_cards]

    @retryable
    def _load_search_page(self, url: str) -> str:
        self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        self.page.wait_for_timeout(2500)

        try:
            self.page.wait_for_selector(
                "a[data-marker='item-title'], div[data-marker='item'] a[href], a[href*='_']",
                timeout=10_000,
            )
        except Exception:
            logger.warning("No listing links found, continue with page content")

        return self.page.content()

    def _extract_listing_urls(self, html: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")

        urls: list[str] = []
        seen: set[str] = set()

        selectors = [
            "a[data-marker='item-title']",
            "a[itemprop='url']",
            "div[data-marker='item'] a[href]",
            "a[href*='_']",
        ]

        for selector in selectors:
            for a in soup.select(selector):
                href = a.get("href")

                if not href:
                    continue

                url = self._normalize_avito_url(href)

                if not url:
                    continue

                if url not in seen:
                    seen.add(url)
                    urls.append(url)

            if urls:
                break

        return urls

    def _normalize_avito_url(self, href: str) -> str | None:
        full_url = urljoin(AVITO_BASE_URL, href)
        parsed = urlparse(full_url)

        if "avito.ru" not in parsed.netloc:
            return None

        path = parsed.path

        # Карточки Avito обычно заканчиваются на _числовой_id.
        if not re.search(r"_\d+$", path):
            return None

        return f"{AVITO_BASE_URL}{path}"


class AvitoCardParser:
    def __init__(self, page: Page, save_html: bool = True):
        self.page = page
        self.save_html = save_html

    def parse_listing(self, url: str) -> Listing:
        try:
            html = self._load_card(url)

            raw_path = None
            if self.save_html:
                raw_path = save_raw_html(url, html, RAW_HTML_DIR)

            soup = BeautifulSoup(html, "lxml")

            return Listing(
                url=url,
                title=self._extract_title(soup),
                price=self._extract_price(soup),
                address=self._extract_address(soup),
                description=self._extract_description(soup),
                seller=self._extract_seller(soup),
                params=self._extract_params(soup),
                images=self._extract_images(soup),
                raw_html_path=raw_path,
            )

        except Exception as exc:
            logger.exception("Failed to parse card %s: %s", url, exc)

            return Listing(
                url=url,
                params={},
                images=[],
                error=str(exc),
            )

        finally:
            random_delay()

    @retryable
    def _load_card(self, url: str) -> str:
        logger.info("Open card: %s", url)

        self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        self.page.wait_for_timeout(3000)

        try:
            self.page.wait_for_selector(
                "h1, [data-marker='item-view/title-info']",
                timeout=15_000,
            )
        except Exception:
            logger.warning("Card title selector not found: %s", url)

        return self.page.content()

    def _select_text(self, soup: BeautifulSoup, selectors: list[str]) -> str | None:
        for selector in selectors:
            node = soup.select_one(selector)

            if not node:
                continue

            text = normalize_text(node.get_text(" ", strip=True))

            if text:
                return text

        return None

    def _extract_title(self, soup: BeautifulSoup) -> str | None:
        title = self._select_text(
            soup,
            [
                "h1[data-marker='item-view/title-info']",
                "h1[itemprop='name']",
                "[data-marker='item-view/title-info']",
                "h1",
            ],
        )

        if title:
            return title

        json_ld = self._extract_json_ld(soup)
        return json_ld.get("name") if json_ld else None

    def _extract_price(self, soup: BeautifulSoup) -> int | None:
        selectors = [
            "[data-marker='item-view/item-price']",
            "[itemprop='price']",
            "span[class*='price']",
            "div[class*='price']",
        ]

        for selector in selectors:
            node = soup.select_one(selector)

            if not node:
                continue

            content = node.get("content") or node.get_text(" ", strip=True)
            price = parse_price(content)

            if price:
                return price

        json_ld = self._extract_json_ld(soup)
        offers = json_ld.get("offers") if json_ld else None

        if isinstance(offers, dict):
            return parse_price(str(offers.get("price")))

        return None

    def _extract_address(self, soup: BeautifulSoup) -> str | None:
        return self._select_text(
            soup,
            [
                "[data-marker='item-view/item-address']",
                "[itemprop='address']",
                "span[class*='address']",
                "div[class*='address']",
            ],
        )

    def _extract_description(self, soup: BeautifulSoup) -> str | None:
        desc = self._select_text(
            soup,
            [
                "[data-marker='item-view/item-description']",
                "[itemprop='description']",
                "div[class*='description']",
            ],
        )

        if desc:
            return desc

        json_ld = self._extract_json_ld(soup)
        return json_ld.get("description") if json_ld else None

    def _extract_seller(self, soup: BeautifulSoup) -> str | None:
        return self._select_text(
            soup,
            [
                "[data-marker='seller-info/name']",
                "[data-marker='seller-link/link']",
                "[data-marker='seller-info/label']",
                "div[class*='seller'] a",
                "div[class*='seller']",
            ],
        )

    def _extract_params(self, soup: BeautifulSoup) -> dict[str, str]:
        params: dict[str, str] = {}

        selectors = [
            "[data-marker='item-view/item-params'] li",
            "ul[class*='params'] li",
            "div[class*='params'] li",
            "li[class*='params']",
        ]

        for selector in selectors:
            nodes = soup.select(selector)

            for node in nodes:
                text = normalize_text(node.get_text(" ", strip=True))

                if not text:
                    continue

                parsed = self._parse_param_line(text)

                if parsed:
                    key, value = parsed
                    params[key] = value

            if params:
                break

        if not params:
            for dt in soup.select("dt"):
                dd = dt.find_next_sibling("dd")

                if not dd:
                    continue

                key = normalize_text(dt.get_text(" ", strip=True))
                value = normalize_text(dd.get_text(" ", strip=True))

                if key and value:
                    params[key] = value

        return params

    def _parse_param_line(self, text: str) -> tuple[str, str] | None:
        if ":" in text:
            key, value = text.split(":", 1)
            key = normalize_text(key)
            value = normalize_text(value)

            if key and value:
                return key, value

        patterns = [
            r"^(Общая площадь)\s+(.+)$",
            r"^(Жилая площадь)\s+(.+)$",
            r"^(Площадь кухни)\s+(.+)$",
            r"^(Этаж)\s+(.+)$",
            r"^(Количество комнат)\s+(.+)$",
            r"^(Тип комнат)\s+(.+)$",
            r"^(Санузел)\s+(.+)$",
            r"^(Ремонт)\s+(.+)$",
            r"^(Способ продажи)\s+(.+)$",
            r"^(Вид сделки)\s+(.+)$",
            r"^(Тип дома)\s+(.+)$",
        ]

        for pattern in patterns:
            match = re.match(pattern, text, flags=re.IGNORECASE)

            if match:
                key = normalize_text(match.group(1))
                value = normalize_text(match.group(2))

                if key and value:
                    return key, value

        return None

    def _extract_images(self, soup: BeautifulSoup) -> list[str]:
        images: list[str] = []
        seen: set[str] = set()

        for img in soup.select("img"):
            candidates = [
                img.get("src"),
                img.get("data-src"),
                img.get("data-lazy"),
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

                if not src.startswith("http"):
                    continue

                if src not in seen:
                    seen.add(src)
                    images.append(src)

        json_ld = self._extract_json_ld(soup)
        image_data = json_ld.get("image") if json_ld else None

        if isinstance(image_data, str) and image_data not in seen:
            seen.add(image_data)
            images.append(image_data)

        elif isinstance(image_data, list):
            for image_url in image_data:
                if isinstance(image_url, str) and image_url not in seen:
                    seen.add(image_url)
                    images.append(image_url)

        return images

    def _extract_json_ld(self, soup: BeautifulSoup) -> dict:
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


def parse_avito_realty(
    price_min: int | None,
    price_max: int | None,
    city: str,
    search_query: str,
    max_items: int,
    *,
    max_pages: int = 5,
    category: str = "nedvizhimost",
    headless: bool = HEADLESS,
    save_html: bool = True,
) -> list[dict]:
    if not city or not city.strip():
        raise ValueError("city не может быть пустым")

    if not search_query or not search_query.strip():
        raise ValueError("search_query не может быть пустым")

    if price_min is not None and price_max is not None and price_min > price_max:
        raise ValueError("price_min не может быть больше price_max")

    if max_items <= 0:
        return []

    listings: list[dict] = []

    with sync_playwright() as p:
        browser = None
        context = None

        try:
            browser = p.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )

            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 768},
                locale="ru-RU",
                timezone_id="Europe/Moscow",
            )

            page = context.new_page()

            crawler = AvitoCrawler(
                page=page,
                city=city,
                search_query=search_query,
                price_min=price_min,
                price_max=price_max,
                max_pages=max_pages,
                max_cards=max_items,
                category=category,
            )

            parser = AvitoCardParser(
                page=page,
                save_html=save_html,
            )

            urls = crawler.collect_listing_urls()
            logger.info("Total collected URLs: %s", len(urls))

            for index, url in enumerate(urls, start=1):
                logger.info("Parse card %s/%s", index, len(urls))

                listing = parser.parse_listing(url)
                listings.append(asdict(listing))

                logger.info(
                    "Parsed: title=%r price=%r url=%s",
                    listing.title,
                    listing.price,
                    listing.url,
                )

                if len(listings) >= max_items:
                    break

        finally:
            if context is not None:
                context.close()

            if browser is not None:
                browser.close()

    return listings


if __name__ == "__main__":
    data = parse_avito_realty(
        price_min=8_000_000,
        price_max=15_000_000,
        city="moskva",
        search_query="склад",
        max_items=20,
        max_pages=3,
        category="nedvizhimost",
        headless=False,
        save_html=True,
    )

    print(json.dumps(data, ensure_ascii=False, indent=2))
