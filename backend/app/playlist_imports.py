"""Persistent playlist imports; reuse existing tracks and individual audio jobs."""
import json
import shutil
import subprocess
import sys
from datetime import timedelta

from sqlalchemy import func, select, update

from . import config
from .db import ImportBatch, ImportJob, Playlist, PlaylistTrack, SessionLocal, Track, User, now

ACTIVE = ("in_attesa", "elaborazione", "conversione")


def batch_out(batch):
    entries = json.loads(batch.entries_json)
    return {
        **{key: getattr(batch, key) for key in ("id", "url", "title", "status", "progress", "error", "playlist_id", "created_at")},
        "kind": "playlist", "track_id": None, "total": len(entries),
        "completed": sum(bool(e.get("track_id")) for e in entries),
        "failed": sum(bool(e.get("error")) for e in entries),
        "items": [{key: e.get(key) for key in ("title", "status", "error", "track_id")} for e in entries],
    }


def claim_batch():
    with SessionLocal() as db:
        stale = now() - timedelta(seconds=config.IMPORT_TIMEOUT_SECONDS + 60)
        db.execute(update(ImportBatch).where(ImportBatch.status == "elaborazione", ImportBatch.discovered.is_(False), ImportBatch.updated_at < stale).values(status="errore", error="La lettura della playlist si è interrotta. Riprova.", updated_at=now()))
        batch = db.scalar(select(ImportBatch).where(ImportBatch.status == "in_attesa").order_by(ImportBatch.created_at).with_for_update(skip_locked=True).limit(1))
        if batch:
            batch.status = "elaborazione"
            batch.updated_at = now()
        db.commit()
        return batch


def discover_batch(batch):
    directory = config.MEDIA_ROOT / "jobs" / batch.id
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "playlist.json"
    try:
        subprocess.run([sys.executable, "-m", "app.playlist_download", batch.url, str(destination)], capture_output=True, timeout=config.IMPORT_TIMEOUT_SECONDS, check=False)
        if not destination.is_file():
            raise ValueError("Impossibile leggere la playlist. Verifica il link e riprova.")
        result = json.loads(destination.read_text(encoding="utf-8"))
        if result.get("error"):
            raise ValueError(result["error"])
        expand_batch(batch.id, result)
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Lettura della playlist non riuscita o tempo massimo superato. Riprova."
        with SessionLocal() as db:
            db.execute(update(ImportBatch).where(ImportBatch.id == batch.id).values(status="errore", error=message, updated_at=now()))
            db.commit()
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def expand_batch(identifier, metadata):
    with SessionLocal() as db:
        batch = db.scalar(select(ImportBatch).where(ImportBatch.id == identifier).with_for_update())
        if batch.discovered:
            return
        db.execute(select(User).where(User.id == batch.user_id).with_for_update())
        refreshing = bool(batch.playlist_id)
        if refreshing:
            playlist = db.scalar(select(Playlist).where(Playlist.id == batch.playlist_id, Playlist.user_id == batch.user_id).with_for_update())
            if not playlist:
                raise ValueError("La playlist di destinazione è stata eliminata.")
        else:
            position = db.scalar(select(func.max(Playlist.position)).where(Playlist.user_id == batch.user_id))
            playlist = Playlist(user_id=batch.user_id, name=metadata["title"][:120], description="Importata da YouTube", position=(position or 0) + 1)
            db.add(playlist)
            db.flush()
        entries = metadata["entries"]
        if refreshing:
            # Previously imported songs that the user removed must stay removed.
            previous = db.scalars(select(ImportBatch).where(ImportBatch.playlist_id == playlist.id, ImportBatch.id != batch.id, ImportBatch.user_id == batch.user_id)).all()
            seen = {entry.get("video_id") for old in previous for entry in json.loads(old.entries_json) if entry.get("track_id")}
            seen.update(db.scalars(select(Track.youtube_id).join(PlaylistTrack, PlaylistTrack.track_id == Track.id).where(PlaylistTrack.playlist_id == playlist.id)))
            entries = [entry for entry in entries if entry.get("video_id") and entry["video_id"] not in seen]
            last_position = db.scalar(select(func.max(PlaylistTrack.position)).where(PlaylistTrack.playlist_id == playlist.id))
            for index, entry in enumerate(entries):
                entry["destination_position"] = (last_position if last_position is not None else -1) + 1 + index
        for entry in entries:
            if entry.get("error"):
                entry["status"] = "errore"
                continue
            video = entry["video_id"]
            track = db.scalar(select(Track).where(Track.user_id == batch.user_id, Track.youtube_id == video))
            if track:
                entry.update(track_id=track.id, status="completato")
                continue
            job = db.scalar(select(ImportJob).where(ImportJob.user_id == batch.user_id, ImportJob.youtube_id == video, ImportJob.status.in_(ACTIVE)))
            if not job:
                job = ImportJob(user_id=batch.user_id, youtube_id=video, url=f"https://www.youtube.com/watch?v={video}")
                db.add(job)
                db.flush()
            entry.update(job_id=job.id, status=job.status)
        batch.title = metadata["title"][:200]
        batch.playlist_id = playlist.id
        batch.entries_json = json.dumps(entries, ensure_ascii=False)
        batch.discovered = True
        batch.updated_at = now()
        db.commit()
    sync_batches()


def sync_batches():
    with SessionLocal() as db:
        batches = db.scalars(select(ImportBatch).where(ImportBatch.discovered.is_(True), ImportBatch.status.in_(ACTIVE)).with_for_update(skip_locked=True)).all()
        for batch in batches:
            entries = json.loads(batch.entries_json)
            job_ids = [e["job_id"] for e in entries if e.get("job_id")]
            jobs = {j.id: j for j in db.scalars(select(ImportJob).where(ImportJob.id.in_(job_ids)))}
            playlist = db.scalar(select(Playlist).where(Playlist.id == batch.playlist_id).with_for_update())
            if not playlist:
                batch.status, batch.error = "errore", "La playlist di destinazione è stata eliminata. I brani importati restano in libreria."
                continue
            included = set(db.scalars(select(PlaylistTrack.track_id).where(PlaylistTrack.playlist_id == playlist.id)))
            progress = 0
            for index, entry in enumerate(entries):
                job = jobs.get(entry.get("job_id"))
                if job:
                    entry.update(status=job.status, error=job.error, track_id=job.track_id)
                    progress += job.progress if job.status in ACTIVE else 100
                elif entry.get("job_id"):
                    entry.update(status="errore", error="Lavorazione non più disponibile.")
                    progress += 100
                else:
                    progress += 100
                track_id = entry.get("track_id")
                if track_id and track_id not in included and db.get(Track, track_id):
                    db.add(PlaylistTrack(playlist_id=playlist.id, track_id=track_id, position=entry.get("destination_position", index)))
                    included.add(track_id)
            finished = all(e.get("status") in {"completato", "errore"} for e in entries)
            failures = sum(bool(e.get("error")) for e in entries)
            batch.progress = int(progress / len(entries)) if entries else 100
            batch.entries_json = json.dumps(entries, ensure_ascii=False)
            batch.updated_at = now()
            if finished:
                batch.status = "completato" if not entries or any(e.get("track_id") for e in entries) else "errore"
                batch.progress = 100
                batch.error = f"{failures} brani non disponibili. Consulta i dettagli." if failures else None
        db.commit()
