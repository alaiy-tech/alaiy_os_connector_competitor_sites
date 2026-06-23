from pydantic import BaseModel
from typing import Optional


class FirecrawlProductSchema(BaseModel):
    product_name: Optional[str] = None
    product_image_url: Optional[str] = None
    product_source_url: Optional[str] = None
    price: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    sku: Optional[str] = None


class FirecrawlPageSchema(BaseModel):
    products: list[FirecrawlProductSchema]


class SingleProductSchema(BaseModel):
    product_name: Optional[str] = None
    product_image_url: Optional[str] = None
    price: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    sku: Optional[str] = None
