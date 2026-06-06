"""
============================================================
  auth.py - ZeroAgent 用户认证模块
  JWT 无状态认证 + 用户数据隔离
============================================================
"""
import os
import json
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import jwt

# 配置
USERS_FILE = os.path.join(os.path.dirname(__file__), "data", "users.json")
JWT_SECRET = os.environ.get("JWT_SECRET", "zeroagent-jwt-secret-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

# 确保目录存在
os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)


def _load_users() -> dict:
    """加载所有用户数据"""
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_users(users: dict):
    """保存用户数据"""
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def _hash_password(password: str) -> str:
    """SHA256 哈希密码"""
    return hashlib.sha256(password.encode()).hexdigest()


def register(username: str, password: str) -> dict:
    """
    注册新用户
    返回: {"success": True/False, "message": "..."}
    """
    username = username.strip()
    if not username or not password:
        return {"success": False, "message": "用户名和密码不能为空"}

    if len(username) < 2 or len(username) > 30:
        return {"success": False, "message": "用户名长度需在2-30个字符之间"}

    if len(password) < 6:
        return {"success": False, "message": "密码长度至少6位"}

    users = _load_users()
    if username in users:
        return {"success": False, "message": "用户名已存在"}

    users[username] = {
        "password_hash": _hash_password(password),
        "created_at": datetime.now().isoformat(),
        "uid": str(uuid.uuid4())[:8],
    }
    _save_users(users)

    # 创建用户专属数据目录
    user_data_dir = os.path.join(
        os.path.dirname(__file__), "data", "users", username
    )
    os.makedirs(user_data_dir, exist_ok=True)

    # 初始化该用户的项目管理数据库表结构
    try:
        from tools.project_tools import _init_db
        _init_db(username)
    except Exception:
        pass  # 如果导入失败不影响注册

    return {"success": True, "message": f"用户 '{username}' 注册成功"}


def login(username: str, password: str) -> dict:
    """
    用户登录，成功返回 JWT token
    返回: {"success": True/False, "message": "...", "token": "...", "username": "..."}
    """
    username = username.strip()
    users = _load_users()

    if username not in users:
        return {"success": False, "message": "用户名或密码错误"}

    if users[username]["password_hash"] != _hash_password(password):
        return {"success": False, "message": "用户名或密码错误"}

    # 生成 JWT
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": username,
        "uid": users[username]["uid"],
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return {
        "success": True,
        "message": "登录成功",
        "token": token,
        "username": username,
    }


def verify_token(token: str) -> dict:
    """
    验证 JWT token
    返回: {"valid": True/False, "username": "...", "message": "..."}
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {
            "valid": True,
            "username": payload["sub"],
            "uid": payload["uid"],
        }
    except jwt.ExpiredSignatureError:
        return {"valid": False, "message": "token 已过期，请重新登录"}
    except jwt.InvalidTokenError:
        return {"valid": False, "message": "无效的 token"}
