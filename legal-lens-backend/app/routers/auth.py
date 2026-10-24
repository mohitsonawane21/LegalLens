from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..models import User
from ..security import ROLES, create_token, current_user, hash_password, require_roles, verify_password
from ..serializers import ser_user

router = APIRouter(prefix="/api/auth", tags=["Auth"])
users_router = APIRouter(prefix="/api/users", tags=["Users"])


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(401, "Incorrect username or password")
    audit.log(db, user.username, "auth.login", "user", user.username)
    db.commit()
    return {"access_token": create_token(user), "token_type": "bearer", "user": ser_user(user)}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return ser_user(user)


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6)
    full_name: str
    designation: str = ""
    role: str = "viewer"


@users_router.post("", status_code=201)
def create_user(body: UserIn, db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    if body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(409, "Username already exists")
    u = User(username=body.username, full_name=body.full_name, designation=body.designation, role=body.role,
             password_hash=hash_password(body.password))
    db.add(u)
    audit.log(db, admin.username, "user.created", "user", body.username, {"role": body.role})
    db.commit()
    return ser_user(u)


@users_router.get("")
def list_users(db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    return [ser_user(u) for u in db.query(User).order_by(User.id).all()]
