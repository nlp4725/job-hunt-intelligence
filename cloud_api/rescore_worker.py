"""Run the rescore reconciliation by hand (local, or against a database you can reach).

    JHI_DATABASE_URL=<owner or jhi_scorer url> ./venv/bin/python -m cloud_api.rescore_worker

In the cloud the same work runs hourly in the jhi-rescore Lambda, and new jobs
and profile changes arrive through SQS within seconds (cloud_api/rescore.py).
"""

import argparse

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cloud_api.rescore import run_reconcile
from cloud_api.settings import owner_database_url


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    engine = create_engine(owner_database_url(), connect_args={"options": "-c timezone=UTC"})
    try:
        with Session(engine) as db:
            print(run_reconcile(db), flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
