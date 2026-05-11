# Test FastAPI API

Small FastAPI application for testing common request types and parsing PortalDA listing cards:

- `GET /` — simple GET request.
- `GET /search?query=<text>&limit=<number>` — GET request with query parameters.
- `POST /items` — POST request with a JSON body.
- `POST /portalda/parse` — accepts a PortalDA listing URL and returns parsed listing data.

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
