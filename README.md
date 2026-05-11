# Test FastAPI API

Small FastAPI application for testing common request types and parsing PortalDA listing cards:

- `GET /` — simple GET request.
- `GET /search?query=<text>&limit=<number>` — GET request with query parameters.
- `POST /items` — POST request with a JSON body.
- `POST /portalda/parse` — accepts a PortalDA listing URL and returns parsed listing data.
- `POST /avito/parse` — accepts Avito search parameters and returns parsed listing data.

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

For production servers without an X server, keep `headless` set to `true`. Use `headless: false` only on a machine with a display or when running through `xvfb-run`.

The same payload is available in `examples/avito_parse_request.json`, so you can test it with curl:

```bash
curl -X POST http://127.0.0.1:8000/avito/parse \
  -H "Content-Type: application/json" \
  --data @examples/avito_parse_request.json
```

The response is an array of listings with URL, title, price, address, description, seller, parsed parameters, image URLs, and optional raw HTML path. If Avito does not render listing links, the API returns `502` with a `detail` message that includes the last search URL and a possible reason such as a captcha, access restriction, or unexpected page content.
