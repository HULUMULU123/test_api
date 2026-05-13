import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

from app.cian_parser import (
    CianCardParser,
    CianCrawler,
    build_search_url,
    detect_offer_type,
    normalize_city,
    normalize_text,
    parse_price,
)
from app.main import CianParseRequest, parse_cian


class DummyPage:
    pass


class CianCrawlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.crawler = CianCrawler(
            page=DummyPage(),
            city="moskva",
            search_query="квартира",
            price_min=None,
            price_max=None,
            max_pages=1,
            max_cards=10,
        )

    def test_build_search_url_matches_parser_contract(self) -> None:
        self.assertEqual(
            build_search_url("/spb/", "склад", 8_000_000, 15_000_000, page=2),
            "https://www.cian.ru/cat.php?deal_type=sale&engine_version=2"
            "&offer_type=commercial&region=sankt-peterburg&minprice=8000000"
            "&maxprice=15000000&query=%D1%81%D0%BA%D0%BB%D0%B0%D0%B4&p=2",
        )

    def test_offer_type_and_city_helpers(self) -> None:
        self.assertEqual(normalize_city("/SPB/"), "sankt-peterburg")
        self.assertEqual(detect_offer_type("коммерческая база"), "commercial")
        self.assertEqual(detect_offer_type("дом у озера"), "suburban")
        self.assertEqual(detect_offer_type("квартира"), "flat")

    def test_extracts_listing_links_from_provided_selectors(self) -> None:
        html = """
        <div>
            <a href="/sale/flat/123456789/">Квартира</a>
            <a href="https://www.cian.ru/sale/flat/123456789/?utm=test">Duplicate</a>
            <a href="https://example.test/sale/flat/987654321/">External</a>
        </div>
        """

        self.assertEqual(
            self.crawler._extract_listing_urls(html),
            ["https://www.cian.ru/sale/flat/123456789/"],
        )

    def test_normalizes_cian_subdomain_links_like_user_parser(self) -> None:
        self.assertEqual(
            self.crawler._normalize_cian_url(
                "https://khabarovsk.cian.ru/sale/commercial/456789012/?context=abc"
            ),
            "https://www.cian.ru/sale/commercial/456789012/",
        )


class CianCardParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = CianCardParser(page=DummyPage(), save_html=False)

    def test_extracts_listing_fields_from_html(self) -> None:
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <h1 data-name="OfferTitle"> Квартира 50 м² </h1>
                <span data-testid="price-amount">10 500 000 ₽</span>
                <div data-name="Geo">Москва, Тверская улица</div>
                <div data-name="Description">Описание квартиры</div>
                <div data-name="AgentName">Продавец</div>
                <ul>
                  <li>Общая площадь: 50 м²</li>
                  <li>Этаж 3 из 9</li>
                </ul>
                <img src="//example.test/image.jpg">
              </body>
            </html>
            """,
            "lxml",
        )

        self.assertEqual(self.parser._extract_title(soup), "Квартира 50 м²")
        self.assertEqual(self.parser._extract_price(soup), 10_500_000)
        self.assertEqual(self.parser._extract_address(soup), "Москва, Тверская улица")
        self.assertEqual(self.parser._extract_description(soup), "Описание квартиры")
        self.assertEqual(self.parser._extract_seller(soup), "Продавец")
        self.assertEqual(
            self.parser._extract_params(soup),
            {"Общая площадь": "50 м²", "Этаж": "3 из 9"},
        )
        self.assertEqual(self.parser._extract_images(soup), ["https://example.test/image.jpg"])

    def test_extracts_fallbacks_from_json_ld(self) -> None:
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <script type="application/ld+json">
                  {
                    "name": "JSON title",
                    "description": "JSON description",
                    "address": {"addressLocality": "Москва", "streetAddress": "Арбат"},
                    "offers": {"price": "12000000"},
                    "image": ["https://example.test/json.jpg"]
                  }
                </script>
              </body>
            </html>
            """,
            "lxml",
        )

        self.assertEqual(self.parser._extract_title(soup), "JSON title")
        self.assertEqual(self.parser._extract_price(soup), 12_000_000)
        self.assertEqual(self.parser._extract_address(soup), "Москва, Арбат")
        self.assertEqual(self.parser._extract_description(soup), "JSON description")
        self.assertEqual(self.parser._extract_images(soup), ["https://example.test/json.jpg"])

    def test_normalize_text_and_parse_price(self) -> None:
        self.assertEqual(normalize_text("  цена\n объекта  "), "цена объекта")
        self.assertEqual(parse_price("10 500 000 ₽"), 10_500_000)
        self.assertIsNone(parse_price("Цена не указана"))


class CianEndpointTests(unittest.TestCase):
    def test_parse_endpoint_uses_package_relative_parser_import(self) -> None:
        listing = {
            "url": "https://www.cian.ru/sale/flat/123456789/",
            "title": "Квартира",
            "price": 10_500_000,
            "address": "Москва",
            "description": "Описание",
            "seller": "Продавец",
            "params": {},
            "images": [],
            "raw_html_path": None,
            "error": None,
        }

        payload = CianParseRequest(
            city="moskva",
            search_query="квартира",
            max_items=1,
            max_pages=1,
            headless=True,
            save_html=False,
        )

        with patch("app.cian_parser.parse_cian_realty", return_value=[listing]) as parser:
            result = parse_cian(payload)

        self.assertEqual(result, [listing])
        parser.assert_called_once_with(
            price_min=None,
            price_max=None,
            city="moskva",
            search_query="квартира",
            max_items=1,
            max_pages=1,
            headless=True,
            save_html=False,
        )


if __name__ == "__main__":
    unittest.main()
