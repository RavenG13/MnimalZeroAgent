"""
============================================================
  caldav_sync.py - CalDAV 日历同步模块（重写版：UID匹配 + 时间冲突解决）
  与 Radicale / Baïkal / SabreDAV 等标准 CalDAV 服务器双向同步。
  核心改进：
  - 统一 UID 机制：本地和远程用同一个 UUID 标识事件
  - last_modified 冲突解决：取最新修改时间的版本
  - caldav_etag 检测远程变化
============================================================
"""
import os
import re
import uuid as _uuid
import threading
import requests
from datetime import datetime
from typing import Optional
from xml.etree import ElementTree as ET

# ---- 用户隔离 ----
_thread_local = threading.local()

def set_current_user(username: str):
    _thread_local.username = username

def get_current_user() -> str:
    return getattr(_thread_local, "username", None) or "default"


# ---- 数据库连接 ----
_tools_dir = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(os.path.dirname(_tools_dir), "data", "users")
SETTINGS_DIR = os.path.join(os.path.dirname(_tools_dir), "data", "settings")


def _get_conn(username: str = None):
    import sqlite3
    username = username or get_current_user()
    db_path = os.path.join(DB_DIR, username, "projects.db")
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.text_factory = str
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _load_settings(username: str) -> dict:
    safe = username.replace("/", "_").replace("\\", "_")
    path = os.path.join(SETTINGS_DIR, f"{safe}.json")
    if os.path.exists(path):
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# ============================================================
#  iCalendar 格式生成/解析
# ============================================================

def _event_to_vevent(ev: dict) -> str:
    """将本地 schedule 事件转为 VEVENT 格式"""
    uid = ev.get("uid", "") or str(_uuid.uuid4())
    dtstart = ""
    dtend = ""
    summary = ev.get("content", "")
    priority_map = {"高": "1", "中": "5", "低": "9"}
    pri = priority_map.get(ev.get("priority", "中"), "5")

    date = ev.get("date", "")
    start_time = ev.get("start_time", "")
    end_time = ev.get("end_time", "")

    # start_time 可能是 "09:00" 或 "2026-08-05 09:00"
    def _extract_hm(t):
        if not t:
            return None
        parts = t.split(" ")[-1].split(":")
        try:
            return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        except Exception:
            return None

    hm_start = _extract_hm(start_time)
    hm_end = _extract_hm(end_time)

    if hm_start and date:
        hh, mm = hm_start
        dtstart = f"DTSTART:{date.replace('-','')}T{hh:02d}{mm:02d}00"
        if hm_end:
            eh, em = hm_end
            dtend = f"DTEND:{date.replace('-','')}T{eh:02d}{em:02d}00"
        else:
            dtend = f"DTEND:{date.replace('-','')}T{(hh+1):02d}{mm:02d}00"
    else:
        dtstart = f"DTSTART;VALUE=DATE:{date.replace('-','')}"

    lm = ev.get("last_modified", "")
    if lm:
        try:
            dt = datetime.strptime(lm, "%Y-%m-%d %H:%M:%S")
            lastmod = f"LAST-MODIFIED:{dt.strftime('%Y%m%dT%H%M%S')}"
        except Exception:
            lastmod = ""
    else:
        lastmod = ""

    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{now}", dtstart]
    if dtend:
        lines.append(dtend)
    lines += [f"SUMMARY:{summary}", f"PRIORITY:{pri}"]
    if lastmod:
        lines.append(lastmod)
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def _vevent_to_dict(vevent: str) -> Optional[dict]:
    """将 VEVENT 字符串解析为字典"""
    # 统一换行符（服务器可能用 \n 或 \r\n）
    vevent = vevent.replace("\r\n", "\n")
    def _get(key):
        for line in vevent.split("\n"):
            if line.startswith(key + ":") or line.startswith(key + ";"):
                return line[line.index(":")+1:].strip()
        return ""

    uid = _get("UID")
    summary = _get("SUMMARY")
    if not summary:
        return None

    dtstart_raw = _get("DTSTART")
    date = ""
    start_time = ""
    if "T" in dtstart_raw:
        try:
            date = f"{dtstart_raw[:4]}-{dtstart_raw[4:6]}-{dtstart_raw[6:8]}"
            start_time = f"{dtstart_raw[9:11]}:{dtstart_raw[11:13]}"
        except Exception:
            pass
    elif dtstart_raw:
        try:
            date = f"{dtstart_raw[:4]}-{dtstart_raw[4:6]}-{dtstart_raw[6:8]}"
        except Exception:
            pass

    dtend_raw = _get("DTEND")
    end_time = ""
    if dtend_raw and "T" in dtend_raw:
        try:
            end_time = f"{dtend_raw[9:11]}:{dtend_raw[11:13]}"
        except Exception:
            pass

    pri_raw = _get("PRIORITY")
    pri_map = {"1": "高", "5": "中", "9": "低"}
    priority = pri_map.get(pri_raw, "中")

    lm_raw = _get("LAST-MODIFIED")
    last_modified = ""
    if lm_raw and "T" in lm_raw:
        try:
            last_modified = f"{lm_raw[:4]}-{lm_raw[4:6]}-{lm_raw[6:8]} {lm_raw[9:11]}:{lm_raw[11:13]}:{lm_raw[13:15]}"
        except Exception:
            pass

    return {
        "uid": uid,
        "date": date,
        "content": summary,
        "start_time": start_time,
        "end_time": end_time,
        "priority": priority,
        "last_modified": last_modified,
    }


