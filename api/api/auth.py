"""
用户认证模块 - JWT + 密码哈希
"""
from datetime import datetime, timedelta
from typing import Optional
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.orm import Session
from crawler.models import User, UserRole, get_user_session

# JWT配置
SECRET_KEY = "your-secret-key-change-in-production"  # 生产环境请更换
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7天

# 密码上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HTTP Bearer认证
security = HTTPBearer(auto_error=False)


class TokenData(BaseModel):
    """Token数据"""
    user_id: int
    username: str
    role: str


class UserResponse(BaseModel):
    """用户响应模型"""
    id: int
    username: str
    email: str
    phone: str
    role: str
    credits: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    """用户注册请求"""
    username: str
    email: str
    phone: str
    password: str


class UserLogin(BaseModel):
    """用户登录请求"""
    username: str
    password: str


class TokenResponse(BaseModel):
    """Token响应"""
    access_token: str
    token_type: str
    user: UserResponse


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """获取密码哈希"""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """创建JWT Token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[TokenData]:
    """解码JWT Token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("user_id")
        username: str = payload.get("sub")
        role: str = payload.get("role")
        if user_id is None or username is None:
            return None
        return TokenData(user_id=user_id, username=username, role=role)
    except JWTError:
        return None


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> User:
    """获取当前用户（依赖注入）"""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证信息",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = credentials.credentials
    token_data = decode_token(token)
    
    if token_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证信息",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    session = get_user_session()
    try:
        user = session.query(User).filter(User.id == token_data.user_id).first()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户不存在",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户已被禁用",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user
    finally:
        session.close()


async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """获取当前激活用户"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="用户已被禁用")
    return current_user


async def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """获取当前管理员用户"""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )
    return current_user


def is_admin(user: User) -> bool:
    """检查用户是否为管理员"""
    return user.role == UserRole.ADMIN


def init_admin_user():
    """初始化管理员用户"""
    session = get_user_session()
    try:
        # 检查是否已存在管理员
        admin = session.query(User).filter(User.username == "admin").first()
        if not admin:
            admin = User(
                username="admin",
                email="admin@example.com",
                phone="13800000000",
                hashed_password=get_password_hash("admin"),
                role=UserRole.ADMIN,
                credits=999999999,  # 管理员无限积分
                is_active=True
            )
            session.add(admin)
            session.commit()
            print("✅ 管理员账号已创建: admin / admin")
    finally:
        session.close()


def get_user_count() -> int:
    """获取用户总数"""
    session = get_user_session()
    try:
        count = session.query(User).count()
        return count
    finally:
        session.close()


def register_user(username: str, email: str, phone: str, password: str) -> User:
    """注册用户"""
    # 验证手机号格式
    import re
    if not re.match(r'^1[3-9]\d{9}$', phone):
        raise HTTPException(status_code=400, detail="手机号格式不正确")
    
    session = get_user_session()
    try:
        # 检查用户名是否已存在
        existing_user = session.query(User).filter(
            (User.username == username) | (User.email == email)
        ).first()
        
        if existing_user:
            raise HTTPException(status_code=400, detail="用户名或邮箱已存在")
        
        # 检查手机号是否已存在
        existing_phone = session.query(User).filter(User.phone == phone).first()
        if existing_phone:
            raise HTTPException(status_code=400, detail="手机号已被注册")
        
        # 创建新用户
        user = User(
            username=username,
            email=email,
            phone=phone,
            hashed_password=get_password_hash(password),
            role=UserRole.USER,
            credits=100,  # 新用户赠送100积分
            is_active=True
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user
    finally:
        session.close()


def authenticate_user(username: str, password: str) -> Optional[User]:
    """验证用户登录"""
    session = get_user_session()
    try:
        user = session.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=401, detail="用户不存在")
        if not verify_password(password, user.hashed_password):
            raise HTTPException(status_code=401, detail="密码错误")
        if not user.is_active:
            raise HTTPException(status_code=401, detail="账号已被禁用")
        return user
    finally:
        session.close()
