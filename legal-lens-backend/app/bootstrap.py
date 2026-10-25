from .config import SECRET_KEY, SEED_DEMO_USERS
from .database import Base, SessionLocal, engine
from .models import User
from .security import hash_password

DEMO_USERS = [
    ("admin", "admin123", "System Administrator", "Administrator", "admin"),
    ("officer", "officer123", "Dr. Pallavi Baviskar", "Test Officer", "officer"),
    ("reviewer", "reviewer123", "Reviewing Metrologist", "Senior Metrologist", "reviewer"),
    ("viewer", "viewer123", "Read-only User", "Auditor", "viewer"),
]


def init_db() -> None:
    Base.metadata.create_all(engine)
    if SECRET_KEY == "change-me-in-production":
        print("WARNING: using the default SECRET_KEY - set SECRET_KEY in the environment for any real deployment.")
    if not SEED_DEMO_USERS:
        return
    with SessionLocal() as db:
        if db.query(User).count() == 0:
            for username, pw, name, desig, role in DEMO_USERS:
                db.add(User(username=username, password_hash=hash_password(pw), full_name=name,
                            designation=desig, role=role))
            db.commit()
            print("Seeded demo users (admin/officer/reviewer/viewer) - change these passwords before deploying.")
