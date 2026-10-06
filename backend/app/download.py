"""Isolated, time-bounded yt-dlp process. Retrieves audio-only streams."""
import json
import sys
from pathlib import Path

import yt_dlp

from . import config
from .media import youtube_cover


def download(url: str, destination: Path):
    def progress(status):
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        downloaded = status.get("downloaded_bytes", 0)
        if downloaded > config.MAX_UPLOAD_MB * 1024 * 1024:
            raise ValueError("L’audio supera il limite di dimensione consentito.")
        if total:
            pending = destination / "progress.tmp"
            pending.write_text(json.dumps({"progress": min(65, 10 + int(downloaded / total * 55))}))
            pending.replace(destination / "progress.json")

    def allowed(info, *, incomplete=False):
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
            return "I contenuti live non sono supportati."
        duration = info.get("duration")
        if duration and duration > config.MAX_IMPORT_SECONDS:
            return "Il contenuto supera la durata massima consentita."

    options = {
        "format": "bestaudio[vcodec=none]",
        "outtmpl": str(destination / "source.%(ext)s"),
        "noplaylist": True,
        "max_filesize": config.MAX_UPLOAD_MB * 1024 * 1024,
        "match_filter": allowed,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "concurrent_fragment_downloads": 1,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "writethumbnail": False,
        "writeinfojson": False,
        "cachedir": False,
        "allow_unplayable_formats": False,
        "progress_hooks": [progress],
        "js_runtimes": {"node": {}},
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
        if not info or info.get("_type") == "playlist" or info.get("vcodec") != "none" or info.get("acodec") in (None, "none"):
            raise ValueError("Nessun flusso solo audio disponibile. Il video non verrà scaricato.")
        if not info.get("duration") or allowed(info):
            raise ValueError("La durata del contenuto non è disponibile o non è supportata.")
        downloader.process_info(info)
        source = Path(downloader.prepare_filename(info))
        if not source.is_file() or source.stat().st_size > config.MAX_UPLOAD_MB * 1024 * 1024:
            raise ValueError("Flusso audio non disponibile o troppo grande.")
        result = {"path": source.name, "title": str(info.get("title") or "Senza titolo").strip()[:200] or "Senza titolo", "artist": str(info.get("artist") or info.get("uploader") or "Artista sconosciuto").strip()[:200] or "Artista sconosciuto", "vcodec": info.get("vcodec"), "cover_url": youtube_cover(info.get("id"), info)}
        (destination / "result.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    download(sys.argv[1], Path(sys.argv[2]))
