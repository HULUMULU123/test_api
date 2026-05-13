from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import AnyHttpUrl, BaseModel, Field, model_validator


app = FastAPI(
    title="Test Methods API",
    description="A small FastAPI application with test endpoints and real estate parsing endpoints.",
    version="0.1.0",
)


class ItemCreate(BaseModel):
    name: str = Field(..., min_length=1, examples=["demo item"])
    description: str | None = Field(default=None, examples=["Item created by POST request"])
    price: float = Field(..., gt=0, examples=[19.99])


class ItemResponse(ItemCreate):
    id: int


class PortaldaParseRequest(BaseModel):
    url: AnyHttpUrl = Field(..., examples=["https://portal-da.ru/object/example"])


class AvitoParseRequest(BaseModel):
    city: str = Field(
        ...,
        min_length=1,
        examples=["moskva"],
        description="Avito city slug, for example moskva or habarovsk",
    )
    search_query: str = Field(
        ...,
        min_length=1,
        examples=["склад"],
        description="Search phrase for Avito",
    )
    price_min: int | None = Field(
        default=None,
        ge=0,
        examples=[8000000],
        description="Minimum price filter",
    )
    price_max: int | None = Field(
        default=None,
        ge=0,
        examples=[15000000],
        description="Maximum price filter",
    )
    max_items: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of listing cards to parse",
    )
    max_pages: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Maximum number of Avito search pages to scan",
    )
    category: str = Field(
        default="nedvizhimost",
        min_length=1,
        examples=["nedvizhimost"],
        description="Avito category slug",
    )
    headless: bool = Field(default=False, description="Run browser without a visible UI")
    save_html: bool = Field(
        default=True,
        description="Save raw listing HTML into raw_html",
    )

    @model_validator(mode="after")
    def validate_price_range(self) -> "AvitoParseRequest":
        if (
            self.price_min is not None
            and self.price_max is not None
            and self.price_min > self.price_max
        ):
            raise ValueError("price_min не может быть больше price_max")

        return self


class AvitoListingResponse(BaseModel):
    url: str
    title: str | None = None
    price: int | None = None
    address: str | None = None
    description: str | None = None
    seller: str | None = None
    params: dict[str, str] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list)
    raw_html_path: str | None = None


class PortaldaParseResponse(BaseModel):
    url: str
    title: str
    price: int
    address: str
    description: str
    seller: str
    params: dict[str, str]
    image_urls: list[str]
    error: str | None = None


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


@app.post(
    "/items",
    response_model=ItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="POST endpoint",
)
def create_item(item: ItemCreate) -> ItemResponse:
    """Return the posted payload with a generated test ID."""
    return ItemResponse(id=1, **item.model_dump())


@app.post(
    "/portalda/parse",
    response_model=PortaldaParseResponse,
    summary="Parse PortalDA card",
)
def parse_portalda(payload: PortaldaParseRequest) -> dict:
    """Parse a PortalDA card URL and return extracted listing data."""
    from app.portalda_parser import parse_portalda_card

    return parse_portalda_card(str(payload.url))


@app.post(
    "/avito/parse",
    response_model=list[AvitoListingResponse],
    summary="Parse Avito listings",
)
def parse_avito(payload: AvitoParseRequest) -> list[dict]:
    """Parse Avito search results by city, query, price range, category, and limits."""
    from app.avito_parser import parse_avito_realty

    try:
        return parse_avito_realty(
            price_min=payload.price_min,
            price_max=payload.price_max,
            city=payload.city,
            search_query=payload.search_query,
            max_items=payload.max_items,
            max_pages=payload.max_pages,
            category=payload.category,
            headless=payload.headless,
            save_html=payload.save_html,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Avito parser failed: {exc}",
        ) from exc
