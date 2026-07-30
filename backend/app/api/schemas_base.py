"""Shared base for every API response model.

The wire format is **camelCase**; Python stays snake_case. Without this the
TypeScript client would have to either mirror Python naming or remap every
field by hand, and hand-remapping is where silent contract drift starts.

``populate_by_name=True`` means handlers can still construct these with Python
field names — ``ArticleOut(**service_dict)`` works with the snake_case dicts
the service layer returns, and only serialisation is camelCased.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        from_attributes=True,
    )
