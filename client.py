"""
============================================================
  ZeroAgent Client - 云端大脑客户端
  命令行交互式客户端，用于连接 ZeroAgent 服务器
============================================================
"""
import json
import requests
import sys
import re

# 服务器地址（可按需修改）
SERVER_URL = "http://127.0.0.1:8000"


def print_banner():
    print("=" * 50)
    print("  ZeroAgent 云端大脑 - 客户端")
    print("  输入消息开始对话，输入 exit 退出")
    print("=" * 50)


def get_server_info():
    """获取服务器信息"""
    try:
        resp = requests.get(f"{SERVER_URL}/", timeout=5)
        return resp.json()
    except Exception:
        return None


def chat_loop(user_id: str):
    """对话循环"""
    session_id = None
    print(f"\n[用户] {user_id}")
    print("[提示] 输入 'exit' 退出，输入 'new' 开启新会话\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue

        if user_input.lower() == "exit":
            print("再见！")
            break

        if user_input.lower() == "new":
            session_id = None
            print("[系统] 已开启新会话\n")
            continue

        # 发送请求
        try:
            payload = {
                "user_id": user_id,
                "message": user_input,
            }
            if session_id:
                payload["session_id"] = session_id

            resp = requests.post(
                f"{SERVER_URL}/chat",
                json=payload,
                timeout=120,  # 长超时，等待 AI 回复
            )

            if resp.status_code != 200:
                print(f"[错误] 服务器返回 {resp.status_code}: {resp.text}")
                continue

            data = resp.json()
            session_id = data["session_id"]
            reply = data["reply"]

            # 过滤 emoji（Windows 兼容）
            safe_reply = re.sub(r'[\U0001F300-\U0001F9FF\u2600-\u27BF\u2700-\u27BF]', '', reply)
            print(f"\nAI: {safe_reply}\n")

        except requests.exceptions.ConnectionError:
            print(f"\n[错误] 无法连接到服务器 {SERVER_URL}")
            print("请确保服务器已启动: python server.py\n")
        except requests.exceptions.Timeout:
            print("\n[错误] 请求超时，请稍后重试\n")
        except Exception as e:
            print(f"\n[错误] {e}\n")


def main():
    print_banner()

    # 检查服务器是否在线
    info = get_server_info()
    if info:
        print(f"[服务器] 在线 | 工具: {info.get('tools_loaded', '?')} 个 | 会话: {info.get('active_sessions', 0)} 个")
    else:
        print(f"[警告] 无法连接到 {SERVER_URL}，请确保服务器已启动")
        print(f"       启动命令: python server.py\n")

    # 获取用户 ID
    user_id = input("请输入你的用户名 (默认: default): ").strip() or "default"

    # 进入对话循环
    chat_loop(user_id)


if __name__ == "__main__":
    main()
