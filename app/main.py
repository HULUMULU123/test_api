from typing import Annotated

from fastapi import FastAPI, Query, status
from pydantic import BaseModel, Field


app = FastAPI(
    title="Test Methods API",
    description="A small FastAPI application for testing GET, GET with query parameters, and POST requests.",
    version="0.1.0",
)


class ItemCreate(BaseModel):
    name: str = Field(..., min_length=1, examples=["demo item"])
    description: str | None = Field(default=None, examples=["Item created by POST request"])
    price: float = Field(..., gt=0, examples=[19.99])


class ItemResponse(ItemCreate):
    id: int


@app.get("/", summary="Simple GET endpoint")
def read_root() -> dict[str, str]:
    """Return a basic response for testing a GET request."""
    return {"message": "GET request works"}


@app.get("/search", summary="GET endpoint with query parameters")
def search_items(
    query: Annotated[str, Query(min_length=1, description="Search text")],
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum number of items")] = 10,
) -> dict[str, str | int]:
    """Echo query parameters for testing GET requests with a query string."""
    return {
        "message": "GET request with query parameters works",
        "query": query,
        "limit": limit,
    }


@app.post("/items", response_model=ItemResponse, status_code=status.HTTP_201_CREATED, summary="POST endpoint")
def create_item(item: ItemCreate) -> ItemResponse:
    """Return the posted payload with a generated test ID."""
    return ItemResponse(id=1, **item.model_dump())
