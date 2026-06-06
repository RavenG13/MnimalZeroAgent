"""
============================================================
  file_storage.py - 用户文件存储管理
  每个用户分配 1GB 存储空间，提供文件上传/下载/列表/删除 API
============================================================
"""
import os
import json
import shutil
import time
from datetime import datetime

# 用户文件存储根目录
STORAGE_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "user_files")
os.makedirs(STORAGE_ROOT, exist_ok=True)

# 每个用户的存储限额（字节）
MAX_STORAGE_PER_USER = 1 * 1024 * 1024 * 1024  # 1GB


def _get_user_storage_dir(username: str) -> str:
    """获取某个用户的文件存储目录"""
    d = os.path.join(STORAGE_ROOT, username)
    os.makedirs(d, exist_ok=True)
    return d


def get_user_storage_usage(username: str) -> dict:
    """
    获取用户的存储使用情况
    返回: {"used_bytes": int, "used_mb": float, "max_bytes": int, "max_mb": int, "percent": float, "file_count": int}
    """
    user_dir = _get_user_storage_dir(username)
    total_size = 0
    file_count = 0
    
    for root, dirs, files in os.walk(user_dir):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total_size += os.path.getsize(fp)
                file_count += 1
            except OSError:
                pass
    
    return {
        "used_bytes": total_size,
        "used_mb": round(total_size / (1024 * 1024), 2),
        "max_bytes": MAX_STORAGE_PER_USER,
        "max_mb": MAX_STORAGE_PER_USER / (1024 * 1024),
        "percent": round(total_size / MAX_STORAGE_PER_USER * 100, 2),
        "file_count": file_count,
    }


def list_user_files(username: str, path: str = "") -> list[dict]:
    """
    列出用户目录下的文件和文件夹
    path: 子路径，空字符串表示根目录
    """
    user_dir = _get_user_storage_dir(username)
    target_dir = os.path.normpath(os.path.join(user_dir, path))
    
    # 安全校验：不能越界
    if not target_dir.startswith(os.path.normpath(user_dir)):
        return [{"error": "不允许访问其他目录"}]
    
    if not os.path.exists(target_dir):
        return []
    
    items = []
    for item in os.listdir(target_dir):
        item_path = os.path.join(target_dir, item)
        stat = os.stat(item_path)
        rel_path = os.path.relpath(item_path, user_dir).replace("\\", "/")
        items.append({
            "name": item,
            "path": rel_path,
            "type": "dir" if os.path.isdir(item_path) else "file",
            "size": stat.st_size if os.path.isfile(item_path) else 0,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    
    # 排序：目录在前，按名称排序
    items.sort(key=lambda x: (0 if x["type"] == "dir" else 1, x["name"]))
    return items


def save_user_file(username: str, file_path: str, content: str = None, binary_content: bytes = None) -> dict:
    """
    保存文件到用户存储空间
    file_path: 相对于用户存储根目录的路径
    """
    user_dir = _get_user_storage_dir(username)
    abs_path = os.path.normpath(os.path.join(user_dir, file_path))
    
    # 安全校验
    if not abs_path.startswith(os.path.normpath(user_dir)):
        return {"success": False, "message": "不允许访问其他目录"}
    
    # 检查存储限额
    usage = get_user_storage_usage(username)
    new_size = len(content or "") if content else len(binary_content or b"")
    if usage["used_bytes"] + new_size > MAX_STORAGE_PER_USER:
        return {"success": False, "message": f"存储空间不足 (已用 {usage['used_mb']}MB / 1GB)"}
    
    # 创建目录
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    
    try:
        if binary_content is not None:
            with open(abs_path, "wb") as f:
                f.write(binary_content)
        else:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content or "")
        return {"success": True, "message": f"文件 '{file_path}' 保存成功"}
    except Exception as e:
        return {"success": False, "message": f"保存失败: {e}"}


def read_user_file(username: str, file_path: str, binary: bool = False) -> dict:
    """
    读取用户存储空间中的文件
    """
    user_dir = _get_user_storage_dir(username)
    abs_path = os.path.normpath(os.path.join(user_dir, file_path))
    
    if not abs_path.startswith(os.path.normpath(user_dir)):
        return {"success": False, "message": "不允许访问其他目录"}
    
    if not os.path.isfile(abs_path):
        return {"success": False, "message": "文件不存在"}
    
    try:
        if binary:
            with open(abs_path, "rb") as f:
                return {"success": True, "content": f.read(), "name": os.path.basename(file_path)}
        else:
            with open(abs_path, "r", encoding="utf-8") as f:
                return {"success": True, "content": f.read(), "name": os.path.basename(file_path)}
    except UnicodeDecodeError:
        # 非文本文件，以二进制读取
        with open(abs_path, "rb") as f:
            return {"success": True, "content": f.read(), "name": os.path.basename(file_path), "binary": True}
    except Exception as e:
        return {"success": False, "message": f"读取失败: {e}"}


def delete_user_file(username: str, file_path: str) -> dict:
    """删除文件或目录"""
    user_dir = _get_user_storage_dir(username)
    abs_path = os.path.normpath(os.path.join(user_dir, file_path))
    
    if not abs_path.startswith(os.path.normpath(user_dir)):
        return {"success": False, "message": "不允许访问其他目录"}
    
    if not os.path.exists(abs_path):
        return {"success": False, "message": "文件或目录不存在"}
    
    try:
        if os.path.isdir(abs_path):
            shutil.rmtree(abs_path)
        else:
            os.remove(abs_path)
        return {"success": True, "message": f"'{file_path}' 已删除"}
    except Exception as e:
        return {"success": False, "message": f"删除失败: {e}"}
