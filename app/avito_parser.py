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
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


AVITO_BASE_URL = "https://www.avito.ru"
RAW_HTML_DIR = "raw_html"

# Для API/сервера лучше True.
# Для локальной отладки, если видишь капчу/блокировку, временно ставь False.
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


def looks_like_block_or_empty_page(html: str) -> bool:
    """
    Грубая диагностика: если Avito отдал защитную/пустую страницу,
    обычные селекторы карточки не сработают.
    """
    text = html.lower()

    block_markers = [
        "доступ ограничен",
        "access denied",
        "captcha",
        "подозрительная активность",
        "проверяем ваш браузер",
        "enable javascript",
        "включите javascript",
    ]

    if any(marker in text for marker in block_markers):
        return True

    # Слишком короткий HTML для карточки.
    if len(html) < 20_000:
        return True

    return False


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

        self._human_like_scroll()
        return self.page.content()

    def _human_like_scroll(self) -> None:
        for _ in range(2):
            self.page.mouse.wheel(0, random.randint(600, 1200))
            self.page.wait_for_timeout(random.randint(500, 900))

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

            if looks_like_block_or_empty_page(html):
                logger.warning(
                    "Card page looks blocked or incomplete. html_len=%s raw_path=%s url=%s",
                    len(html),
                    raw_path,
                    url,
                )

            soup = BeautifulSoup(html, "lxml")
            embedded_data = self._extract_embedded_data(soup)

            title = self._extract_title(soup, embedded_data)
            price = self._extract_price(soup, embedded_data)

            return Listing(
                url=url,
                title=title,
                price=price,
                address=self._extract_address(soup, embedded_data),
                description=self._extract_description(soup, embedded_data),
                seller=self._extract_seller(soup, embedded_data),
                params=self._extract_params(soup, embedded_data),
                images=self._extract_images(soup, embedded_data),
                raw_html_path=raw_path,
                error=None if title or price else "content_not_found_or_blocked",
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

        # Важно: domcontentloaded недостаточно. Avito часть карточки дорисовывает позже.
        self.page.wait_for_timeout(random.randint(2500, 4000))

        self._human_like_card_actions()

        # Ждём не только старый data-marker, а любой признак нормальной карточки.
        selectors = [
            "h1",
            "[data-marker='item-view/title-info']",
            "[data-marker='item-view/item-title']",
            "[data-marker='item-view/item-price']",
            "[data-marker='item-view/item-description']",
            "[itemprop='name']",
            "[itemprop='price']",
            "script[type='application/ld+json']",
        ]

        found = False
        for selector in selectors:
            try:
                self.page.wait_for_selector(selector, timeout=4_000)
                found = True
                break
            except PlaywrightTimeoutError:
                continue

        if not found:
            logger.warning("Card content selectors not found: %s", url)

        # Дополнительное ожидание стабилизации DOM.
        self.page.wait_for_load_state("domcontentloaded", timeout=30_000)
        self.page.wait_for_timeout(random.randint(1500, 2500))

        html = self.page.content()

        # Если получили явную пустоту/блокировку, пробуем reload.
        if looks_like_block_or_empty_page(html):
            logger.warning("First card HTML looks incomplete, reload once: %s", url)
            self.page.reload(wait_until="domcontentloaded", timeout=60_000)
            self.page.wait_for_timeout(random.randint(4000, 6000))
            self._human_like_card_actions()
            html = self.page.content()

        return html

    def _human_like_card_actions(self) -> None:
        try:
            self.page.mouse.move(random.randint(200, 900), random.randint(150, 650))
            self.page.wait_for_timeout(random.randint(300, 700))

            for _ in range(3):
                self.page.mouse.wheel(0, random.randint(500, 1100))
                self.page.wait_for_timeout(random.randint(500, 1000))

            self.page.mouse.wheel(0, -random.randint(300, 800))
            self.page.wait_for_timeout(random.randint(500, 900))
        except Exception:
            # Не критично. Нужно только для прогрузки ленивых блоков.
            pass

    def _select_text(self, soup: BeautifulSoup, selectors: list[str]) -> str | None:
        for selector in selectors:
            node = soup.select_one(selector)

            if not node:
                continue

            content = node.get("content")
            text = normalize_text(content or node.get_text(" ", strip=True))

            if text:
                return text

        return None

    def _extract_title(self, soup: BeautifulSoup, data: dict | None = None) -> str | None:
        title = self._select_text(
            soup,
            [
                "h1[data-marker='item-view/title-info']",
                "[data-marker='item-view/title-info'] h1",
                "[data-marker='item-view/title-info']",
                "[data-marker='item-view/item-title']",
                "h1[itemprop='name']",
                "[itemprop='name']",
                "meta[property='og:title']",
                "meta[name='twitter:title']",
                "h1",
                "title",
            ],
        )

        if title:
            # Иногда <title> содержит мусор после разделителя.
            return re.split(r"\s+[—|-]\s+Авито", title)[0].strip()

        json_ld = self._extract_json_ld(soup)
        if json_ld.get("name"):
            return normalize_text(str(json_ld.get("name")))

        return self._deep_find_first_string(
            data,
            keys=("title", "name", "itemTitle"),
            min_len=3,
        )

    def _extract_price(self, soup: BeautifulSoup, data: dict | None = None) -> int | None:
        selectors = [
            "[data-marker='item-view/item-price']",
            "[data-marker='item-view/price']",
            "[itemprop='price']",
            "meta[property='product:price:amount']",
            "span[class*='price']",
            "div[class*='price']",
        ]

        for selector in selectors:
            node = soup.select_one(selector)

            if not node:
                continue

            content = node.get("content") or node.get("value") or node.get_text(" ", strip=True)
            price = parse_price(content)

            if price:
                return price

        json_ld = self._extract_json_ld(soup)
        offers = json_ld.get("offers") if json_ld else None

        if isinstance(offers, dict):
            price = parse_price(str(offers.get("price")))
            if price:
                return price

        embedded_price = self._deep_find_first_number(
            data,
            keys=("price", "value", "amount"),
        )
        return embedded_price

    def _extract_address(self, soup: BeautifulSoup, data: dict | None = None) -> str | None:
        address = self._select_text(
            soup,
            [
                "[data-marker='item-view/item-address']",
                "[data-marker='delivery/location']",
                "[itemprop='address']",
                "span[class*='address']",
                "div[class*='address']",
                "meta[property='og:street-address']",
            ],
        )

        if address:
            return address

        json_ld = self._extract_json_ld(soup)
        ld_address = json_ld.get("address") if json_ld else None
        if isinstance(ld_address, str):
            return normalize_text(ld_address)
        if isinstance(ld_address, dict):
            values = [str(v) for v in ld_address.values() if v]
            if values:
                return normalize_text(", ".join(values))

        return self._deep_find_first_string(
            data,
            keys=("address", "location", "geoAddress"),
            min_len=5,
        )

    def _extract_description(self, soup: BeautifulSoup, data: dict | None = None) -> str | None:
        desc = self._select_text(
            soup,
            [
                "[data-marker='item-view/item-description']",
                "[data-marker='item-view/description']",
                "[itemprop='description']",
                "meta[property='og:description']",
                "meta[name='description']",
                "div[class*='description']",
            ],
        )

        if desc:
            return desc

        json_ld = self._extract_json_ld(soup)
        if json_ld.get("description"):
            return normalize_text(str(json_ld.get("description")))

        return self._deep_find_first_string(
            data,
            keys=("description", "itemDescription"),
            min_len=20,
        )

    def _extract_seller(self, soup: BeautifulSoup, data: dict | None = None) -> str | None:
        seller = self._select_text(
            soup,
            [
                "[data-marker='seller-info/name']",
                "[data-marker='seller-link/link']",
                "[data-marker='seller-info/label']",
                "[data-marker='seller-info']",
                "div[class*='seller'] a",
                "div[class*='seller']",
            ],
        )

        if seller:
            return seller

        return self._deep_find_first_string(
            data,
            keys=("sellerName", "seller", "shopName", "userName"),
            min_len=2,
        )

    def _extract_params(self, soup: BeautifulSoup, data: dict | None = None) -> dict[str, str]:
        params: dict[str, str] = {}

        selectors = [
            "[data-marker='item-view/item-params'] li",
            "[data-marker='item-view/params'] li",
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

        if not params and data:
            params.update(self._extract_params_from_embedded_data(data))

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
            r"^(Класс здания)\s+(.+)$",
            r"^(Назначение помещения)\s+(.+)$",
            r"^(Площадь помещения)\s+(.+)$",
            r"^(Тип недвижимости)\s+(.+)$",
        ]

        for pattern in patterns:
            match = re.match(pattern, text, flags=re.IGNORECASE)

            if match:
                key = normalize_text(match.group(1))
                value = normalize_text(match.group(2))
                if key and value:
                    return key, value

        return None

    def _extract_images(self, soup: BeautifulSoup, data: dict | None = None) -> list[str]:
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
                src = self._normalize_image_url(src)
                if not src:
                    continue

                if src not in seen:
                    seen.add(src)
                    images.append(src)

        json_ld = self._extract_json_ld(soup)
        image_data = json_ld.get("image") if json_ld else None

        if isinstance(image_data, str):
            src = self._normalize_image_url(image_data)
            if src and src not in seen:
                seen.add(src)
                images.append(src)

        elif isinstance(image_data, list):
            for image_url in image_data:
                if isinstance(image_url, str):
                    src = self._normalize_image_url(image_url)
                    if src and src not in seen:
                        seen.add(src)
                        images.append(src)

        if data:
            embedded_images = self._deep_find_image_urls(data)
            for image_url in embedded_images:
                src = self._normalize_image_url(image_url)
                if src and src not in seen:
                    seen.add(src)
                    images.append(src)

        return images

    def _normalize_image_url(self, src: str | None) -> str | None:
        if not src:
            return None

        if src.startswith("//"):
            src = "https:" + src

        if src.startswith("/"):
            src = urljoin(AVITO_BASE_URL, src)

        if not src.startswith("http"):
            return None

        return src

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

    def _extract_embedded_data(self, soup: BeautifulSoup) -> dict:
        """
        Avito часто кладёт данные не в обычный DOM, а в JSON внутри script.
        Эта функция собирает пригодные JSON-объекты из __NEXT_DATA__,
        window.__initialData__, и похожих скриптов.
        """
        merged: dict = {}

        for script in soup.select("script"):
            raw = script.string or script.get_text() or ""
            raw = raw.strip()

            if not raw:
                continue

            # Next.js формат.
            if script.get("id") == "__NEXT_DATA__":
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        merged["__NEXT_DATA__"] = data
                except Exception:
                    pass

            # Поиск крупных JSON-блоков в JS.
            # Да, это эвристика, но она часто спасает, когда DOM пустой.
            for pattern in [
                r"window\.__initialData__\s*=\s*({.*?})\s*;",
                r"window\.__INITIAL_STATE__\s*=\s*({.*?})\s*;",
                r"window\.__data__\s*=\s*({.*?})\s*;",
            ]:
                match = re.search(pattern, raw, flags=re.DOTALL)
                if not match:
                    continue

                candidate = match.group(1)
                try:
                    data = json.loads(candidate)
                    if isinstance(data, dict):
                        merged[f"script_{len(merged)}"] = data
                except Exception:
                    continue

        return merged

    def _deep_find_first_string(
        self,
        obj,
        *,
        keys: tuple[str, ...],
        min_len: int = 1,
        max_depth: int = 8,
    ) -> str | None:
        if obj is None or max_depth < 0:
            return None

        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in keys and isinstance(value, str):
                    text = normalize_text(value)
                    if text and len(text) >= min_len:
                        return text

            for value in obj.values():
                result = self._deep_find_first_string(
                    value,
                    keys=keys,
                    min_len=min_len,
                    max_depth=max_depth - 1,
                )
                if result:
                    return result

        elif isinstance(obj, list):
            for item in obj:
                result = self._deep_find_first_string(
                    item,
                    keys=keys,
                    min_len=min_len,
                    max_depth=max_depth - 1,
                )
                if result:
                    return result

        return None

    def _deep_find_first_number(
        self,
        obj,
        *,
        keys: tuple[str, ...],
        max_depth: int = 8,
    ) -> int | None:
        if obj is None or max_depth < 0:
            return None

        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in keys:
                    if isinstance(value, int) and value > 0:
                        return value
                    if isinstance(value, str):
                        parsed = parse_price(value)
                        if parsed:
                            return parsed

            for value in obj.values():
                result = self._deep_find_first_number(
                    value,
                    keys=keys,
                    max_depth=max_depth - 1,
                )
                if result:
                    return result

        elif isinstance(obj, list):
            for item in obj:
                result = self._deep_find_first_number(
                    item,
                    keys=keys,
                    max_depth=max_depth - 1,
                )
                if result:
                    return result

        return None

    def _deep_find_image_urls(self, obj, max_depth: int = 8) -> list[str]:
        found: list[str] = []

        if obj is None or max_depth < 0:
            return found

        if isinstance(obj, dict):
            for key, value in obj.items():
                key_lower = str(key).lower()

                if any(part in key_lower for part in ("image", "images", "photo", "photos", "url")):
                    if isinstance(value, str) and ("http" in value or value.startswith("//")):
                        if re.search(r"\.(jpg|jpeg|png|webp)", value, flags=re.IGNORECASE):
                            found.append(value)
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, str) and ("http" in item or item.startswith("//")):
                                if re.search(r"\.(jpg|jpeg|png|webp)", item, flags=re.IGNORECASE):
                                    found.append(item)

                found.extend(self._deep_find_image_urls(value, max_depth=max_depth - 1))

        elif isinstance(obj, list):
            for item in obj:
                found.extend(self._deep_find_image_urls(item, max_depth=max_depth - 1))

        return found

    def _extract_params_from_embedded_data(self, data: dict) -> dict[str, str]:
        params: dict[str, str] = {}

        def walk(obj, depth: int = 0):
            if depth > 8:
                return

            if isinstance(obj, dict):
                # Частые варианты: {"title": "...", "value": "..."} или {"name": "...", "value": "..."}
                key = obj.get("title") or obj.get("name") or obj.get("label")
                value = obj.get("value") or obj.get("text")

                if isinstance(key, str) and isinstance(value, (str, int, float)):
                    clean_key = normalize_text(key)
                    clean_value = normalize_text(str(value))
                    if clean_key and clean_value and len(clean_key) <= 80:
                        params[clean_key] = clean_value

                for child in obj.values():
                    walk(child, depth + 1)

            elif isinstance(obj, list):
                for child in obj:
                    walk(child, depth + 1)

        walk(data)
        return params


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
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )

            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 768},
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                java_script_enabled=True,
                extra_http_headers={
                    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                },
            )

            # Маскировка базовых webdriver-признаков.
            context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['ru-RU', 'ru', 'en-US', 'en']
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                """
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
                    "Parsed: title=%r price=%r url=%s error=%r",
                    listing.title,
                    listing.price,
                    listing.url,
                    listing.error,
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
