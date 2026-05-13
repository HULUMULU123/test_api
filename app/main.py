from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import AnyHttpUrl, BaseModel, Field, model_validator


app = FastAPI(
    title="Real Estate Parsing API",
    description="FastAPI application for parsing real estate listings.",
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
        description="Avito city slug, for example: moskva, habarovsk",
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

    # В API по умолчанию True. На сервере headless=False часто падает без GUI.
    headless: bool = Field(default=True, description="Run browser without visible UI")
    save_html: bool = Field(default=True, description="Save raw listing HTML into raw_html")

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
    error: str | None = None


class PortaldaParseResponse(BaseModel):
    url: str
    title: str | None = None
    price: int | None = None
    address: str | None = None
    description: str | None = None
    seller: str | None = None
    params: dict[str, str] = Field(default_factory=dict)
    image_urls: list[str] = Field(default_factory=list)
    error: str | None = None


@app.get("/", summary="Health check")
def read_root() -> dict[str, str]:
    return {"message": "API works"}


@app.get("/search", summary="Test GET endpoint with query parameters")
def search_items(
    query: Annotated[str, Query(min_length=1, description="Search text")],
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum number of items")] = 10,
) -> dict[str, str | int]:
    return {
        "message": "GET request with query parameters works",
        "query": query,
        "limit": limit,
    }


@app.post(
    "/items",
    response_model=ItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Test POST endpoint",
)
def create_item(item: ItemCreate) -> ItemResponse:
    return ItemResponse(id=1, **item.model_dump())


@app.post(
    "/portalda/parse",
    response_model=PortaldaParseResponse,
    summary="Parse PortalDA card",
)
def parse_portalda(payload: PortaldaParseRequest) -> dict:
    try:
        # Если portalda_parser.py лежит рядом с app.py
        from portalda_parser import parse_portalda_card
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Файл portalda_parser.py не найден рядом с app.py",
        ) from exc

    try:
        return parse_portalda_card(str(payload.url))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"PortalDA parser failed: {exc}",
        ) from exc


@app.post(
    "/avito/parse",
    response_model=list[AvitoListingResponse],
    summary="Parse Avito listings",
)
def parse_avito(payload: AvitoParseRequest) -> list[dict]:
    # Исправлено: если avito_parser.py лежит рядом с app.py,
    # импорт должен быть без префикса app.
    from avito_parser import parse_avito_realty

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
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
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
