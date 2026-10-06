import asyncio
import hashlib
import ipaddress
import json
import logging
import re
import subprocess
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from . import config
from .db import Base, ImportBatch, ImportJob, Listen, LoginSession, Playlist, PlaylistTrack, Queue, SessionLocal, Track, User, engine, get_db, initialize_database, now, uid
from .media import probe_mp3, sha256_file, youtube_id, youtube_playlist_id, youtube_cover
from .normalization import normalized_audio
from .now_playing import router as now_playing_router
from .frontend import mount_frontend
from .playlist_imports import ACTIVE, batch_out
from .schemas import FavoriteIn, ImportIn, LoginIn, PlaylistIn, PlaylistOrderIn, PlaylistUpdate, ProfileIn, QueueIn, RegisterIn, TrackIn, TracksIn
from .security import create_session, current_user, hash_password, local_storage_owner, token_hash, verify_password


logger = logging.getLogger("cass")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    with engine.begin() as conn:
        # API and worker containers can start concurrently; serialize first schema creation.
        if engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(14817205)"))
        initialize_database(conn)
    if config.LOCAL_DESKTOP_MODE:
        with SessionLocal() as db:
            local_storage_owner(db)
    yield


app = FastAPI(title="Kass API", version="1.0.0", lifespan=lifespan)
app.include_router(now_playing_router)
app.add_middleware(CORSMiddleware, allow_origins=list(config.ALLOWED_ORIGINS), allow_credentials=True, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type"])


@app.middleware("http")
async def protect_requests(request: Request, call_next):
    if config.LOCAL_DESKTOP_MODE and (request.url.path == "/api" or request.url.path.startswith("/api/")):
        # The native app has no user login: only its loopback webview may reach
        # the API. Validate the Host too, to prevent DNS rebinding to localhost.
        allowed_hosts = {
            parsed.netloc.lower()
            for origin in config.ALLOWED_ORIGINS
            if (parsed := urlsplit(origin)).hostname in {"localhost", "127.0.0.1", "::1"}
        }
        try:
            local_client = bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
        except ValueError:
            local_client = False
        origin = request.headers.get("origin")
        if (
            not local_client
            or request.headers.get("host", "").lower() not in allowed_hosts
            or (origin is not None and origin.rstrip("/") not in config.ALLOWED_ORIGINS)
            or request.headers.get("sec-fetch-site", "").lower() == "cross-site"
        ):
            return JSONResponse(status_code=403, content={"detail": "Kass è un’app locale: questa richiesta non proviene da una finestra autorizzata sul tuo PC."})
        if request.url.path in {"/api/auth/register", "/api/auth/login", "/api/auth/logout"} or (request.url.path == "/api/auth/me" and request.method != "GET"):
            return JSONResponse(status_code=403, content={"detail": "Kass sul tuo PC non usa account, password o sessioni. La libreria viene salvata direttamente in locale."})
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if (origin and origin.rstrip("/") not in config.ALLOWED_ORIGINS) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse(status_code=403, content={"detail": "Richiesta da un’origine non autorizzata."})
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > (config.MAX_UPLOAD_MB + 1) * 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": f"Il file supera il limite di {config.MAX_UPLOAD_MB} MB."})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    fields = [".".join(str(x) for x in err["loc"][1:]) for err in exc.errors()]
    messages = {"email": "Inserisci un indirizzo email valido.", "password": "La password deve contenere da 8 a 256 caratteri.", "name": "Inserisci un nome valido.", "cover_url": "La copertina deve avere un indirizzo HTTPS valido.", "title": "Inserisci un titolo da 1 a 200 caratteri.", "artist": "Inserisci un artista da 1 a 200 caratteri.", "file": "Seleziona un file MP3."}
    return JSONResponse(status_code=422, content={"detail": " ".join(dict.fromkeys(messages.get(field, "Controlla i campi indicati e riprova.") for field in fields)), "fields": fields})


@app.exception_handler(SQLAlchemyError)
async def database_error(request, exc):
    logger.exception("Database operation failed")
    return JSONResponse(status_code=503, content={"detail": "Il database non è disponibile. Riprova tra poco."})


def user_out(user):
    return {"id": user.id, "name": user.name, "email": user.email}


def track_out(track):
    return {**{k: getattr(track, k) for k in ("id", "title", "artist", "duration", "cover_url", "favorite", "source", "is_demo", "created_at")}, "cover_url": (youtube_cover(track.youtube_id) if track.youtube_id and track.cover_url in {None, f"https://i.ytimg.com/vi/{track.youtube_id}/hqdefault.jpg"} else track.cover_url) or youtube_cover(track.youtube_id)}


def playlist_out(playlist, db):
    source = db.scalar(select(ImportBatch).where(ImportBatch.playlist_id == playlist.id, ImportBatch.user_id == playlist.user_id).order_by(ImportBatch.created_at.desc()).limit(1))
    return {**{k: getattr(playlist, k) for k in ("id", "name", "description", "cover_url", "created_at")}, "track_ids": list(db.scalars(select(PlaylistTrack.track_id).where(PlaylistTrack.playlist_id == playlist.id).order_by(PlaylistTrack.position))), "youtube_url": source.url if source else None, "youtube_check_status": source.status if source else None}


def job_out(job):
    return {k: getattr(job, k) for k in ("id", "url", "status", "progress", "error", "track_id", "created_at")}


def own_track(db, user, identifier):
    item = db.scalar(select(Track).where(Track.id == identifier, Track.user_id == user.id))
    if item is None:
        raise HTTPException(404, "Brano non trovato nella tua libreria.")
    return item


def own_playlist(db, user, identifier):
    item = db.scalar(select(Playlist).where(Playlist.id == identifier, Playlist.user_id == user.id))
    if item is None:
        raise HTTPException(404, "Playlist non trovata.")
    return item


def validate_track_ids(db, user, ids):
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "La lista contiene brani duplicati.")
    count = db.scalar(select(func.count()).select_from(Track).where(Track.user_id == user.id, Track.id.in_(ids)))
    if count != len(ids):
        raise HTTPException(400, "Uno o più brani non appartengono alla tua libreria.")


