import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

from . import config


def youtube_cover(identifier: str | None, metadata: dict | None = None) -> str | None:
    if not identifier or not re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
        return None
    candidates = [item for item in (metadata or {}).get("thumbnails", []) if isinstance(item, dict) and str(item.get("url", "")).startswith("https://")]
    squares = [item for item in candidates if item.get("width") and item.get("width") == item.get("height")]
    if squares:
        return max(squares, key=lambda item: (item.get("width") or 0) * (item.get("height") or 0))["url"]
    return f"https://i.ytimg.com/vi/{identifier}/mqdefault.jpg"





def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe_mp3(path: Path) -> dict:
    try:
        result = subprocess.run([config.FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, timeout=30, check=True)
        # FFprobe emits UTF-8 JSON. Decode its bytes independently of Windows locale.
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        # ID3 cover art is allowed; actual video streams are not.
        video = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
        duration = float(data.get("format", {}).get("duration", 0))
        if len(audio) != 1 or audio[0].get("codec_name") != "mp3" or video or data.get("format", {}).get("format_name") != "mp3" or not 0 < duration < 86400:
            raise ValueError("invalid media")
        return {"duration": duration, "tags": data.get("format", {}).get("tags", {})}
    except FileNotFoundError:
        raise HTTPException(503, "FFmpeg/FFprobe non è disponibile sul server. Contatta l’amministratore.")
    except (subprocess.SubprocessError, ValueError, KeyError):
        raise HTTPException(400, "Il file non è un MP3 audio valido. Sono ammessi soltanto MP3 senza video.")


def probe_audio_source(path: Path):
    """Check the retrieved stream itself before converting an audio-only selection."""
    result = subprocess.run([config.FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, timeout=30, check=True)
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    duration = float(data.get("format", {}).get("duration", 0))
    if not streams or any(stream.get("codec_type") != "audio" for stream in streams):
        raise ValueError("Il flusso recuperato non contiene esclusivamente audio. Importazione annullata.")
    if not 0 < duration <= config.MAX_IMPORT_SECONDS + 1:
        raise ValueError("La durata dell’audio recuperato non è supportata.")


def youtube_id(url: str) -> str:
    try:
        parsed = urlparse(url)
        valid = parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in (None, 443)
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(400, "Inserisci un link HTTPS di YouTube valido.")
    host = (parsed.hostname or "").lower()
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"} and parsed.path == "/watch":
        identifier = parse_qs(parsed.query).get("v", [""])[0]
    elif host == "youtu.be":
        identifier = parsed.path.strip("/")
    else:
        raise HTTPException(400, "Usa un link youtube.com/watch oppure youtu.be a un singolo contenuto.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
        raise HTTPException(400, "Il link non contiene un identificativo YouTube valido.")
    return identifier


def youtube_playlist_id(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        valid = parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in (None, 443)
        valid = valid and ((host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"} and parsed.path in {"/playlist", "/watch"}) or host == "youtu.be")
        if not valid:
            return None
        identifier = parse_qs(parsed.query).get("list", [None])[0]
        if identifier is None:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{2,200}", identifier):
            raise HTTPException(400, "Il link della playlist YouTube non è valido.")
        if identifier.startswith("RD"):
            raise HTTPException(400, "I Mix automatici non sono playlist fisse. Condividi il link di una playlist salvata.")
        return identifier
    except ValueError:
        raise HTTPException(400, "Inserisci un link HTTPS di YouTube valido.")

