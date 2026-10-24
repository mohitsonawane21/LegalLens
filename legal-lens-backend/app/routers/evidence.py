"""Photos / supporting documents and the 'Upload Test Video' flow (upload -> extract reading -> verify)."""
import hashlib
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import audit
from ..common import ensure_editable, get_test_or_404
from ..config import ATTACHMENTS_DIR, MAX_ATTACHMENT_MB, MAX_VIDEO_MB, VIDEOS_DIR
from ..database import get_db
from ..models import Attachment, User, VideoEvidence, utcnow
from ..security import current_user, require_roles
from ..serializers import ser_attachment, ser_video
from ..services.reading_extractor import extract_reading

router = APIRouter(prefix="/api", tags=["Evidence"])
WRITERS = ("admin", "officer")
ATTACH_EXT = {".jpg", ".jpeg", ".png", ".pdf", ".docx", ".xlsx", ".csv", ".txt"}
VIDEO_EXT = {".mp4", ".mov", ".avi"}


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(name or "file").name)[:120]


async def _save_upload(file: UploadFile, folder: Path, max_mb: int):
    """Stream an upload to disk, hashing as we go. Returns (path, sha256, size)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex[:8]}_{_safe_name(file.filename)}"
    h, size, limit = hashlib.sha256(), 0, max_mb * 1024 * 1024
    with open(path, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, f"File larger than {max_mb} MB")
            h.update(chunk)
            out.write(chunk)
    return path, h.hexdigest(), size


# ------------------------------------------------------------------ attachments
@router.post("/tests/{test_id}/attachments", status_code=201)
async def upload_attachment(test_id: str, file: UploadFile = File(...), kind: str = Form("photo"),
                            caption: str = Form(""), db: Session = Depends(get_db),
                            user: User = Depends(require_roles(*WRITERS))):
    t = get_test_or_404(db, test_id)
    ensure_editable(t)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ATTACH_EXT:
        raise HTTPException(422, f"Allowed file types: {sorted(ATTACH_EXT)}")
    if kind not in ("photo", "document"):
        raise HTTPException(422, "kind must be 'photo' or 'document'")
    path, sha, size = await _save_upload(file, ATTACHMENTS_DIR / test_id, MAX_ATTACHMENT_MB)
    a = Attachment(filename=_safe_name(file.filename), stored_path=str(path), kind=kind, caption=caption, sha256=sha,
                   size=size, content_type=file.content_type or mimetypes.guess_type(path.name)[0] or "",
                   uploaded_by=user.username)
    t.attachments.append(a)
    audit.log(db, user.username, "attachment.added", "test", test_id, {"filename": a.filename, "sha256": sha})
    db.commit()
    return ser_attachment(a, test_id)


@router.get("/tests/{test_id}/attachments")
def list_attachments(test_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    t = get_test_or_404(db, test_id)
    return [ser_attachment(a, test_id) for a in t.attachments]


@router.get("/attachments/{att_id}/download")
def download_attachment(att_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a = db.get(Attachment, att_id)
    if not a or not Path(a.stored_path).exists():
        raise HTTPException(404, "Attachment not found")
    return FileResponse(a.stored_path, filename=a.filename, media_type=a.content_type or None)


@router.delete("/attachments/{att_id}", status_code=204)
def delete_attachment(att_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*WRITERS))):
    a = db.get(Attachment, att_id)
    if not a:
        raise HTTPException(404, "Attachment not found")
    t = a.test
    ensure_editable(t)
    Path(a.stored_path).unlink(missing_ok=True)
    db.delete(a)
    audit.log(db, user.username, "attachment.deleted", "test", t.test_id, {"filename": a.filename})
    db.commit()


# ------------------------------------------------------------------ test video
@router.post("/tests/{test_id}/videos", status_code=201)
async def upload_video(test_id: str, file: UploadFile = File(...), purpose: str = Form(""),
                       roi: Optional[str] = Form(None, description="Optional display crop 'x,y,w,h' as fractions 0-1"),
                       db: Session = Depends(get_db), user: User = Depends(require_roles(*WRITERS))):
    """Step 1+2 of the UI flow: store the video, run automatic reading extraction. Operator confirms next."""
    t = get_test_or_404(db, test_id)
    ensure_editable(t)
    if Path(file.filename or "").suffix.lower() not in VIDEO_EXT:
        raise HTTPException(422, f"Supported video formats: {sorted(VIDEO_EXT)}")
    box = None
    if roi:
        try:
            box = tuple(float(x) for x in roi.split(","))
            assert len(box) == 4 and all(0 <= x <= 1 for x in box)
        except Exception:
            raise HTTPException(422, "roi must be 'x,y,w,h' with values between 0 and 1")
    path, sha, size = await _save_upload(file, VIDEOS_DIR / test_id, MAX_VIDEO_MB)
    res = await run_in_threadpool(extract_reading, str(path), box)
    v = VideoEvidence(filename=_safe_name(file.filename), stored_path=str(path), sha256=sha, size=size, purpose=purpose,
                      duration_s=res.get("duration_s"), extracted_reading=res.get("reading"),
                      extraction_method=res.get("method", ""), extraction_confidence=res.get("confidence"),
                      extraction_note=res.get("note", ""), uploaded_by=user.username)
    t.videos.append(v)
    audit.log(db, user.username, "video.uploaded", "test", test_id,
              {"filename": v.filename, "sha256": sha, "extracted_reading": v.extracted_reading})
    db.commit()
    return ser_video(v, t)


@router.get("/tests/{test_id}/videos")
def list_videos(test_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    t = get_test_or_404(db, test_id)
    return [ser_video(v, t) for v in t.videos]


class ConfirmIn(BaseModel):
    reading: float = Field(description="Value shown on the instrument display, as verified by the operator")


@router.post("/videos/{video_id}/confirm")
def confirm_reading(video_id: int, body: ConfirmIn, db: Session = Depends(get_db),
                    user: User = Depends(require_roles(*WRITERS))):
    """Step 3 of the UI flow ('Confirm Reading'). The confirmed value is what gets used and logged."""
    v = db.get(VideoEvidence, video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    ensure_editable(v.test)
    v.confirmed_reading, v.confirmed_by, v.confirmed_at = body.reading, user.username, utcnow()
    audit.log(db, user.username, "video.reading_confirmed", "test", v.test.test_id,
              {"video_id": v.id, "confirmed": body.reading, "auto_detected": v.extracted_reading})
    db.commit()
    return ser_video(v, v.test)


@router.get("/videos/{video_id}/file")
def stream_video(video_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    v = db.get(VideoEvidence, video_id)
    if not v or not Path(v.stored_path).exists():
        raise HTTPException(404, "Video not found")
    return FileResponse(v.stored_path, media_type=mimetypes.guess_type(v.stored_path)[0] or "video/mp4")
