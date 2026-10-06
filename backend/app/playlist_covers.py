"""Local playlist artwork uploads, decoded and square-cropped by FFmpeg."""
import json
import re
import subprocess
from uuid import uuid4
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from . import config
from .db import User, get_db
from .security import current_user

router = APIRouter()


@router.post('/api/playlists/{playlist_id}/cover')
def upload_cover(playlist_id: str, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .main import own_playlist, playlist_out
    playlist = own_playlist(db, user, playlist_id)
    content = file.file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, 'La copertina deve essere inferiore a 10 MB.')
    is_image = content.startswith(b'\x89PNG\r\n\x1a\n') or content.startswith(b'\xff\xd8\xff') or (content[:4] == b'RIFF' and content[8:12] == b'WEBP')
    if not is_image:
        raise HTTPException(400, 'Scegli un’immagine JPG, PNG o WebP valida.')
    folder = config.MEDIA_ROOT / 'covers'
    folder.mkdir(parents=True, exist_ok=True)
    stem = f'{playlist.id}-{uuid4()}'
    source, output = folder / f'{stem}.tmp', folder / f'{stem}.png'
    source.write_bytes(content)
    try:
        probe = subprocess.run([config.FFPROBE, '-v', 'error', '-show_streams', '-of', 'json', str(source)], capture_output=True, check=True, timeout=15)
        streams = json.loads(probe.stdout).get('streams', [])
        if len(streams) != 1 or streams[0].get('codec_name') not in {'png', 'mjpeg', 'webp'} or not 0 < streams[0].get('width', 0) * streams[0].get('height', 0) <= 40_000_000:
            raise ValueError('Invalid image dimensions or format')
        subprocess.run([config.FFMPEG, '-nostdin', '-v', 'error', '-y', '-i', str(source), '-frames:v', '1', '-vf', 'scale=512:512:force_original_aspect_ratio=increase,crop=512:512', '-threads', '1', str(output)], capture_output=True, check=True, timeout=30)
        old = playlist.cover_url
        playlist.cover_url = f'/api/playlist-covers/{output.name}'
        db.commit()
        if old and re.fullmatch(r'/api/playlist-covers/[a-f0-9-]{73}\.png', old):
            (folder / old.rsplit('/', 1)[1]).unlink(missing_ok=True)
        return playlist_out(playlist, db)
    except (ValueError, KeyError, subprocess.SubprocessError):
        output.unlink(missing_ok=True)
        raise HTTPException(400, 'Immagine non leggibile. Prova un altro file JPG, PNG o WebP.')
    except FileNotFoundError:
        raise HTTPException(503, 'FFmpeg non disponibile per preparare la copertina.')
    finally:
        source.unlink(missing_ok=True)


@router.get('/api/playlist-covers/{filename}')
def cover(filename: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .main import own_playlist
    if not re.fullmatch(r'[a-f0-9-]{73}\.png', filename):
        raise HTTPException(404, 'Copertina non trovata.')
    own_playlist(db, user, filename[:36])
    path = config.MEDIA_ROOT / 'covers' / filename
    if not path.is_file():
        raise HTTPException(404, 'Copertina non trovata.')
    return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'private, max-age=86400'})