# ============================================================
#  CalDAV 客户端
# ============================================================

def _caldav_request(method, url, username, password, headers=None, data=None, timeout=30):
    h = {"Content-Type": "application/xml; charset=utf-8"}
    if headers:
        h.update(headers)
    auth = (username, password) if username else None
    return requests.request(method, url, auth=auth, headers=h, data=data, timeout=timeout)


def _try_as_calendar(url, username, password):
    """尝试将 URL 当作日历直接 PROPFIND"""
    if not url.endswith("/"):
        url += "/"
    body = '<?xml version="1.0" encoding="UTF-8"?>' \
           '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">' \
           '<d:prop><d:displayname/><d:resourcetype/></d:prop></d:propfind>'
    resp = _caldav_request("PROPFIND", url, username, password, {"Depth": "0"}, body)
    if resp.status_code not in (200, 207):
        return []
    ns = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
    root = ET.fromstring(resp.text)
    for r in root.findall("d:response", ns):
        rt = r.find(".//d:resourcetype", ns)
        if rt is not None and rt.find("c:calendar", ns) is not None:
            dn = r.find(".//d:displayname", ns)
            return [{"url": url, "name": dn.text if dn is not None else "Calendar"}]
    return []


def _list_calendars(url, username, password):
    if not url.endswith("/"):
        url += "/"
    body = '<?xml version="1.0" encoding="UTF-8"?>' \
           '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">' \
           '<d:prop><d:displayname/><d:resourcetype/></d:prop></d:propfind>'
    resp = _caldav_request("PROPFIND", url, username, password, {"Depth": "1"}, body)
    if resp.status_code not in (200, 207):
        return []
    ns = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
    root = ET.fromstring(resp.text)
    cals = []
    for r in root.findall("d:response", ns):
        href = r.find("d:href", ns)
        if href is None:
            continue
        rt = r.find(".//d:resourcetype", ns)
        if rt is not None and rt.find("c:calendar", ns) is not None:
            dn = r.find(".//d:displayname", ns)
            name = dn.text if dn is not None else href.text
            h = href.text
            if not h.startswith("http"):
                from urllib.parse import urljoin
                h = urljoin(url, h)
            cals.append({"url": h, "name": name})
    return cals


