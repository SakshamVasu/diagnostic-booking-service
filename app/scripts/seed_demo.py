"""Load demo centres, tests and centre-specific prices. Safe to run repeatedly.

Usage:
    python -m app.scripts.seed_demo                   # catalogue only
    python -m app.scripts.seed_demo --with-accounts   # also an example admin and patient

The example accounts use publicly documented passwords (see DEMO_ACCOUNTS and the README):
only use them on a local or demo database, never in production.
"""

import argparse
import sys
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.database import SessionLocal
from app.models.centre import CentreTest, DiagnosticCentre
from app.models.test import DiagnosticTest, SampleType
from app.models.user import User, UserRole
from app.schemas.auth import SignupRequest

CENTRES = [
    ("Apollo Diagnostics", "Delhi"),
    ("Metropolis Healthcare", "Mumbai"),
    ("Dr Lal PathLabs", "Delhi"),
    ("Thyrocare Wellness Centre", "Bengaluru"),
    ("SRL Diagnostics", "Chennai"),
    ("Vijaya Diagnostic Centre", "Hyderabad"),
]

TESTS = [
    ("Complete Blood Count (CBC)", "Measures red and white blood cells, haemoglobin and platelets.", "500.00"),
    ("Lipid Profile", "Total cholesterol, HDL, LDL and triglycerides.", "800.00"),
    ("Thyroid Profile (T3, T4, TSH)", "Screens for an over- or under-active thyroid.", "650.00"),
    ("HbA1c", "Average blood sugar over the past three months.", "550.00"),
    ("Liver Function Test", "Enzymes, proteins and bilirubin that show how the liver is working.", "900.00"),
    ("Kidney Function Test", "Creatinine, urea and electrolytes.", "850.00"),
    ("Vitamin D (25-OH)", "Checks for vitamin D deficiency.", "1400.00"),
    ("Chest X-Ray", "Single-view digital chest radiograph.", "450.00"),
]

# Patient guidance per test name: (sample type, fasting hours, preparation, report turnaround hours).
TEST_DETAILS: dict[str, tuple[SampleType, int, str, int]] = {
    "Complete Blood Count (CBC)": (SampleType.BLOOD, 0, "No special preparation needed.", 12),
    "Lipid Profile": (
        SampleType.BLOOD,
        10,
        "Fast for 10-12 hours beforehand; water is fine. Avoid alcohol for 24 hours.",
        24,
    ),
    "Thyroid Profile (T3, T4, TSH)": (
        SampleType.BLOOD,
        0,
        "Take the test before your morning thyroid medicine, unless your doctor says otherwise.",
        24,
    ),
    "HbA1c": (SampleType.BLOOD, 0, "No fasting needed. Continue your usual medicines.", 24),
    "Liver Function Test": (SampleType.BLOOD, 8, "Fast for 8 hours beforehand; water is fine.", 24),
    "Kidney Function Test": (SampleType.BLOOD, 0, "Drink water as usual. Mention any supplements you take.", 24),
    "Vitamin D (25-OH)": (SampleType.BLOOD, 0, "No fasting needed. Mention any vitamin D supplements.", 48),
    "Chest X-Ray": (
        SampleType.IMAGING,
        0,
        "Wear clothing without metal (zips, buttons) and remove jewellery. Tell staff if you may be pregnant.",
        6,
    ),
}

# Price multiplier per centre (index into CENTRES) and which tests (index into TESTS) it offers.
OFFERINGS = {
    0: (Decimal("1.10"), [0, 1, 2, 3, 4, 5, 6]),
    1: (Decimal("1.25"), [0, 1, 2, 4, 6, 7]),
    2: (Decimal("1.00"), [0, 1, 2, 3, 5]),
    3: (Decimal("0.85"), [0, 2, 3, 6]),
    4: (Decimal("0.95"), [0, 1, 4, 5, 7]),
    5: (Decimal("1.05"), [0, 3, 6, 7]),
}


# Example logins for local demos, created only with --with-accounts. The passwords are public.
DEMO_ACCOUNTS = [
    ("Demo Admin", "admin@example.com", "DemoAdmin#2026", UserRole.ADMIN),
    ("Demo Patient", "patient@example.com", "DemoPatient#2026", UserRole.USER),
]


def _ensure_demo_accounts(db: Session) -> list[str]:
    """Create the example accounts that don't exist yet. Existing users are left untouched."""
    created = []
    for name, email, password, role in DEMO_ACCOUNTS:
        if db.scalar(select(User).where(User.email == email)) is not None:
            continue
        data = SignupRequest(name=name, email=email, password=password)  # same rules as signup
        db.add(User(name=data.name, email=data.email, password_hash=hash_password(data.password), role=role))
        created.append(f"{email} ({role.value})")
    return created


def _get_or_create_centre(db: Session, name: str, location: str) -> DiagnosticCentre:
    centre = db.scalar(
        select(DiagnosticCentre).where(DiagnosticCentre.name == name, DiagnosticCentre.location == location)
    )
    if centre is None:
        centre = DiagnosticCentre(name=name, location=location)
        db.add(centre)
    return centre


def _get_or_create_test(db: Session, name: str, description: str, base_price: str) -> DiagnosticTest:
    test = db.scalar(select(DiagnosticTest).where(DiagnosticTest.name == name))
    if test is None:
        test = DiagnosticTest(name=name, description=description, base_price=Decimal(base_price))
        db.add(test)
    if name in TEST_DETAILS and test.sample_type is None:  # fill in details without overwriting edits
        test.sample_type, test.fasting_hours, test.preparation_instructions, test.report_turnaround_hours = (
            TEST_DETAILS[name]
        )
    return test


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--with-accounts", action="store_true", help="also create an example admin and patient (demo use only)"
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        accounts = _ensure_demo_accounts(db) if args.with_accounts else []
        centres = [_get_or_create_centre(db, *centre) for centre in CENTRES]
        tests = [_get_or_create_test(db, *test) for test in TESTS]
        db.flush()

        added = 0
        for centre_index, (multiplier, test_indexes) in OFFERINGS.items():
            centre = centres[centre_index]
            for test_index in test_indexes:
                test = tests[test_index]
                exists = db.scalar(
                    select(CentreTest).where(CentreTest.centre_id == centre.id, CentreTest.test_id == test.id)
                )
                if exists is None:
                    price = (test.base_price * multiplier).quantize(Decimal("1"))  # whole rupees
                    db.add(CentreTest(centre_id=centre.id, test_id=test.id, price=price))
                    added += 1
        db.commit()
    print(f"Demo data ready: {len(CENTRES)} centres, {len(TESTS)} tests, {added} new price entries.")
    if args.with_accounts:
        print("Example accounts created: " + (", ".join(accounts) if accounts else "none (they already exist)"))
        print("Their passwords are listed in README.md, section 1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
