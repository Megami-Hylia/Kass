"""Durable PostgreSQL job queue: independent process, row locks and stale-job recovery."""
import json
import logging
import shutil
import subprocess
import sys
import time
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import or_, select, text, update

from . import config
from .db import Base, ImportJob, SessionLocal, Track, engine, initialize_database, now, uid
from .media import probe_audio_source, probe_mp3, sha256_file
from .playlist_imports import claim_batch, discover_batch, sync_batches

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cass.worker")


def update_job(identifier: str, **values):
    with SessionLocal() as db:
        db.execute(update(ImportJob).where(ImportJob.id == identifier).values(updated_at=now(), **values))
        db.commit()
    sync_batches()


def claim_job():
    with SessionLocal() as db:
        stale = now() - timedelta(seconds=config.IMPORT_TIMEOUT_SECONDS + 420)
        db.execute(update(ImportJob).where(ImportJob.status.in_(["elaborazione", "conversione"]), ImportJob.updated_at < stale).values(status="errore", error="Il processo si è interrotto. Puoi riprovare l’importazione.", updated_at=now()))
        job = db.scalar(select(ImportJob).where(ImportJob.status == "in_attesa").order_by(ImportJob.created_at).with_for_update(skip_locked=True).limit(1))
        if job:
            job.status = "elaborazione"
            job.progress = 5
            job.updated_at = now()
        db.commit()
        return job


def process_job(job):
    directory = config.MEDIA_ROOT / "jobs" / job.id
    directory.mkdir(parents=True, exist_ok=True)
    final = None
    try:
        deadline = time.monotonic() + config.IMPORT_TIMEOUT_SECONDS
        with (directory / "download.log").open("wb") as log:
            with subprocess.Popen([sys.executable, "-m", "app.download", job.url, str(directory)], stdout=log, stderr=log) as process:
                while process.poll() is None:
                    if time.monotonic() > deadline:
                        process.kill()
                        process.wait()
                        raise TimeoutError("Il recupero audio ha superato il tempo massimo. Riprova o carica un MP3 personale.")
                    progress = 10
                    try:
                        progress = int(json.loads((directory / "progress.json").read_text())["progress"])
                    except (OSError, ValueError, KeyError):
                        pass
                    update_job(job.id, progress=progress)
                    time.sleep(1)
                if process.returncode != 0:
                    raise ValueError("Il recupero audio non è disponibile: contenuto limitato, formato solo audio assente o accesso non consentito. Nessun video è stato scaricato. Puoi caricare un MP3 personale.")
        result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        source = (directory / result["path"]).resolve()
        if source.parent != directory.resolve() or not source.is_file() or result.get("vcodec") != "none":
            raise ValueError("Nessun flusso solo audio valido disponibile.")
        if source.stat().st_size > config.MAX_UPLOAD_MB * 1024 * 1024:
            raise ValueError("Il flusso audio supera la dimensione massima consentita.")
        probe_audio_source(source)
        update_job(job.id, status="conversione", progress=75)
        identifier = uid()
        converted = directory / "converted.mp3"
        subprocess.run([config.FFMPEG, "-nostdin", "-y", "-v", "error", "-i", str(source), "-map", "0:a:0", "-vn", "-codec:a", "libmp3lame", "-b:a", "192k", "-map_metadata", "-1", "-metadata", f"title={result['title']}", "-metadata", f"artist={result['artist']}", str(converted)], capture_output=True, timeout=300, check=True)
        if converted.stat().st_size > config.MAX_UPLOAD_MB * 1024 * 1024:
            raise ValueError("Il file MP3 convertito supera la dimensione massima consentita.")
        metadata = probe_mp3(converted)
        digest = sha256_file(converted)
        with SessionLocal() as db:
            duplicate = db.scalar(select(Track).where(Track.user_id == job.user_id, or_(Track.sha256 == digest, Track.youtube_id == job.youtube_id)))
            if duplicate:
                update_job(job.id, status="completato", progress=100, track_id=duplicate.id)
                return
            final = config.MEDIA_ROOT / f"{identifier}.mp3"
            converted.replace(final)
            track = Track(id=identifier, user_id=job.user_id, title=result["title"], artist=result["artist"], duration=metadata["duration"], source="youtube", filename=final.name, sha256=digest, youtube_id=job.youtube_id, cover_url=result.get("cover_url"))
            db.add(track)
            db.execute(update(ImportJob).where(ImportJob.id == job.id).values(status="completato", progress=100, track_id=identifier, error=None, updated_at=now()))
            db.commit()
    except Exception as exc:
        if final:
            final.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            message = str(exc.detail)
        elif isinstance(exc, (ValueError, TimeoutError)):
            message = str(exc)
        elif isinstance(exc, FileNotFoundError):
            message = "Strumento di conversione non disponibile. Contatta l’amministratore o carica un MP3 personale."
        elif isinstance(exc, subprocess.SubprocessError):
            message = "La conversione in MP3 non è riuscita. Il flusso audio potrebbe non essere valido."
        else:
            message = "L’importazione non è riuscita. Riprova o carica un MP3 personale."
        logger.exception("Importazione %s non riuscita", job.id)
        update_job(job.id, status="errore", error=message)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def main():
    config.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(14817205)"))
        initialize_database(conn)
    logger.info("Worker Kass pronto; archivio %s", engine.dialect.name)
    while True:
        try:
            sync_batches()
            batch = claim_batch()
            if batch:
                discover_batch(batch)
            job = claim_job()
            if job:
                process_job(job)
                sync_batches()
            else:
                time.sleep(2)
        except KeyboardInterrupt:
            break
        except Exception:
            logger.exception("Errore del worker; nuovo tentativo tra 5 secondi")
            time.sleep(5)


if __name__ == "__main__":
    main()