def discover_calendars(base_url, username, password):
    """发现 CalDAV 服务器上的日历（支持 Baïkal / Radicale）"""
    if not base_url.endswith("/"):
        base_url += "/"

    # 策略1：直接探测
    try:
        cals = _try_as_calendar(base_url, username, password)
        if cals:
            return cals
    except Exception:
        pass

    # 策略2：Baïkal principal 路径
    from urllib.parse import urljoin
    propfind_body = '<?xml version="1.0" encoding="UTF-8"?>' \
                    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">' \
                    '<d:prop><d:displayname/><c:calendar-home-set/></d:prop></d:propfind>'

    entry_urls = [base_url]
    if "/dav.php" in base_url and username:
        dav_prefix = base_url.split("/dav.php")[0] + "/dav.php/"
        entry_urls.insert(0, dav_prefix + "principals/" + username + "/")
        entry_urls.insert(0, dav_prefix + "calendars/" + username + "/")

    for entry_url in entry_urls:
        try:
            resp = _caldav_request("PROPFIND", entry_url, username, password, {"Depth": "0"}, propfind_body)
            if resp.status_code in (200, 207):
                ns = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
                root = ET.fromstring(resp.text)
                for elem in root.iter():
                    if elem.tag.endswith("calendar-home-set"):
                        he = elem.find("d:href", ns)
                        if he is not None and he.text:
                            home_url = urljoin(entry_url, he.text) if he.text.startswith("/") else he.text
                            cals = _list_calendars(home_url, username, password)
                            if cals:
                                return cals
        except Exception:
            continue

    # 策略3：直接列出
    for entry_url in entry_urls:
        try:
            cals = _list_calendars(entry_url, username, password)
            if cals:
                return cals
        except Exception:
            continue

    return []


def test_connection(base_url, username, password):
    try:
        cals = discover_calendars(base_url, username, password)
        return {"success": True, "message": f"连接成功，发现 {len(cals)} 个日历", "calendars": cals}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ============================================================
#  核心：按 UID 匹配 + 时间冲突解决的双向同步
# ============================================================

def _get_caldav_config(username):
    settings = _load_settings(username)
    url = settings.get("caldav_url", "")
    user = settings.get("caldav_username", "")
    pw = settings.get("caldav_password", "")
    if not url:
        return None
    return {"url": url, "username": user, "password": pw}


def _fetch_remote_events(cal_url, username, password):
    """从 CalDAV 拉取所有事件（含 UID + ETag + last-modified）"""
    if not cal_url.endswith("/"):
        cal_url += "/"

    report_body = '<?xml version="1.0" encoding="UTF-8"?>' \
                  '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">' \
                  '<d:prop><d:getetag/><c:calendar-data/></d:prop>' \
                  '<c:filter><c:comp-filter name="VCALENDAR">' \
                  '<c:comp-filter name="VEVENT"/></c:comp-filter></c:filter>' \
                  '</c:calendar-query>'

    resp = _caldav_request("REPORT", cal_url, username, password, {"Depth": "1"}, report_body)
    if resp.status_code not in (200, 207):
        raise Exception(f"REPORT 失败: {resp.status_code}")

    ns = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
    root = ET.fromstring(resp.text)
    events = []

    for response in root.findall("d:response", ns):
        href = response.find("d:href", ns)
        href_text = href.text if href is not None else ""
        etag_el = response.find(".//d:getetag", ns)
        etag = etag_el.text.strip('"') if etag_el is not None and etag_el.text else ""
        cal_data = response.find(".//c:calendar-data", ns)
        if cal_data is not None and cal_data.text:
            for m in re.finditer(r"BEGIN:VEVENT[\r\n]+(.*?)END:VEVENT", cal_data.text, re.DOTALL):
                ev = _vevent_to_dict("BEGIN:VEVENT\r\n" + m.group(1) + "END:VEVENT")
                if ev:
                    ev["_remote_href"] = href_text
                    ev["_etag"] = etag
                    events.append(ev)

    return events


def _parse_last_modified(lm_str):
    """解析 last_modified 字符串为可比较的 datetime"""
    if not lm_str:
        return datetime.min
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(lm_str, fmt)
        except Exception:
            pass
    return datetime.min


