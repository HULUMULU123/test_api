import unittest
from unittest.mock import AsyncMock, patch

from app.main import CadastralParseRequest, parse_cadastral_endpoint


class CadastralEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_endpoint_uses_package_relative_parser_import(self) -> None:
        response = {
            "cad_number": "27:09:0000103:1627",
            "found": True,
            "objects_info": [[{"header": "Кадастровый номер", "value": "27:09:0000103:1627"}]],
            "screenshot": "ZmFrZS1wbmc=",
        }
        payload = CadastralParseRequest(cad_number="27:09:0000103:1627")

        with patch("app.cadastral_parser.parse_cadastral", new=AsyncMock(return_value=response)) as parser:
            result = await parse_cadastral_endpoint(payload)

        self.assertEqual(result, response)
        parser.assert_awaited_once_with("27:09:0000103:1627")


if __name__ == "__main__":
    unittest.main()
