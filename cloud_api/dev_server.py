"""Run the cloud API on this machine against a local Postgres, for testing the
extension's cloud copy and (later) the React app. Dev login only; bound to
localhost.

    ./venv/bin/python -m cloud_api.dev_server \
        --owner-url postgresql+psycopg://jhi@localhost:5433/jhi_cloud --port 5060

Creates (if missing) two logins in the jhi_app and jhi_admin_api roles so
requests run under row-level security exactly as in the cloud. Seniority
classification is off by default (captures are saved and queued, level left
for the backfill); --classify deepseek makes one paid LLM call per new job.
"""

import argparse
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from cloud_api.app import create_app
from cloud_api.auth.verify import FakeVerifier
from resume.storage import DevSignedStorage
from resume.store import ResumeCipher

LOGINS = {"jhi_api_dev": "jhi_app", "jhi_admin_dev": "jhi_admin_api"}


def ensure_logins(owner_url: str) -> dict[str, str]:
    engine = create_engine(owner_url)
    with engine.begin() as conn:
        for login, role in LOGINS.items():
            conn.execute(text(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{login}') "
                              f"THEN CREATE ROLE {login} LOGIN; END IF; END $$"))
            conn.execute(text(f"GRANT {role} TO {login}"))
    engine.dispose()
    base = make_url(owner_url)
    return {role: base.set(username=login, password=None).render_as_string(hide_password=False)
            for login, role in LOGINS.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner-url", required=True, help="table owner connection (creates the logins)")
    parser.add_argument("--port", type=int, default=5060)
    parser.add_argument("--classify", choices=("off", "deepseek"), default="off")
    parser.add_argument("--files", default="data/cloud_dev_files", help="where dev resume uploads are stored")
    args = parser.parse_args()

    urls = ensure_logins(args.owner_url)
    key = os.environ.get("JHI_RESUME_KEY") or Fernet.generate_key()
    app = create_app(urls["jhi_app"], admin_database_url=urls["jhi_admin_api"], verifier=FakeVerifier(),
                     auth_mode="dev", host="127.0.0.1", cors_origins=("http://localhost:5173",),
                     storage=DevSignedStorage(root=Path(args.files), secret=secrets.token_bytes(32),
                                              base_url=f"http://127.0.0.1:{args.port}"),
                     cipher=ResumeCipher(key),
                     classify=(lambda posting: None) if args.classify == "off" else None)
    app.run(host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
