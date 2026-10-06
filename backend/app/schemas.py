import re
from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

Name = Annotated[str, Field(min_length=1, max_length=80)]


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Inserisci un indirizzo email valido.")
        return value


class RegisterIn(LoginIn):
    name: Name


class ProfileIn(BaseModel):
    name: Name


class CoverModel(BaseModel):
    cover_url: str | None = Field(default=None, max_length=2048)

    @field_validator("cover_url")
    @classmethod
    def validate_cover(cls, value):
        if value:
            if re.fullmatch(r"/api/playlist-covers/[a-f0-9-]{73}\.png", value):
                return value
            if not value.startswith("https://") or not urlparse(value).hostname:
                raise ValueError("La copertina deve avere un indirizzo HTTPS valido.")
        return value or None


class TrackIn(CoverModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    artist: str | None = Field(default=None, min_length=1, max_length=200)


class FavoriteIn(BaseModel):
    favorite: bool


class PlaylistIn(CoverModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class PlaylistUpdate(CoverModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class TracksIn(BaseModel):
    track_ids: list[str] = Field(max_length=2000)


class QueueIn(TracksIn):
    current_track_id: str | None = None


class PlaylistOrderIn(BaseModel):
    playlist_ids: list[str] = Field(max_length=1000)


class ImportIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
