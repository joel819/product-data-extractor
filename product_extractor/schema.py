"""The public output schema. Any field that was not found is null: values are never invented."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

SourceMethod = Literal["jsonld", "opengraph", "microdata", "html", "llm"]
Confidence = Literal["high", "medium", "low"]

# The fields that carry product data, in output order. url is always the requested URL.
DATA_FIELDS: tuple[str, ...] = (
    "name", "brand", "sku", "price", "currency", "availability",
    "description", "image_url", "dimensions", "colour", "finish", "material",
)


class ProductData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    name: str | None = None
    brand: str | None = None
    sku: str | None = None
    price: Decimal | None = Field(default=None, description="Numeric price, serialised as a string such as '89.00'")
    currency: str | None = Field(default=None, description="ISO 4217 code such as EUR")
    availability: str | None = Field(
        default=None,
        description="in_stock, out_of_stock, preorder, backorder, limited, discontinued, or the page's own wording",
    )
    description: str | None = None
    image_url: str | None = None
    dimensions: str | None = Field(default=None, description="As written on the page; units are not converted")
    colour: str | None = None
    finish: str | None = None
    material: str | None = None

    source_method: dict[str, SourceMethod | None] = Field(
        default_factory=lambda: dict.fromkeys(DATA_FIELDS),
        description="Which extraction layer produced each field; null where the field is null",
    )
    confidence: dict[str, Confidence | None] = Field(
        default_factory=lambda: dict.fromkeys(DATA_FIELDS),
        description="high, medium or low per field; null where the field is null",
    )
    warnings: list[str] = Field(default_factory=list)

    @field_serializer("price")
    def _price_as_string(self, v: Decimal | None) -> str | None:
        return None if v is None else format(v, "f")
