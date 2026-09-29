"""Expire lapsed unpaid bookings and settle payments stuck in PENDING.

Usage:
    python -m app.scripts.maintenance            # run once (e.g. from cron)
    python -m app.scripts.maintenance --every 60 # keep running, once a minute

Safe to run alongside the API and to run repeatedly; see payment_service.run_maintenance.
"""

import argparse
import logging
import sys
import time

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.dependencies.payments import get_payment_provider
from app.services.payment_service import run_maintenance

logger = logging.getLogger("maintenance")


def run_once() -> None:
    with SessionLocal() as db:
        report = run_maintenance(db, get_payment_provider())
    logger.info("Expired %d booking(s), reconciled %d payment(s)", report.expired_bookings, report.reconciled_payments)
    for error in report.reconcile_errors:
        logger.warning("Not reconciled: %s", error)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--every", type=int, metavar="SECONDS", help="repeat forever with this interval")
    args = parser.parse_args()
    logging.basicConfig(
        level=get_settings().log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if not args.every:
        run_once()
        return 0
    while True:
        try:
            run_once()
        except Exception:  # keep the loop alive through transient database errors
            logger.exception("Maintenance run failed; retrying in %ss", args.every)
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())
