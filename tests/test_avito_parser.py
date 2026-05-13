import os
import unittest
from unittest.mock import Mock, patch

from app.avito_parser import AvitoCrawler, headed_browser_display


class DummyPage:
    pass


class AvitoCrawlerUrlExtractionTests(unittest.TestCase):
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

    def test_extracts_links_from_standard_dom_markers(self) -> None:
        html = """
        <div data-marker="item">
            <a data-marker="item-title" href="/moskva/nedvizhimost/sklad_1234567890?context=abc">
                Склад
            </a>
        </div>
        """

        self.assertEqual(
            self.crawler._extract_listing_urls(html),
            ["https://www.avito.ru/moskva/nedvizhimost/sklad_1234567890"],
        )

    def test_extracts_links_from_escaped_json_when_dom_links_are_absent(self) -> None:
        html = r'''
        <script>
        window.__initialData__ = {
            "url":"\/moskva\/kommercheskaya_nedvizhimost\/sklad_2345678901?context=abc",
            "desktopUrl":"https:\/\/m.avito.ru\/moskva\/nedvizhimost\/pomeshchenie_3456789012"
        };
        </script>
        '''

        self.assertEqual(
            self.crawler._extract_listing_urls(html),
            [
                "https://www.avito.ru/moskva/kommercheskaya_nedvizhimost/sklad_2345678901",
                "https://www.avito.ru/moskva/nedvizhimost/pomeshchenie_3456789012",
            ],
        )

    def test_normalizes_mobile_avito_links(self) -> None:
        self.assertEqual(
            self.crawler._normalize_avito_url(
                "https://m.avito.ru/moskva/nedvizhimost/sklad_4567890123"
            ),
            "https://www.avito.ru/moskva/nedvizhimost/sklad_4567890123",
        )


class HeadedBrowserDisplayTests(unittest.TestCase):
    def test_raises_in_headed_mode_without_display_or_xvfb(self) -> None:
        with (
            patch.object(os, "name", "posix"),
            patch.dict(os.environ, {}, clear=True),
            patch("app.avito_parser.shutil.which", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "Xvfb"):
                with headed_browser_display(headless=False):
                    pass

    def test_starts_xvfb_in_headed_mode_without_display(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = None

        with (
            patch.object(os, "name", "posix"),
            patch.dict(os.environ, {}, clear=True),
            patch("app.avito_parser.shutil.which", return_value="/usr/bin/Xvfb"),
            patch("app.avito_parser._find_free_display_number", return_value=99),
            patch("app.avito_parser.subprocess.Popen", return_value=process) as popen,
            patch("app.avito_parser.time.sleep"),
        ):
            with headed_browser_display(headless=False):
                self.assertEqual(os.environ["DISPLAY"], ":99")

        popen.assert_called_once()
        process.terminate.assert_called_once()
        self.assertNotIn("DISPLAY", os.environ)


if __name__ == "__main__":
    unittest.main()
