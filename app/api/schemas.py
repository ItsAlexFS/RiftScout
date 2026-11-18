from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Role = Literal["TOP", "JUNGLE", "MID", "ADC", "SUPPORT", "FILL"]


class PlayerCreate(BaseModel):
    gameName: str = Field(..., min_length=3, max_length=16, examples=["Kukulcan"])
    tagLine: str = Field(..., min_length=2, max_length=5, examples=["MYB"])
    role: Role = "FILL"
    notes: str = Field("", max_length=500)


class PlayerUpdate(BaseModel):
    role: Optional[Role] = None
    notes: Optional[str] = Field(None, max_length=500)
