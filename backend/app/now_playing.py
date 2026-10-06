"""Ephemeral player heartbeat for the local streaming overlay."""
import time
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from .db import Track, User, get_db
from .security import current_user
from .media import youtube_cover

router = APIRouter()
states = {}


class PlaybackIn(BaseModel):
    track_id: str | None = Field(default=None, max_length=36)
    position: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)
    playing: bool = False
    volume: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)


@router.post('/api/now-playing')
def publish(data: PlaybackIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from fastapi import HTTPException
    track = db.get(Track, data.track_id) if data.track_id else None
    if data.track_id and (not track or track.user_id != user.id):
        raise HTTPException(404, 'Brano non trovato.')
    states[user.id] = dict(track_id=data.track_id, position=data.position, playing=data.playing, volume=data.volume, updated=time.monotonic())
    return {'ok': True}


@router.get('/api/now-playing')
def read(user: User = Depends(current_user), db: Session = Depends(get_db)):
    state = states.get(user.id)
    idle = dict(track_id=None, playing=False, volume=0, title='Kass', artist='Nessun brano in riproduzione', image='/kass.svg', progress_ms=0, duration_ms=0)
    if not state or time.monotonic()-state['updated'] > 8:
        return idle
    track = db.get(Track, state['track_id']) if state['track_id'] else None
    if not track or track.user_id != user.id:
        return idle
    position = state['position'] + (time.monotonic()-state['updated'] if state['playing'] else 0)
    return dict(track_id=track.id, playing=state['playing'], volume=state.get('volume',1), title=track.title, artist=track.artist,
                image=track.cover_url or youtube_cover(track.youtube_id) or '/kass.svg', progress_ms=round(min(position, track.duration)*1000), duration_ms=round(track.duration*1000))
