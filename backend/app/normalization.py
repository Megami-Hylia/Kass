"""Persistent two-pass EBU R128 playback copies; imported originals stay untouched."""
import json
import math
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from . import config

TARGET_I = -16
TARGET_TP = -1.5
TIMEOUT = 300
_slots = threading.BoundedSemaphore(2)


def measure(path: Path) -> dict:
    result = subprocess.run([
        config.FFMPEG, "-nostdin", "-hide_banner", "-i", str(path),
        "-map", "0:a:0", "-vn", "-af",
        f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA=11:print_format=json",
        "-f", "null", "-",
    ], capture_output=True, check=True, timeout=TIMEOUT)
    matches = re.findall(r'\{\s*"input_i".*?\}', result.stderr.decode("utf-8", errors="replace"), re.S)
    if not matches:
        raise ValueError("Misurazione del volume non disponibile.")
    return json.loads(matches[-1])


def normalized_audio(source: Path, digest: str) -> Path:
    if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        raise ValueError("Identificativo audio non valido.")
    folder = config.MEDIA_ROOT / "normalized-v1"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{digest}.mp3"
    lock = target.with_suffix(".lock")
    if target.is_file():
        return target
    # Also coordinates API threads and independent worker processes.
    deadline = time.monotonic() + TIMEOUT * 3
    while True:
        if target.is_file():
            return target
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > TIMEOUT * 3:
                lock.unlink(missing_ok=True)
                continue
            if time.monotonic() > deadline:
                raise TimeoutError("La normalizzazione sta impiegando troppo tempo. Riprova.")
            time.sleep(0.2)
    temporary = target.with_suffix(".partial.mp3")
    try:
        with _slots:
            stats = measure(source)
            keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
            values = {key: float(stats[key]) for key in keys}
            # Silence has no finite loudness: keep it silent, never amplify noise.
            if not all(math.isfinite(value) for value in values.values()):
                import shutil
                shutil.copyfile(source, temporary)
            else:
                audio_filter = (
                    f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA=11:"
                    f"measured_I={values['input_i']}:measured_TP={values['input_tp']}:"
                    f"measured_LRA={values['input_lra']}:measured_thresh={values['input_thresh']}:"
                    f"offset={values['target_offset']}:linear=true"
                )
                subprocess.run([
                    config.FFMPEG, "-nostdin", "-hide_banner", "-v", "error", "-y",
                    "-i", str(source), "-map", "0:a:0", "-vn", "-af", audio_filter,
                    "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k", str(temporary),
                ], capture_output=True, check=True, timeout=TIMEOUT)
            os.replace(temporary, target)
        return target
    finally:
        temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
