"""Auth API - 注册/登录（简化版，密码 hash）"""
import hashlib, logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.database.models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["认证"])

class RegisterReq(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=4, max_length=64)

class LoginReq(BaseModel):
    username: str
    password: str

class AuthResp(BaseModel):
    status: str
    message: str
    user_id: int = None
    username: str = None

def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

@router.post("/register", response_model=AuthResp)
def register(req: RegisterReq, db: Session = Depends(get_db)):
    exist = db.query(User).filter(User.username == req.username).first()
    if exist:
        raise HTTPException(400, "用户名已存在")
    user = User(username=req.username, password_hash=_hash(req.password))
    db.add(user); db.commit(); db.refresh(user)
    logger.info("用户注册: %s (id=%d)", req.username, user.id)
    return AuthResp(status="ok", message="注册成功", user_id=user.id, username=user.username)

@router.post("/login", response_model=AuthResp)
def login(req: LoginReq, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or user.password_hash != _hash(req.password):
        raise HTTPException(401, "用户名或密码错误")
    logger.info("用户登录: %s (id=%d)", req.username, user.id)
    return AuthResp(status="ok", message="登录成功", user_id=user.id, username=user.username)