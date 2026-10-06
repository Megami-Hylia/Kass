"""Read playlist metadata only in a separate, time-bounded process."""
import json
import re
import sys
from pathlib import Path

import yt_dlp

MAX_PLAYLIST_TRACKS = 2000


def discover(url):
    options = {
        "extract_flat": "in_playlist", "skip_download": True,
        "playlist_items": f"1:{MAX_PLAYLIST_TRACKS + 1}", "ignoreerrors": True,
        "quiet": True, "no_warnings": True, "cachedir": False,
        "socket_timeout": 20, "retries": 2, "js_runtimes": {"node": {}},
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
        if not info or info.get("_type") not in {"playlist", "multi_video"}:
            raise ValueError("Playlist non disponibile. Verifica il link e che la playlist sia accessibile.")
        entries, seen = [], set()
        for index, entry in enumerate(info.get("entries") or []):
            if index >= MAX_PLAYLIST_TRACKS:
                raise ValueError(f"La playlist supera il limite di {MAX_PLAYLIST_TRACKS} brani. Dividila in playlist più piccole.")
            entry = entry or {}
            identifier = entry.get("id") or ""
            if identifier in seen:
                continue
            valid = bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier))
            if valid:
                seen.add(identifier)
            title = str(entry.get("title") or "Brano non disponibile")[:200]
            unavailable = not valid or entry.get("availability") in {"private", "needs_auth", "premium_only", "subscriber_only"} or title in {"[Private video]", "[Deleted video]"}
            entries.append({"video_id": identifier if valid else None, "title": title, "error": "Brano privato, rimosso o non disponibile." if unavailable else None})
        if not entries:
            raise ValueError("La playlist è vuota o i suoi brani non sono disponibili.")
        return {"title": str(info.get("title") or "Playlist YouTube")[:200], "entries": entries}


if __name__ == "__main__":
    destination = Path(sys.argv[2])
    try:
        result = discover(sys.argv[1])
        destination.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Impossibile leggere la playlist. Verifica il link e la disponibilità su YouTube."
        destination.write_text(json.dumps({"error": message}), encoding="utf-8")
        raise SystemExit(1)
