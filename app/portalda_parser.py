import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_TIMEOUT_MS = 30_000
RETRIES = 3


def clean_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def abs_url(base_url: str, value: str) -> Optional[str]:
    if not value:
        return None

    value = value.strip().strip('"').strip("'")

    if value.startswith("data:"):
        return None

    if value.startswith("//"):
        return "https:" + value

    return urljoin(base_url, value)


def parse_price(text: str) -> Optional[int]:
    if not text:
        return None

    patterns = [
        r"([\d\s]+)\s*₽",
        r"([\d\s]+)\s*руб",
        r"Цена[:\s]+([\d\s]+)",
        r"Стоимость[:\s]+([\d\s]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            digits = re.sub(r"\D", "", match.group(1))
            if digits:
                return int(digits)

    return None


def extract_first_text(soup: BeautifulSoup, selectors: List[str]) -> Optional[str]:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            text = clean_text(node.get_text(" ", strip=True))
            if text:
                return text
    return None


def extract_title(soup: BeautifulSoup, body_text: str) -> str:
    title = extract_first_text(soup, ["h1"])
    if title:
        return title

    og_title = soup.select_one('meta[property="og:title"], meta[name="title"]')
    if og_title and og_title.get("content"):
        return clean_text(og_title.get("content")) or ""

    lines = [x.strip() for x in body_text.splitlines() if x.strip()]
    return lines[0] if lines else ""


def extract_price(soup: BeautifulSoup, body_text: str) -> int:
    price_texts = []

    for node in soup.select('[class*="price" i]'):
        text = clean_text(node.get_text(" ", strip=True))
        if text:
            price_texts.append(text)

    for text in price_texts:
        price = parse_price(text)
        if price is not None:
            return price

    price = parse_price(body_text)
    return price if price is not None else 0


def extract_address(soup: BeautifulSoup, body_text: str) -> str:
    address = extract_first_text(soup, [".asset-map"])
    if address:
        address = re.sub(r"^(Адрес|На карте)\s*:?\s*", "", address, flags=re.IGNORECASE)
        return clean_text(address) or ""

    patterns = [
        r"Адрес[:\s]+(.+?)(?:Описание|Характеристики|Продавец|Цена|$)",
        r"Расположение[:\s]+(.+?)(?:Описание|Характеристики|Продавец|Цена|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, body_text, re.IGNORECASE | re.DOTALL)
        if match:
            value = clean_text(match.group(1))
            if value:
                return value

    return ""


def extract_description(soup: BeautifulSoup, body_text: str) -> str:
    description = extract_first_text(
        soup,
        [
            ".asset-page-description__content",
            ".asset-page-description__content_short",
            '[class*="description" i]',
        ],
    )

    if description:
        return description

    patterns = [
        r"Описание[:\s]+(.+?)(?:Характеристики|Продавец|Контакты|Адрес|Похожие|$)",
        r"Об объекте[:\s]+(.+?)(?:Характеристики|Продавец|Контакты|Адрес|Похожие|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, body_text, re.IGNORECASE | re.DOTALL)
        if match:
            value = clean_text(match.group(1))
            if value:
                return value

    return ""


def normalize_param_key(value: str) -> str:
    value = clean_text(value) or ""
    value = value.strip(":").strip()
    return value


def extract_params_from_text(text: str) -> Dict[str, str]:
    params = {}

    text = clean_text(text) or ""
    if not text:
        return params

    common_keys = [
        "Площадь",
        "Этаж",
        "Этажность",
        "Комнат",
        "Количество комнат",
        "Тип объекта",
        "Тип дома",
        "Материал стен",
        "Участок",
        "Площадь участка",
        "Санузел",
        "Ремонт",
        "Год постройки",
        "Назначение",
        "Кадастровый номер",
    ]

    for key in common_keys:
        pattern = rf"{re.escape(key)}\s*:?\s+(.+?)(?={'|'.join(map(re.escape, common_keys))}|$)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = clean_text(match.group(1))
            if value and len(value) <= 120:
                params[key] = value.strip(" :")

    return params


def extract_params(soup: BeautifulSoup, body_text: str) -> Dict[str, str]:
    params = {}

    wrappers = soup.select(".asset-page-characteristics__content-wrapper")
    if not wrappers:
        wrappers = soup.select('[class*="characteristic" i], [class*="param" i], [class*="properties" i]')

    for wrapper in wrappers:
        rows = wrapper.select("li, tr, .row, div")

        for row in rows:
            text = clean_text(row.get_text(" ", strip=True))
            if not text:
                continue

            key_node = row.select_one('[class*="name" i], [class*="label" i], [class*="title" i], dt')
            value_node = row.select_one('[class*="value" i], [class*="text" i], dd')

            if key_node and value_node:
                key = normalize_param_key(key_node.get_text(" ", strip=True))
                value = clean_text(value_node.get_text(" ", strip=True))
                if key and value and key != value:
                    params[key] = value
                    continue

            if ":" in text:
                left, right = text.split(":", 1)
                key = normalize_param_key(left)
                value = clean_text(right)
                if key and value and len(key) <= 60:
                    params[key] = value

        if not params:
            text = clean_text(wrapper.get_text(" ", strip=True))
            params.update(extract_params_from_text(text or ""))

    if not params:
        match = re.search(
            r"Характеристики[:\s]+(.+?)(?:Описание|Продавец|Контакты|Адрес|Похожие|$)",
            body_text,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            params.update(extract_params_from_text(match.group(1)))

    return params


def extract_seller(soup: BeautifulSoup, body_text: str) -> str:
    seller = extract_first_text(
        soup,
        [
            '[class*="seller" i]',
            '[class*="agent" i]',
            '[class*="author" i]',
            '[class*="owner" i]',
            '[class*="realtor" i]',
        ],
    )

    if seller:
        seller = re.sub(r"^(Продавец|Агент|Автор)\s*:?\s*", "", seller, flags=re.IGNORECASE)
        return clean_text(seller) or ""

    patterns = [
        r"Продавец[:\s]+(.+?)(?:Телефон|Контакты|Написать|Показать|$)",
        r"Агент[:\s]+(.+?)(?:Телефон|Контакты|Написать|Показать|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, body_text, re.IGNORECASE | re.DOTALL)
        if match:
            value = clean_text(match.group(1))
            if value:
                return value

    return ""


def is_bad_image(url: str) -> bool:
    lowered = url.lower()

    bad_parts = [
        "logo",
        "icon",
        "avatar",
        "sprite",
        "placeholder",
        "favicon",
        "userpic",
        "profile",
    ]

    if any(x in lowered for x in bad_parts):
        return True

    parsed = urlparse(url)
    path = parsed.path.lower()

    if not re.search(r"\.(jpg|jpeg|png|webp|avif)(\?|$)", path):
        return False

    return False


def extract_url_from_srcset(srcset: str) -> List[str]:
    urls = []

    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        candidate = part.split()[0].strip()
        if candidate:
            urls.append(candidate)

    return urls


def extract_background_urls(style: str) -> List[str]:
    if not style:
        return []

    return re.findall(r"url\((.*?)\)", style, flags=re.IGNORECASE)


def extract_images(soup: BeautifulSoup, base_url: str) -> List[str]:
    result = []
    seen = set()

    containers = soup.select(".swiper-wrapper, .swiper")

    for container in containers:
        for img in container.select("img"):
            candidates = []

            for attr in ["src", "data-src", "data-lazy-src", "data-original"]:
                value = img.get(attr)
                if value:
                    candidates.append(value)

            srcset = img.get("srcset") or img.get("data-srcset")
            if srcset:
                candidates.extend(extract_url_from_srcset(srcset))

            for candidate in candidates:
                full = abs_url(base_url, candidate)
                if full and not is_bad_image(full) and full not in seen:
                    seen.add(full)
                    result.append(full)

        for node in container.select("[style]"):
            for candidate in extract_background_urls(node.get("style", "")):
                full = abs_url(base_url, candidate)
                if full and not is_bad_image(full) and full not in seen:
                    seen.add(full)
                    result.append(full)

        for node in container.select("[data-background], [data-bg], [data-image]"):
            for attr in ["data-background", "data-bg", "data-image"]:
                value = node.get(attr)
                if value:
                    full = abs_url(base_url, value)
                    if full and not is_bad_image(full) and full not in seen:
                        seen.add(full)
                        result.append(full)

    return result


def force_gallery_load(page) -> None:
    page.evaluate(
        """
        () => {
            window.scrollTo(0, 0);
            document.querySelectorAll('.swiper, .swiper-wrapper').forEach(el => {
                el.scrollIntoView({block: 'center'});
            });
        }
        """
    )

    page.wait_for_timeout(500)

    next_buttons = page.locator(".swiper-button-next")
    count = min(next_buttons.count(), 3)

    for i in range(count):
        button = next_buttons.nth(i)
        try:
            if button.is_visible():
                for _ in range(12):
                    try:
                        button.click(timeout=1500)
                        page.wait_for_timeout(250)
                    except Exception:
                        break
        except Exception:
            continue

    for y in [400, 900, 1400, 2000, 2600, 3200]:
        page.evaluate(f"window.scrollTo(0, {y})")
        page.wait_for_timeout(250)

    page.evaluate(
        """
        () => {
            document.querySelectorAll('.swiper, .swiper-wrapper').forEach(el => {
                el.scrollIntoView({block: 'center'});
            });
        }
        """
    )
    page.wait_for_timeout(500)


def collect_dom_text(page) -> str:
    try:
        return clean_text(page.locator("body").inner_text(timeout=10_000)) or ""
    except Exception:
        return ""


def parse_rendered_html(url: str, html: str, body_text: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    return {
        "url": url,
        "title": extract_title(soup, body_text),
        "price": extract_price(soup, body_text),
        "address": extract_address(soup, body_text),
        "description": extract_description(soup, body_text),
        "seller": extract_seller(soup, body_text),
        "params": extract_params(soup, body_text),
        "image_urls": extract_images(soup, url),
    }


def parse_portalda_card(url: str) -> dict:
    last_error = None

    for _attempt in range(1, RETRIES + 1):
        browser = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )

                context = browser.new_context(
                    viewport={"width": 1440, "height": 1200},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    locale="ru-RU",
                    timezone_id="Europe/Moscow",
                )

                page = context.new_page()
                page.set_default_timeout(DEFAULT_TIMEOUT_MS)

                page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)

                try:
                    page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    pass

                try:
                    page.locator("h1").first.wait_for(timeout=15_000)
                except PlaywrightTimeoutError:
                    pass

                force_gallery_load(page)

                body_text = collect_dom_text(page)
                html = page.content()

                data = parse_rendered_html(url, html, body_text)

                browser.close()

                data["title"] = data["title"] or ""
                data["price"] = data["price"] or 0
                data["address"] = data["address"] or ""
                data["description"] = data["description"] or ""
                data["seller"] = data["seller"] or ""
                data["params"] = data["params"] or {}
                data["image_urls"] = data["image_urls"] or []

                return data

        except Exception as exc:
            last_error = exc
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass

    return {
        "url": url,
        "title": "",
        "price": 0,
        "address": "",
        "description": "",
        "seller": "",
        "params": {},
        "image_urls": [],
        "error": str(last_error) if last_error else "unknown error",
    }
