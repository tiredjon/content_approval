from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Wire format is camelCase (per the assignment's example payloads); internal code
    uses snake_case field names throughout."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)