def sync_calendar(username=None):
    """
    双向同步（含删除同步）：5 阶段处理。
    
    阶段1: 本地删除 → 远程（墓碑表处理）
      墓碑 UID 在远程存在 → DELETE 远程 → 清墓碑
      墓碑 UID 远程不存在 → 直接清墓碑
    阶段2: 远程删除 → 本地
      本地有、远程没有、caldav_etag 非空 → 删除本地（远程被删）
    阶段3: 本地新建 → 远程
      本地有、远程没有、caldav_etag 为空 → 推送（真新事件）
    阶段4: 两端都有 → 比较 last_modified 取最新
    阶段5: 远程有、本地没有 → 拉取创建
    """
    username = username or get_current_user()
    config = _get_caldav_config(username)
    if not config:
        return {"success": False, "message": "未配置 CalDAV 服务器"}

    try:
        calendars = discover_calendars(config["url"], config["username"], config["password"])
        if not calendars:
            return {"success": False, "message": "未找到日历"}
        cal_url = calendars[0]["url"]
        if not cal_url.endswith("/"):
            cal_url += "/"

        # 拉取远程
        remote_events = _fetch_remote_events(cal_url, config["username"], config["password"])
        remote_by_uid = {}
        for rev in remote_events:
            if rev.get("uid"):
                remote_by_uid[rev["uid"]] = rev

        # 获取本地 + 墓碑
        conn = _get_conn(username)
        if not conn:
            return {"success": False, "message": "本地数据库不存在"}
        try:
            local_rows = conn.execute("SELECT * FROM schedule").fetchall()
            local_events = [dict(r) for r in local_rows]
            # 确保墓碑表存在
            conn.execute("""CREATE TABLE IF NOT EXISTS caldav_deleted (
                                uid TEXT PRIMARY KEY,
                                deleted_at TEXT DEFAULT (datetime('now','localtime'))
                            )""")
            deleted_rows = conn.execute("SELECT uid FROM caldav_deleted").fetchall()
            deleted_uids = {r["uid"] for r in deleted_rows}
        finally:
            conn.close()

        local_by_uid = {}
        for lev in local_events:
            uid = lev.get("uid", "")
            if uid:
                local_by_uid[uid] = lev

        pulled = 0
        updated = 0
        pushed = 0
        deleted_remote = 0
        deleted_local = 0

        conn = _get_conn(username)
        try:
            # ===== 阶段1: 本地删除 → 远程 =====
            for uid in list(deleted_uids):
                if uid in remote_by_uid:
                    # 远程存在 → 删除远程
                    delete_url = cal_url + f"{uid}.ics"
                    resp = _caldav_request("DELETE", delete_url,
                                           config["username"], config["password"])
                    if resp.status_code in (200, 204, 404):
                        deleted_remote += 1
                        del remote_by_uid[uid]
                # 清墓碑（无论远程是否存在，墓碑使命完成）
                conn.execute("DELETE FROM caldav_deleted WHERE uid = ?", (uid,))

            # ===== 阶段2: 远程删除 → 本地 =====
            for uid, lev in list(local_by_uid.items()):
                if uid not in remote_by_uid and lev.get("caldav_etag", ""):
                    # 之前已同步过，现在远程没有 → 远程被删 → 删除本地
                    conn.execute("DELETE FROM schedule WHERE uid = ?", (uid,))
                    deleted_local += 1
                    del local_by_uid[uid]

            # ===== 阶段4: 两端都有 → 比较 last_modified =====
            for uid, rev in remote_by_uid.items():
                if uid in local_by_uid:
                    lev = local_by_uid[uid]
                    remote_lm = _parse_last_modified(rev.get("last_modified", ""))
                    local_lm = _parse_last_modified(lev.get("last_modified", ""))
                    if remote_lm > local_lm:
                        # 远程更新 → 更新本地
                        conn.execute(
                            """UPDATE schedule SET date=?, time_slot=?, content=?, priority=?,
                               start_time=?, end_time=?, last_modified=?, caldav_etag=?
                               WHERE uid=?""",
                            (rev.get("date", lev.get("date", "")),
                             rev.get("start_time", lev.get("time_slot", "")),
                             rev.get("content", lev.get("content", "")),
                             rev.get("priority", lev.get("priority", "中")),
                             f"{rev.get('date','')} {rev.get('start_time','')}" if rev.get("start_time") else lev.get("start_time", ""),
                             f"{rev.get('date','')} {rev.get('end_time','')}" if rev.get("end_time") else lev.get("end_time", ""),
                             rev.get("last_modified", ""),
                             rev.get("_etag", ""),
                             uid))
                        updated += 1

            # ===== 阶段5: 远程有、本地没有 → 拉取创建 =====
            for uid, rev in remote_by_uid.items():
                if uid not in local_by_uid:
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute(
                        """INSERT INTO schedule (date, time_slot, content, priority, start_time, end_time, uid, last_modified, caldav_etag)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (rev.get("date", ""),
                         rev.get("start_time", ""),
                         rev.get("content", ""),
                         rev.get("priority", "中"),
                         f"{rev.get('date','')} {rev.get('start_time','')}" if rev.get("start_time") else "",
                         f"{rev.get('date','')} {rev.get('end_time','')}" if rev.get("end_time") else "",
                         uid,
                         rev.get("last_modified", now),
                         rev.get("_etag", "")))
                    pulled += 1
                    local_by_uid[uid] = {"uid": uid, "caldav_etag": rev.get("_etag", "")}
            conn.commit()
        finally:
            conn.close()

        # ===== 阶段3: 本地新建/本地更新 → 推送到远程 =====
        conn = _get_conn(username)
        try:
            local_rows = conn.execute("SELECT * FROM schedule").fetchall()
            local_events = [dict(r) for r in local_rows]
        finally:
            conn.close()

        for lev in local_events:
            uid = lev.get("uid", "")
            if not uid:
                continue
            local_lm = _parse_last_modified(lev.get("last_modified", ""))

            if uid in remote_by_uid:
                remote_lm = _parse_last_modified(remote_by_uid[uid].get("last_modified", ""))
                if local_lm <= remote_lm:
                    continue  # 远程更新或相同，不推送

            # 推送（新建：etag为空；本地更新：本地较新）
            ics = _event_to_vevent(lev)
            ical = f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//ZeroAgent//EN\r\n{ics}\r\nEND:VCALENDAR"
            put_url = cal_url + f"{uid}.ics"
            resp = _caldav_request("PUT", put_url, config["username"], config["password"],
                                   data=ical.encode("utf-8"))
            if resp.status_code in (200, 201, 204):
                pushed += 1
                new_etag = resp.headers.get("ETag", "").strip('"')
                if new_etag:
                    conn2 = _get_conn(username)
                    try:
                        conn2.execute("UPDATE schedule SET caldav_etag = ? WHERE uid = ?", (new_etag, uid))
                        conn2.commit()
                    finally:
                        conn2.close()

        return {
            "success": True,
            "message": "同步完成",
            "pulled": pulled,
            "updated": updated,
            "pushed": pushed,
            "deleted_remote": deleted_remote,
            "deleted_local": deleted_local,
            "remote_count": len(remote_events),
            "local_count": len(local_events),
        }
    except Exception as e:
        return {"success": False, "message": f"同步失败: {e}"}


# ============================================================
#  AI Agent 工具定义
# ============================================================

caldav_tools = [
    {
        "type": "function",
        "function": {
            "name": "calendar_sync",
            "description": "触发本地日历与远程 CalDAV 服务器的双向同步。"
                           "按 UID 匹配事件，last_modified 取最新版本覆盖。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_test_caldav",
            "description": "测试 CalDAV 服务器连接是否正常，并列出可用日历。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def calendar_sync():
    result = sync_calendar()
    if result["success"]:
        return (f"[成功] CalDAV 同步完成\n"
                f"  拉取新事件: {result['pulled']} 条\n"
                f"  更新旧事件: {result['updated']} 条\n"
                f"  推送到远程: {result['pushed']} 条\n"
                f"  远程事件数: {result['remote_count']}\n"
                f"  本地事件数: {result['local_count']}")
    return f"[失败] {result['message']}"


def calendar_test_caldav():
    username = get_current_user()
    config = _get_caldav_config(username)
    if not config:
        return "[提示] 未配置 CalDAV 服务器，请在设置中开启并填写地址/用户名/密码"
    result = test_connection(config["url"], config["username"], config["password"])
    if result["success"]:
        cals = result.get("calendars", [])
        lines = [f"[成功] {result['message']}"]
        for c in cals:
            lines.append(f"  📅 {c['name']} — {c['url']}")
        return "\n".join(lines)
    return f"[失败] {result['message']}"
