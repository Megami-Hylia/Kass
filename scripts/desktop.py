"""Private desktop process entry point; no passwords are passed on command lines."""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["init-db", "api", "worker"], required=True)
    parser.add_argument("--cass-instance", required=True)
    args = parser.parse_args()
    config = json.loads((ROOT / ".local" / "cass.json").read_text(encoding="utf-8-sig"))
    if config["instance_id"] != args.cass_instance:
        raise SystemExit("Configurazione dell'istanza Kass non corrispondente.")

    if args.role == "init-db":
        import psycopg

        try:
            with psycopg.connect(host="127.0.0.1", port=config["postgres_port"],
                                 user="cass", password=config["postgres_password"],
                                 dbname="postgres", autocommit=True, connect_timeout=10) as conn:
                if not conn.execute("SELECT 1 FROM pg_database WHERE datname = 'cass'").fetchone():
                    conn.execute('CREATE DATABASE "cass"')
        except psycopg.Error:
            raise SystemExit("PostgreSQL non disponibile o credenziali locali non valide. Consulta .local/logs/postgres.log.") from None
        return

    # Only local, generated hexadecimal passwords are used in this URL.
    os.environ.update({
        "DATABASE_URL": f"postgresql+psycopg://cass:{config['postgres_password']}@127.0.0.1:{config['postgres_port']}/cass",
        "MEDIA_ROOT": str(ROOT / ".local" / "media"),
        "FRONTEND_DIST": str(ROOT / "frontend" / "dist"),
        "ALLOWED_ORIGINS": f"http://127.0.0.1:{config['app_port']},http://localhost:{config['app_port']}",
        "COOKIE_SECURE": "false",
        "CASS_INSTANCE_ID": config["instance_id"],
        "PERSONAL_MODE": "true",
        "CASS_LOCAL_DESKTOP": "true",
        "PYTHONUNBUFFERED": "1",
    })
    for name in ("FFMPEG_BINARY", "FFPROBE_BINARY"):
        if config.get(name.lower()):
            os.environ[name] = config[name.lower()]
    os.chdir(ROOT / "backend")
    sys.path.insert(0, str(ROOT / "backend"))
    if args.role == "api":
        import uvicorn

        uvicorn.run("app.main:app", host="127.0.0.1", port=config["app_port"],
                    access_log=False, log_level="info")
    else:
        runpy.run_module("app.worker", run_name="__main__")


if __name__ == "__main__":
    main()
