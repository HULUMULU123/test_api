# Test FastAPI API

Small FastAPI application for testing three common request types:

- `GET /` — simple GET request.
- `GET /search?query=<text>&limit=<number>` — GET request with query parameters.
- `POST /items` — POST request with a JSON body.

## Run locally

```bash
python -m pip install -r requirements.txt
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
