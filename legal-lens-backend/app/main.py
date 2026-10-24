from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .bootstrap import init_db
from .config import CORS_ORIGINS
from .routers import auth, dashboard, evidence, instruments, reports, tests

DESCRIPTION = """
Backend for **Legal Lens** - automated test-report generation for Non-Automatic Weighing Instruments (NAWI)
as per OIML R 76 (SIH problem statement 26035).

Flow: login -> register instrument -> create test -> enter observations (validated + auto-calculated)
-> finalize (PASS/FAIL + PDF/DOCX report with QR + SHA-256) -> optional digital signature -> verify / share.
Demo logins: `officer / officer123`, `admin / admin123`, `reviewer / reviewer123`, `viewer / viewer123`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Legal Lens - NAWI Test Report API", version="1.0.0", description=DESCRIPTION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["Content-Disposition"])
for r in (auth.router, auth.users_router, instruments.router, tests.standards_router, tests.router,
          evidence.router, reports.router, dashboard.router):
    app.include_router(r)


@app.get("/api/health", tags=["Meta"])
def health():
    return {"status": "ok", "service": "legal-lens-api"}
