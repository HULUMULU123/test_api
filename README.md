# Test FastAPI API

Small FastAPI application for testing common request types and parsing PortalDA listing cards:

- `GET /` — simple GET request.
- `GET /search?query=<text>&limit=<number>` — GET request with query parameters.
- `POST /items` — POST request with a JSON body.
- `POST /portalda/parse` — accepts a PortalDA listing URL and returns parsed listing data.
- `POST /avito/parse` — accepts Avito search parameters and returns parsed listing data.
- `POST /cian/parse` — accepts Cian search parameters and returns parsed listing data.
- `POST /cadastral/parse` — accepts a cadastral number and returns parsed NSPD object data with a base64 map screenshot.

## Run locally

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
uvicorn app.main:app --reload
```

Open interactive API docs at <http://127.0.0.1:8000/docs>.

## Example requests

```bash
curl http://127.0.0.1:8000/
```

```bash
curl "http://127.0.0.1:8000/search?query=test&limit=5"
```

```bash
curl -X POST http://127.0.0.1:8000/items \
  -H "Content-Type: application/json" \
  -d '{"name":"demo item","description":"created with POST","price":19.99}'
```


Parse a PortalDA card URL:

```bash
curl -X POST http://127.0.0.1:8000/portalda/parse \
  -H "Content-Type: application/json" \
  -d '{"url":"https://portal-da.ru/object/example"}'
```

The response contains the original URL, title, price, address, description, seller, object parameters, image URLs, and an optional `error` field when parsing fails after retries.

Parse Avito listings by search parameters:

JSON body for testing `POST /avito/parse`:

```json
{
  "city": "moskva",
  "search_query": "склад",
  "price_min": 8000000,
  "price_max": 15000000,
  "max_items": 20,
  "max_pages": 3,
  "category": "nedvizhimost",
  "headless": false,
  "save_html": true
}
```

The API uses the same Playwright flow as the standalone parser: Chromium is launched with the request's `headless` value and only `--disable-blink-features=AutomationControlled`. For server runs with `headless: false`, provide a display yourself, for example by starting the service through `xvfb-run` or another configured `DISPLAY`. Example: `xvfb-run -a uvicorn app.main:app --host 0.0.0.0 --port 8000`.

The same payload is available in `examples/avito_parse_request.json`, so you can test it with curl:

```bash
curl -X POST http://127.0.0.1:8000/avito/parse \
  -H "Content-Type: application/json" \
  --data @examples/avito_parse_request.json
```

The response is an array of listings with URL, title, price, address, description, seller, parsed parameters, image URLs, and optional raw HTML path.


Parse NSPD cadastral object by cadastral number:

JSON body for testing `POST /cadastral/parse`:

```json
{
  "cad_number": "27:09:0000103:1627"
}
```

The parser uses the provided Playwright flow for the NSPD map, searches the cadastral number, collects object attributes, zooms the map out three times, takes a map screenshot, and returns that screenshot in the JSON response as a base64-encoded PNG string in the `screenshot` field. Because the parser launches Chromium with `headless=False`, run the API in an environment with a configured display, for example through `xvfb-run`.

The same payload is available in `examples/cadastral_parse_request.json`, so you can test it with curl:

```bash
curl -X POST http://127.0.0.1:8000/cadastral/parse \
  -H "Content-Type: application/json" \
  --data @examples/cadastral_parse_request.json
```


Parse Cian listings by search parameters:

JSON body for testing `POST /cian/parse`:

```json
{
  "city": "moskva",
  "search_query": "квартира",
  "price_min": 8000000,
  "price_max": 15000000,
  "max_items": 20,
  "max_pages": 3,
  "headless": true,
  "save_html": true
}
```

Cian search URLs are built for `deal_type=sale` and the parser automatically chooses Cian `offer_type` from the query: commercial keywords use `commercial`, house/land keywords use `suburban`, and other queries use `flat`. Raw card HTML is saved into `raw_html_cian` when `save_html` is enabled; blocked or empty search pages are saved into `debug_html_cian`.

The same payload is available in `examples/cian_parse_request.json`, so you can test it with curl:

```bash
curl -X POST http://127.0.0.1:8000/cian/parse \
  -H "Content-Type: application/json" \
  --data @examples/cian_parse_request.json
```

The response is an array of Cian listings with URL, title, price, address, description, seller, parsed parameters, image URLs, and optional raw HTML path/error fields.
