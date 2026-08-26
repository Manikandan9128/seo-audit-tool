import uuid
from datetime import datetime

from pydantic import BaseModel


class ClientCreate(BaseModel):
    name: str
    website_url: str


class ClientOut(BaseModel):
    id: uuid.UUID
    name: str
    website_url: str
    ga4_property_id: str | None
    gsc_site_url: str | None
    google_connected: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class GA4PropertyOut(BaseModel):
    name: str
    display_name: str


class GSCSiteOut(BaseModel):
    site_url: str
    permission_level: str


class ClientSelectProperties(BaseModel):
    ga4_property_id: str | None = None
    gsc_site_url: str | None = None