attempts = defaultdict(deque)


def throttle_auth(request):
    key = request.client.host if request.client else "unknown"
    current = time.monotonic()
    bucket = attempts[key]
    while bucket and bucket[0] < current - 60:
        bucket.popleft()
    if len(bucket) >= 20:
        raise HTTPException(429, "Troppi tentativi. Attendi un minuto e riprova.")
    bucket.append(current)


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": engine.dialect.name, "instance_id": config.INSTANCE_ID, "local_desktop": config.LOCAL_DESKTOP_MODE}


@app.get("/api/config")
def public_config(db: Session = Depends(get_db)):
    registration_open = not config.LOCAL_DESKTOP_MODE and (not config.PERSONAL_MODE or not db.scalar(select(User.id).limit(1)))
    return {
        "youtube_import_enabled": True,
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "personal_mode": config.PERSONAL_MODE or config.LOCAL_DESKTOP_MODE,
        "local_desktop": config.LOCAL_DESKTOP_MODE,
        "registration_open": registration_open,
        "storage_path": str(config.MEDIA_ROOT.parent.resolve()) if config.LOCAL_DESKTOP_MODE else None,
        "media_path": str(config.MEDIA_ROOT.resolve()) if config.LOCAL_DESKTOP_MODE else None,
    }


@app.post("/api/auth/register", status_code=201)
def register(data: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    throttle_auth(request)
    if config.PERSONAL_MODE:
        # Two first-launch requests must not create two accounts on PostgreSQL.
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(14817206)"))
        if db.scalar(select(User.id).limit(1)):
            raise HTTPException(403, "Il profilo personale di Kass è già configurato. Accedi con il tuo account.")
    if not data.name.strip():
        raise HTTPException(422, "Inserisci il tuo nome.")
    user = User(name=data.name.strip(), email=data.email, password_hash=hash_password(data.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Esiste già un account con questa email.")
    create_session(db, user, response)
    return user_out(user)


@app.post("/api/auth/login")
def login(data: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    throttle_auth(request)
    user = db.scalar(select(User).where(User.email == data.email))
    valid = verify_password(data.password, user.password_hash) if user else verify_password(data.password, hash_password("invalid-placeholder-password"))
    if not user or not valid:
        raise HTTPException(401, "Email o password non corretti.")
    create_session(db, user, response)
    return user_out(user)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get("cass_session")
    if token:
        db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(token)))
        db.commit()
    response.delete_cookie("cass_session", path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


@app.patch("/api/auth/me")
def edit_me(data: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not data.name.strip():
        raise HTTPException(422, "Inserisci il tuo nome.")
    user.name = data.name.strip()
    db.commit()
    return user_out(user)


@app.get("/api/tracks")
def tracks(q: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    statement = select(Track).where(Track.user_id == user.id)
    if q.strip():
        query = "%" + q[:200].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        statement = statement.where(or_(Track.title.ilike(query, escape="\\"), Track.artist.ilike(query, escape="\\")))
    return [track_out(t) for t in db.scalars(statement.order_by(Track.created_at.desc()))]


@app.post("/api/tracks/upload", status_code=201)
async def upload(file: UploadFile = File(...), title: str | None = Form(None), artist: str | None = Form(None), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.lower().endswith(".mp3"):
        raise HTTPException(400, "Scegli un file con estensione .mp3.")
    title = title.strip() if title and title.strip() else None
    artist = artist.strip() if artist and artist.strip() else None
    for value in (title, artist):
        if value is not None and len(value) > 200:
            raise HTTPException(422, "Titolo e artista devono contenere da 1 a 200 caratteri.")
    identifier = uid()
    temporary = config.MEDIA_ROOT / f"{identifier}.upload"
    final = config.MEDIA_ROOT / f"{identifier}.mp3"
    size = 0
    committed = False
    try:
        with temporary.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > config.MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(413, f"Il file supera il limite di {config.MAX_UPLOAD_MB} MB.")
                destination.write(chunk)
        metadata = await asyncio.to_thread(probe_mp3, temporary)
        digest = await asyncio.to_thread(sha256_file, temporary)
        duplicate = db.scalar(select(Track).where(Track.user_id == user.id, Track.sha256 == digest))
        if duplicate:
            raise HTTPException(409, "Questo MP3 è già presente nella tua libreria.")
        tags = metadata["tags"]
        track = Track(id=identifier, user_id=user.id, title=(title or tags.get("title") or Path(file.filename).stem)[:200].strip() or "Senza titolo", artist=(artist or tags.get("artist") or "Artista sconosciuto")[:200], duration=metadata["duration"], filename=final.name, sha256=digest)
        temporary.replace(final)
        db.add(track)
        try:
            db.commit()
            committed = True
        except IntegrityError:
            db.rollback()
            final.unlink(missing_ok=True)
            raise HTTPException(409, "Questo MP3 è già presente nella tua libreria.")
        return track_out(track)
    finally:
        temporary.unlink(missing_ok=True)
        if not committed:
            final.unlink(missing_ok=True)
        await file.close()


@app.patch("/api/tracks/{track_id}")
def edit_track(track_id: str, data: TrackIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    track = own_track(db, user, track_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if key in {"title", "artist"}:
            if value is None or not value.strip():
                raise HTTPException(422, "Titolo e artista non possono essere vuoti.")
            value = value.strip()
        setattr(track, key, value)
    db.commit()
    return track_out(track)


@app.delete("/api/tracks/{track_id}")
def remove_track(track_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    track = own_track(db, user, track_id)
    filename = track.filename
    db.execute(delete(PlaylistTrack).where(PlaylistTrack.track_id == track_id))
    db.execute(delete(Listen).where(Listen.track_id == track_id))
    queue = db.get(Queue, user.id)
    if queue:
        queue.track_ids_json = json.dumps([t for t in json.loads(queue.track_ids_json) if t != track_id])
        if queue.current_track_id == track_id:
            queue.current_track_id = None
    db.delete(track)
    db.commit()
    (config.MEDIA_ROOT / filename).unlink(missing_ok=True)
    return {"ok": True}


@app.put("/api/tracks/{track_id}/favorite")
def favorite(track_id: str, data: FavoriteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    track = own_track(db, user, track_id)
    track.favorite = data.favorite
    db.commit()
    return track_out(track)


@app.get("/api/tracks/{track_id}/audio")
def audio(track_id: str, normalize: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    track = own_track(db, user, track_id)
    path = config.MEDIA_ROOT / track.filename
    if not path.is_file():
        raise HTTPException(404, "Il file audio non è disponibile. Ricarica il tuo MP3.")
    if normalize:
        try:
            path = normalized_audio(path, track.sha256)
        except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as exc:
            logger.exception("Audio normalization failed for track %s", track_id)
            raise HTTPException(503, "Non è stato possibile uniformare il volume del brano. Verifica FFmpeg e riprova.") from exc
    return FileResponse(path, media_type="audio/mpeg", content_disposition_type="inline", headers={"Cache-Control": "private, no-store"})


@app.post("/api/tracks/{track_id}/listen")
def listen(track_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    own_track(db, user, track_id)
    db.add(Listen(user_id=user.id, track_id=track_id))
    db.commit()
    return {"ok": True}


@app.get("/api/history")
def history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    recent = select(Listen.track_id, func.max(Listen.listened_at).label("last_played")).where(Listen.user_id == user.id).group_by(Listen.track_id).subquery()
    return [track_out(t) for t in db.scalars(select(Track).join(recent, Track.id == recent.c.track_id).where(Track.user_id == user.id).order_by(recent.c.last_played.desc()).limit(100))]


@app.get("/api/playlists")
def playlists(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [playlist_out(p, db) for p in db.scalars(select(Playlist).where(Playlist.user_id == user.id).order_by(Playlist.position, Playlist.created_at))]


@app.post("/api/playlists", status_code=201)
def create_playlist(data: PlaylistIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not data.name.strip():
        raise HTTPException(422, "Inserisci un nome per la playlist.")
    count = db.scalar(select(func.count()).select_from(Playlist).where(Playlist.user_id == user.id))
    if count >= 1000:
        raise HTTPException(400, "Hai raggiunto il limite di 1000 playlist.")
    playlist = Playlist(user_id=user.id, name=data.name.strip(), description=data.description, cover_url=data.cover_url, position=count)
    db.add(playlist)
    db.commit()
    return playlist_out(playlist, db)


@app.put("/api/playlists/order")
def reorder_playlists(data: PlaylistOrderIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    items = list(db.scalars(select(Playlist).where(Playlist.user_id == user.id)))
    if len(set(data.playlist_ids)) != len(data.playlist_ids) or set(data.playlist_ids) != {p.id for p in items}:
        raise HTTPException(400, "L’ordine deve includere tutte le tue playlist una sola volta.")
    for playlist in items:
        playlist.position = data.playlist_ids.index(playlist.id)
    db.commit()
    return {"ok": True}


@app.patch("/api/playlists/{playlist_id}")
def edit_playlist(playlist_id: str, data: PlaylistUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    playlist = own_playlist(db, user, playlist_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if key == "name":
            if value is None or not value.strip():
                raise HTTPException(422, "Inserisci un nome per la playlist.")
            value = value.strip()
        if key == "description" and value is None:
            value = ""
        setattr(playlist, key, value)
    db.commit()
    return playlist_out(playlist, db)


@app.delete("/api/playlists/{playlist_id}")
def delete_playlist(playlist_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    playlist = own_playlist(db, user, playlist_id)
    db.execute(delete(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id))
    db.delete(playlist)
    db.commit()
    return {"ok": True}


@app.put("/api/playlists/{playlist_id}/tracks")
def playlist_tracks(playlist_id: str, data: TracksIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    playlist = own_playlist(db, user, playlist_id)
    validate_track_ids(db, user, data.track_ids)
    db.execute(delete(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id))
    for position, track_id in enumerate(data.track_ids):
        db.add(PlaylistTrack(playlist_id=playlist_id, track_id=track_id, position=position))
    db.commit()
    return playlist_out(playlist, db)


@app.get("/api/queue")
def get_queue(user: User = Depends(current_user), db: Session = Depends(get_db)):
    queue = db.get(Queue, user.id)
    return {"track_ids": json.loads(queue.track_ids_json) if queue else [], "current_track_id": queue.current_track_id if queue else None}


@app.put("/api/queue")
def set_queue(data: QueueIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    validate_track_ids(db, user, data.track_ids)
    if data.current_track_id and data.current_track_id not in data.track_ids:
        raise HTTPException(400, "Il brano corrente deve appartenere alla coda.")
    queue = db.get(Queue, user.id) or Queue(user_id=user.id)
    queue.track_ids_json = json.dumps(data.track_ids)
    queue.current_track_id = data.current_track_id
    db.add(queue)
    db.commit()
    return data.model_dump()


@app.get("/api/imports")
def imports(user: User = Depends(current_user), db: Session = Depends(get_db)):
    batches = db.scalars(select(ImportBatch).where(ImportBatch.user_id == user.id).order_by(ImportBatch.created_at.desc())).all()
    child_ids = {entry["job_id"] for batch in batches for entry in json.loads(batch.entries_json) if entry.get("job_id")}
    jobs = db.scalars(select(ImportJob).where(ImportJob.user_id == user.id, ImportJob.id.not_in(child_ids)).order_by(ImportJob.created_at.desc()).limit(100)).all()
    return sorted([batch_out(b) for b in batches[:100]] + [job_out(j) for j in jobs], key=lambda item: item["created_at"], reverse=True)[:100]


@app.post("/api/imports", status_code=202)
def create_import(data: ImportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    playlist_source = youtube_playlist_id(data.url)
    if playlist_source:
        db.execute(select(User).where(User.id == user.id).with_for_update())
        existing = db.scalar(select(ImportBatch).where(ImportBatch.user_id == user.id, ImportBatch.youtube_playlist_id == playlist_source, ImportBatch.status.in_(ACTIVE)))
        if existing:
            return batch_out(existing)
        if db.scalar(select(func.count()).select_from(ImportBatch).where(ImportBatch.user_id == user.id, ImportBatch.status.in_(ACTIVE))) >= 5:
            raise HTTPException(429, "Hai già 5 playlist in importazione. Attendi il completamento.")
        batch = ImportBatch(user_id=user.id, url=f"https://www.youtube.com/playlist?list={playlist_source}", youtube_playlist_id=playlist_source)
        db.add(batch)
        db.commit()
        return batch_out(batch)
    identifier = youtube_id(data.url)
    # Lock the account so simultaneous requests cannot create duplicate active jobs.
    db.execute(select(User).where(User.id == user.id).with_for_update())
    if db.scalar(select(Track.id).where(Track.user_id == user.id, Track.youtube_id == identifier)):
        raise HTTPException(409, "Questo contenuto è già nella tua libreria.")
    active = db.scalar(select(ImportJob).where(ImportJob.user_id == user.id, ImportJob.youtube_id == identifier, ImportJob.status.in_(["in_attesa", "elaborazione", "conversione"])))
    if active:
        return job_out(active)
    pending_count = db.scalar(select(func.count()).select_from(ImportJob).where(ImportJob.user_id == user.id, ImportJob.status.in_(["in_attesa", "elaborazione", "conversione"])))
    if pending_count >= 5:
        raise HTTPException(429, "Hai già 5 importazioni in corso. Attendi il completamento.")
    job = ImportJob(user_id=user.id, url=f"https://www.youtube.com/watch?v={identifier}", youtube_id=identifier)
    db.add(job)
    db.commit()
    return job_out(job)


@app.post("/api/playlists/{playlist_id}/check-youtube", status_code=202)
def check_youtube_playlist(playlist_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    own_playlist(db, user, playlist_id)
    db.execute(select(User).where(User.id == user.id).with_for_update())
    source = db.scalar(select(ImportBatch).where(ImportBatch.playlist_id == playlist_id, ImportBatch.user_id == user.id).order_by(ImportBatch.created_at.desc()).limit(1))
    if not source:
        raise HTTPException(400, "Questa playlist non è stata importata da YouTube.")
    active = db.scalar(select(ImportBatch).where(ImportBatch.playlist_id == playlist_id, ImportBatch.user_id == user.id, ImportBatch.status.in_(ACTIVE)))
    if active:
        return batch_out(active)
    if db.scalar(select(func.count()).select_from(ImportBatch).where(ImportBatch.user_id == user.id, ImportBatch.status.in_(ACTIVE))) >= 5:
        raise HTTPException(429, "Hai già 5 playlist in lavorazione. Attendi il completamento.")
    batch = ImportBatch(user_id=user.id, url=source.url, youtube_playlist_id=source.youtube_playlist_id, title=source.title, playlist_id=playlist_id)
    db.add(batch)
    db.commit()
    return batch_out(batch)


@app.post("/api/demo")
def seed_demo(user: User = Depends(current_user), db: Session = Depends(get_db)):
    # Explicit opt-in only. These are original generated sounds, never commercial music.
    db.execute(select(User).where(User.id == user.id).with_for_update())
    existing = list(db.scalars(select(Track).where(Track.user_id == user.id, Track.is_demo.is_(True)).order_by(Track.created_at)))
    if existing:
        return {"tracks": [track_out(t) for t in existing], "message": "Gli audio dimostrativi sono già presenti."}
    soundscapes = [("Dopo mezzanotte", 174, 261, "#6843a8"), ("Orbita lenta", 196, 294, "#176d73"), ("Luce morbida", 220, 330, "#97552e"), ("Notti di vetro", 146.83, 220, "#594879"), ("Marea", 130.81, 196, "#247a74"), ("Frequenze gentili", 164.81, 247, "#7c683f")]
    created = []
    paths = []
    try:
        for index, (title, low, high, _) in enumerate(soundscapes):
            identifier = uid()
            path = config.MEDIA_ROOT / f"{identifier}.mp3"
            paths.append(path)
            # Slow beating pads: no sample recordings, copyrighted compositions or downloads.
            expression = f"0.16*sin(2*PI*{low}*t)*(0.7+0.3*sin(2*PI*0.15*t))+0.07*sin(2*PI*{high}*t)+0.03*sin(2*PI*{low*2}*t)"
            subprocess.run([config.FFMPEG, "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", f"aevalsrc={expression}:s=44100:d=24", "-af", "afade=t=in:d=2,afade=t=out:st=21:d=3", "-ac", "2", "-codec:a", "libmp3lame", "-b:a", "128k", "-metadata", f"title={title}", "-metadata", "artist=Kass Studio · audio demo", str(path)], check=True, capture_output=True, timeout=30)
            track = Track(id=identifier, user_id=user.id, title=title, artist="Kass Studio · audio demo", duration=probe_mp3(path)["duration"], filename=path.name, sha256=sha256_file(path), source="demo", is_demo=True, favorite=index in (0, 2))
            db.add(track)
            created.append(track)
        db.flush()
        for name, description, indices in [("Di notte", "Paesaggi sonori originali · raccolta dimostrativa", [0, 3, 1]), ("Concentrazione", "Frequenze lente per il tuo spazio · raccolta dimostrativa", [2, 5, 4]), ("Fuori dal tempo", "Una piccola pausa · raccolta dimostrativa", [4, 1, 2])]:
            playlist = Playlist(user_id=user.id, name=name, description=description, position=len(created))
            db.add(playlist)
            db.flush()
            for position, index in enumerate(indices):
                db.add(PlaylistTrack(playlist_id=playlist.id, track_id=created[index].id, position=position))
        db.commit()
    except (subprocess.SubprocessError, FileNotFoundError):
        db.rollback()
        for path in paths:
            path.unlink(missing_ok=True)
        raise HTTPException(503, "Non è stato possibile creare gli audio demo: verifica FFmpeg sul server.")
    return {"tracks": [track_out(t) for t in created], "message": "Aggiunti 6 audio originali dimostrativi di 24 secondi, generati con sintesi sonora."}


# Registered last so API routes always take precedence over the desktop bundle.
mount_frontend(app, config.FRONTEND_DIST)
