"""Schema bộ lọc và kết quả trích xuất của chatbot."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SearchFilters(BaseModel):
    province: str | None = None
    ward_or_area: str | None = None
    price_min: int | None = Field(default=None, ge=0)
    price_max: int | None = Field(default=None, ge=0)
    area_min: float | None = Field(default=None, ge=0)
    area_max: float | None = Field(default=None, ge=0)
    room_type: str | None = None
    amenities: list[str] = Field(default_factory=list)
    occupants: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def check_ranges(self) -> "SearchFilters":
        if (
            self.price_min is not None
            and self.price_max is not None
            and self.price_min > self.price_max
        ):
            raise ValueError("price_min > price_max")
        if (
            self.area_min is not None
            and self.area_max is not None
            and self.area_min > self.area_max
        ):
            raise ValueError("area_min > area_max")
        return self

    def is_empty(self) -> bool:
        return not any(
            [
                self.province,
                self.ward_or_area,
                self.price_min is not None,
                self.price_max is not None,
                self.area_min is not None,
                self.area_max is not None,
                self.room_type,
                self.amenities,
                self.occupants is not None,
            ]
        )

    def as_delta(self) -> dict:
        """Chỉ các trường khác None/rỗng, dùng để gộp vào bộ lọc trước."""
        data = self.model_dump(exclude_none=True)
        data.pop("amenities", None)
        if not self.amenities:
            data.pop("amenities", None)
        return data


class ExtractResult(BaseModel):
    intent: Literal["SEARCH", "FOLLOW_UP", "OUT_OF_SCOPE", "CLARIFY"]
    filters: SearchFilters = Field(default_factory=SearchFilters)
    clarification_question: str | None = None
