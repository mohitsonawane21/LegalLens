from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    designation: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # admin | officer | reviewer | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    instrument_type: Mapped[str] = mapped_column(String(60), default="Weighing Scale")
    manufacturer: Mapped[str] = mapped_column(String(120))
    model: Mapped[str] = mapped_column(String(120))
    serial_number: Mapped[str] = mapped_column(String(80), index=True)
    accuracy_class: Mapped[str] = mapped_column(String(5))
    max_capacity: Mapped[float] = mapped_column(Float)
    min_capacity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    division_d: Mapped[float] = mapped_column(Float)
    verification_interval_e: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(5), default="kg")
    location: Mapped[str] = mapped_column(String(200), default="")
    manufacturer_details: Mapped[dict] = mapped_column(JSON, default=dict)
    technical_parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    tests: Mapped[list["TestRun"]] = relationship(back_populates="instrument")


class TestRun(Base):
    """One type-evaluation test session of one instrument (named TestRun so pytest never collects it)."""
    __tablename__ = "test_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    instrument_pk: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    standard: Mapped[str] = mapped_column(String(40))
    lab_name: Mapped[str] = mapped_column(String(200))
    test_date: Mapped[date] = mapped_column(Date)
    environment: Mapped[dict] = mapped_column(JSON, default=dict)
    remarks: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | in_progress | completed
    overall_result: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)  # PASS | FAIL
    created_by: Mapped[str] = mapped_column(String(50), default="")
    prepared_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    instrument: Mapped["Instrument"] = relationship(back_populates="tests")
    observations: Mapped[list["Observation"]] = relationship(back_populates="test", cascade="all, delete-orphan")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="test", cascade="all, delete-orphan")
    videos: Mapped[list["VideoEvidence"]] = relationship(back_populates="test", cascade="all, delete-orphan")
    report: Mapped[Optional["Report"]] = relationship(back_populates="test", uselist=False, cascade="all, delete-orphan")


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (UniqueConstraint("test_pk", "test_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    test_pk: Mapped[int] = mapped_column(ForeignKey("test_runs.id"))
    test_type: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    updated_by: Mapped[str] = mapped_column(String(50), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    test: Mapped["TestRun"] = relationship(back_populates="observations")


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_pk: Mapped[int] = mapped_column(ForeignKey("test_runs.id"))
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(100), default="")
    kind: Mapped[str] = mapped_column(String(20), default="photo")  # photo | document
    caption: Mapped[str] = mapped_column(String(300), default="")
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[str] = mapped_column(String(50), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    test: Mapped["TestRun"] = relationship(back_populates="attachments")


class VideoEvidence(Base):
    __tablename__ = "videos"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_pk: Mapped[int] = mapped_column(ForeignKey("test_runs.id"))
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    purpose: Mapped[str] = mapped_column(String(200), default="")
    extracted_reading: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(30), default="")
    extraction_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    extraction_note: Mapped[str] = mapped_column(String(300), default="")
    confirmed_reading: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confirmed_by: Mapped[str] = mapped_column(String(50), default="")
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    uploaded_by: Mapped[str] = mapped_column(String(50), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    test: Mapped["TestRun"] = relationship(back_populates="videos")


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_pk: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), unique=True)
    report_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    pdf_path: Mapped[str] = mapped_column(String(500), default="")
    docx_path: Mapped[str] = mapped_column(String(500), default="")
    pdf_sha256: Mapped[str] = mapped_column(String(64), default="")
    docx_sha256: Mapped[str] = mapped_column(String(64), default="")
    generated_by: Mapped[str] = mapped_column(String(50), default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    signed_by: Mapped[str] = mapped_column(String(50), default="")
    signed_by_name: Mapped[str] = mapped_column(String(120), default="")
    signed_by_designation: Mapped[str] = mapped_column(String(120), default="")
    signed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    signature: Mapped[str] = mapped_column(String(128), default="")
    test: Mapped["TestRun"] = relationship(back_populates="report")


class AuditLog(Base):
    """Append-only, hash-chained log (each row hashes the previous row) - tamper-evident audit trail."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    username: Mapped[str] = mapped_column(String(50), default="")
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(30), index=True)
    entity_id: Mapped[str] = mapped_column(String(50), index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    payload: Mapped[str] = mapped_column(Text, default="")
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
