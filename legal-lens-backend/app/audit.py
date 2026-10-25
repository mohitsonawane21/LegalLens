import hashlib
import json

from sqlalchemy.orm import Session

from .models import AuditLog, utcnow


def compute_hash(prev_hash: str, payload: str) -> str:
    return hashlib.sha256((prev_hash + payload).encode()).hexdigest()


def log(db: Session, username: str, action: str, entity: str, entity_id: str, detail: dict = None) -> None:
    """Append a hash-chained audit entry. Caller commits."""
    db.flush()  # make earlier pending entries of this request visible so the chain stays linear
    last = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    prev = last.hash if last else "GENESIS"
    ts = utcnow()
    detail = detail or {}
    payload = json.dumps({"ts": ts.isoformat(), "user": username, "action": action, "entity": entity,
                          "entity_id": entity_id, "detail": detail}, sort_keys=True, default=str)
    db.add(AuditLog(ts=ts, username=username, action=action, entity=entity, entity_id=entity_id, detail=detail,
                    payload=payload, prev_hash=prev, hash=compute_hash(prev, payload)))
