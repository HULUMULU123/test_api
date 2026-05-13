import unittest

from bs4 import BeautifulSoup

from app.avito_parser import (
    AvitoCardParser,
    AvitoCrawler,
    build_search_url,
    normalize_text,
    parse_price,
)


class DummyPage:
    pass


class AvitoCrawlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.crawler = AvitoCrawler(
            page=DummyPage(),
            city="moskva",
            search_query="склад",
            price_min=None,
            price_max=None,
            max_pages=1,
            max_cards=10,
        )

    def test_build_search_url_matches_parser_contract(self) -> None:
        self.assertEqual(
            build_search_url("/moskva/", "склад", 8_000_000, 15_000_000, page=2),
            "https://www.avito.ru/moskva/nedvizhimost?"
            "q=%D1%81%D0%BA%D0%BB%D0%B0%D0%B4&pmin=8000000&pmax=15000000&p=2",
        )

    def test_extracts_listing_links_from_provided_selectors(self) -> None:
        html = """
        <div data-marker="item">
            <a data-marker="item-title" href="/moskva/nedvizhimost/sklad_1234567890?context=abc">
                Склад
            </a>
            <a href="/moskva/nedvizhimost/duplicate_1234567890">Duplicate id</a>
        </div>
        """

        self.assertEqual(
            self.crawler._extract_listing_urls(html),
            ["https://www.avito.ru/moskva/nedvizhimost/sklad_1234567890"],
        )

    def test_normalizes_avito_subdomain_links_like_user_parser(self) -> None:
        self.assertEqual(
            self.crawler._normalize_avito_url(
                "https://m.avito.ru/moskva/nedvizhimost/sklad_4567890123?context=abc"
            ),
            "https://www.avito.ru/moskva/nedvizhimost/sklad_4567890123",
        )


class AvitoCardParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = AvitoCardParser(page=DummyPage(), save_html=False)

    def test_extracts_listing_fields_from_html(self) -> None:
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <h1 data-marker="item-view/title-info"> Склад 100 м² </h1>
                <span data-marker="item-view/item-price">10 500 000 ₽</span>
                <div data-marker="item-view/item-address">Москва, Тверская улица</div>
                <div data-marker="item-view/item-description">Описание склада</div>
                <div data-marker="seller-info/name">Продавец</div>
                <ul data-marker="item-view/item-params">
                  <li>Общая площадь: 100 м²</li>
                  <li>Этаж 1 из 3</li>
                </ul>
                <img src="//example.test/image.jpg">
              </body>
            </html>
            """,
            "lxml",
        )

        self.assertEqual(self.parser._extract_title(soup), "Склад 100 м²")
        self.assertEqual(self.parser._extract_price(soup), 10_500_000)
        self.assertEqual(self.parser._extract_address(soup), "Москва, Тверская улица")
        self.assertEqual(self.parser._extract_description(soup), "Описание склада")
        self.assertEqual(self.parser._extract_seller(soup), "Продавец")
        self.assertEqual(
            self.parser._extract_params(soup),
            {"Общая площадь": "100 м²", "Этаж": "1 из 3"},
        )
        self.assertEqual(self.parser._extract_images(soup), ["https://example.test/image.jpg"])

    def test_normalize_text_and_parse_price(self) -> None:
        self.assertEqual(normalize_text("  цена\n объекта  "), "цена объекта")
        self.assertEqual(parse_price("10 500 000 ₽"), 10_500_000)
        self.assertIsNone(parse_price("Цена не указана"))


if __name__ == "__main__":
    unittest.main()
