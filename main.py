import tkinter as tk
from tkinter import ttk, messagebox, Toplevel, Label
from datetime import datetime, timedelta
import calendar
import json
import sqlite3
import re
import random
import os
import shutil
import urllib.error
import urllib.request
import base64
import ctypes
from ctypes import wintypes
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import threading
import time
from plyer import notification
plt.rcParams["font.family"] = ["SimHei", "Microsoft YaHei"]

FONT_FAMILY = "微软雅黑"
COLORS = {
    "bg": "#f4f7fb",
    "panel": "#ffffff",
    "sidebar": "#172033",
    "sidebar_text": "#dbe4f0",
    "primary": "#2563eb",
    "primary_hover": "#1d4ed8",
    "success": "#16a34a",
    "warning": "#d97706",
    "danger": "#dc2626",
    "muted": "#64748b",
    "border": "#d8e0ea",
    "text": "#1f2937",
}

POINTS_PER_POMODORO = 25
SCORE_SERVER_URL = "http://127.0.0.1:8000"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GLOBAL_DB_PATH = os.path.join(BASE_DIR, "app_global.db")
ACCOUNTS_DIR = os.path.join(BASE_DIR, "accounts")
HONOR_LEVELS = [
    (0, "起步者"),
    (50, "坚持新星"),
    (150, "专注学徒"),
    (350, "恒心行者"),
    (700, "毅力达人"),
    (1200, "破局先锋"),
    (2000, "长期主义者"),
    (3200, "自律大师"),
    (5000, "登峰者"),
]
PERSEVERANCE_QUOTES = [
    ("锲而不舍，金石可镂。", "《荀子》"),
    ("路漫漫其修远兮，吾将上下而求索。", "屈原"),
    ("千磨万击还坚劲，任尔东西南北风。", "郑燮"),
    ("宝剑锋从磨砺出，梅花香自苦寒来。", "古训"),
    ("绳锯木断，水滴石穿。", "古训"),
    ("不积跬步，无以至千里；不积小流，无以成江海。", "《荀子》"),
    ("合抱之木，生于毫末；九层之台，起于累土。", "《道德经》"),
    ("行百里者半九十。", "《战国策》"),
    ("精诚所至，金石为开。", "王充"),
    ("伟大的作品不是靠力量，而是靠坚持来完成的。", "约翰逊"),
]


# ====================== Windows DPAPI Token 加密 ======================
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_protect(data: bytes, description: str = "SmartSchedule-v2") -> str:
    """使用 Windows DPAPI 加密数据（仅当前登录用户可解密）"""
    blob_in = _DATA_BLOB()
    blob_in.cbData = len(data)
    blob_in.pbData = ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char))
    blob_out = _DATA_BLOB()

    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        description.encode("utf-16le"),
        None, None, None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(blob_out),
    ):
        raise OSError("DPAPI 加密失败")
    result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return base64.b64encode(result).decode("ascii")


def _dpapi_unprotect(encrypted: str) -> bytes:
    """使用 Windows DPAPI 解密数据"""
    ciphertext = base64.b64decode(encrypted.encode("ascii"))
    blob_in = _DATA_BLOB()
    blob_in.cbData = len(ciphertext)
    blob_in.pbData = ctypes.cast(ctypes.create_string_buffer(ciphertext), ctypes.POINTER(ctypes.c_char))
    blob_out = _DATA_BLOB()

    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None, None, None, None,
        0x01,
        ctypes.byref(blob_out),
    ):
        raise OSError("DPAPI 解密失败（可能是换了用户或系统）")
    result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return result


def get_honor_profile(points):
    current_threshold, current_title = HONOR_LEVELS[0]
    next_level = None
    for index, (threshold, title) in enumerate(HONOR_LEVELS):
        if points >= threshold:
            current_threshold, current_title = threshold, title
            next_level = HONOR_LEVELS[index + 1] if index + 1 < len(HONOR_LEVELS) else None
        else:
            next_level = threshold, title
            break

    if next_level:
        next_threshold, next_title = next_level
        points_to_next = next_threshold - points
    else:
        next_threshold, next_title = None, None
        points_to_next = 0

    return {
        "points": points,
        "title": current_title,
        "threshold": current_threshold,
        "next_title": next_title,
        "next_threshold": next_threshold,
        "points_to_next": points_to_next,
    }


def score_server_request(method, path, data=None, token=None, timeout=5):
    url = SCORE_SERVER_URL + path
    body = None
    headers = {"Content-Type": "application/json"}
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            error_data = json.loads(exc.read().decode("utf-8"))
        except Exception:
            error_data = {"error": f"服务器返回 HTTP {exc.code}"}
        raise RuntimeError(error_data.get("error", "服务器请求失败")) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("无法连接积分排行服务器，请先运行 score_server.py") from exc
    except TimeoutError as exc:
        raise RuntimeError("连接积分排行服务器超时") from exc


def ensure_score_server_running():
    try:
        score_server_request("GET", "/health", timeout=1)
        return True
    except RuntimeError:
        pass

    try:
        import score_server
        server_thread = threading.Thread(target=score_server.run_server, daemon=True)
        server_thread.start()
        time.sleep(0.5)
        score_server_request("GET", "/health", timeout=2)
        return True
    except Exception:
        return False


def safe_account_name(username):
    safe_name = re.sub(r"[^A-Za-z0-9_]", "_", username.strip())
    return safe_name or "user"


def get_account_dir(username):
    return os.path.join(ACCOUNTS_DIR, safe_account_name(username))


def format_duration(seconds):
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}秒"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{seconds}秒" if seconds else f"{minutes}分"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}时{minutes}分" if minutes else f"{hours}时"

# ====================== 核心实体类 ======================
class Task:
    def __init__(self, title, description="", due_date=None, priority=3, scheduled_date=None):
        self.id = datetime.now().strftime("%Y%m%d%H%M%S%f")
        self.title = title
        self.description = description
        self.due_date = due_date
        self.scheduled_date = scheduled_date
        self.priority = priority  # 1最高，3最低
        self.completed = False
        self.created_at = datetime.now()
        self.finished_at = None
        self.cost_sec = 0  # 改为秒
        self.target_pomodoros = 1
        self.completed_pomodoros = 0

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "due_date": self.due_date.strftime("%Y-%m-%d %H:%M") if self.due_date else None,
            "scheduled_date": self.scheduled_date.strftime("%Y-%m-%d") if self.scheduled_date else None,
            "priority": self.priority,
            "completed": self.completed,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": self.finished_at.strftime("%Y-%m-%d %H:%M:%S") if self.finished_at else None,
            "cost_sec": self.cost_sec,
            "target_pomodoros": self.target_pomodoros,
            "completed_pomodoros": self.completed_pomodoros
        }

    @staticmethod
    def parse_saved_datetime(value):
        if not value:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                pass
        return None

    @staticmethod
    def from_dict(data):
        task = Task(data["title"], data["description"])
        task.id = data["id"]
        task.due_date = Task.parse_saved_datetime(data.get("due_date"))
        task.scheduled_date = Task.parse_saved_datetime(data.get("scheduled_date"))
        task.priority = data["priority"]
        task.completed = data["completed"]
        task.created_at = datetime.strptime(data["created_at"], "%Y-%m-%d %H:%M:%S")
        if data.get("finished_at"):
            task.finished_at = datetime.strptime(data["finished_at"], "%Y-%m-%d %H:%M:%S")
        task.cost_sec = data.get("cost_sec", 0)
        task.target_pomodoros = data.get("target_pomodoros", 1)
        task.completed_pomodoros = data.get("completed_pomodoros", 0)
        return task

    def is_pending(self, today=None):
        if self.completed or not self.scheduled_date:
            return False
        today = today or datetime.now().date()
        return self.scheduled_date.date() > today

    def calculate_priority_score(self):
        now = datetime.now()
        urgency = 0
        if self.due_date:
            hours_left = (self.due_date - now).total_seconds() / 3600
            if hours_left <= 0:
                urgency = 100
            elif hours_left <= 24:
                urgency = 80
            elif hours_left <= 72:
                urgency = 50
            elif hours_left <= 168:
                urgency = 20
            else:
                urgency = 10
        else:
            urgency = 5
        importance = (4 - self.priority) * 25
        status_penalty = 30 if not self.completed else 0
        return urgency + importance + status_penalty

    def check_pomodoro_complete(self):
        if self.completed_pomodoros >= self.target_pomodoros and not self.completed:
            self.completed = True
            self.finished_at = datetime.now()
            self.cost_sec = int((self.finished_at - self.created_at).total_seconds())
            return True
        return False

class Schedule:
    def __init__(self):
        self.tasks = []

    def add_task(self, task):
        self.tasks.append(task)

    def remove_task(self, tid):
        self.tasks = [t for t in self.tasks if t.id != tid]

    def get_task_by_id(self, tid):
        for task in self.tasks:
            if task.id == tid:
                return task
        return None

    @staticmethod
    def priority_sort_key(task):
        due_date = task.due_date or datetime.max
        return task.priority, due_date, task.created_at

    def smart_sort(self):
        self.tasks.sort(key=self.priority_sort_key)

    def get_statistics(self):
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks if t.completed)
        completion_rate = (completed / total * 100) if total > 0 else 0
        priority_stats = {1: 0, 2: 0, 3: 0}
        for t in self.tasks:
            if t.completed:
                priority_stats[t.priority] += 1
        return {
            "total": total,
            "completed": completed,
            "completion_rate": completion_rate,
            "priority_stats": priority_stats
        }

    def get_uncompleted_tasks(self):
        return sorted(
            [t for t in self.tasks if not t.completed and not t.is_pending()],
            key=self.priority_sort_key
        )

    def get_completed_tasks(self):
        return sorted(
            [t for t in self.tasks if t.completed],
            key=lambda t: t.finished_at or t.created_at,
            reverse=True
        )

    def get_pending_tasks(self):
        return sorted(
            [t for t in self.tasks if t.is_pending()],
            key=lambda t: (t.scheduled_date, t.priority, t.created_at)
        )

# ====================== 数据持久化 ======================
class DataManager:
    def __init__(self, username=None):
        self.username = username
        self.is_account_data = bool(username)
        if username:
            account_dir = get_account_dir(username)
            os.makedirs(account_dir, exist_ok=True)
            self.json_path = os.path.join(account_dir, "tasks.json")
            self.db_path = os.path.join(account_dir, "pomodoro.db")
        else:
            self.json_path = None
            self.db_path = None
        self.init_db()

    def init_db(self):
        if not self.db_path:
            return
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS pomodoro_records
        (id INTEGER PRIMARY KEY AUTOINCREMENT,
         start_time TEXT,
         end_time TEXT,
         phase TEXT,
         duration INT,
         task_id TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS task_history
        (id INTEGER PRIMARY KEY AUTOINCREMENT,
         task_id TEXT,
         title TEXT,
         completed_at TEXT,
         cost_sec INT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS user_profile
        (key TEXT PRIMARY KEY,
         value TEXT NOT NULL)''')
        cur.execute("SELECT value FROM user_profile WHERE key='total_points'")
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO user_profile (key, value) VALUES (?, ?)",
                ("total_points", "0")
            )
        conn.commit()
        conn.close()

    def set_profile_value(self, key, value):
        if not self.db_path:
            return
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO user_profile (key, value) VALUES (?, ?)",
            (key, str(value))
        )
        conn.commit()
        conn.close()

    def get_profile_value(self, key, default=None):
        if not self.db_path:
            return default
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT value FROM user_profile WHERE key=?", (key,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else default

    def clear_profile_value(self, key):
        if not self.db_path:
            return
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM user_profile WHERE key=?", (key,))
        conn.commit()
        conn.close()

    def save_tasks(self, tasks_data):
        if not self.json_path:
            return
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(tasks_data, f, ensure_ascii=False, indent=2)

    def load_tasks(self):
        if not self.json_path:
            return []
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []

    def save_pomodoro(self, start_time, end_time, phase, task_id=None):
        if not self.db_path:
            return False, None, None
        duration = int((end_time - start_time).total_seconds())
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("INSERT INTO pomodoro_records (start_time, end_time, phase, duration, task_id) VALUES (?,?,?,?,?)",
                    (start_time.strftime("%Y-%m-%d %H:%M:%S"),
                     end_time.strftime("%Y-%m-%d %H:%M:%S"),
                     phase, duration, task_id))
        conn.commit()
        conn.close()
        points_update = self.add_pomodoro_points() if phase == 'work' else None
        if phase == 'work' and task_id:
            updated, task_data = self.update_task_pomodoro_count(task_id)
            return updated, task_data, points_update
        return False, None, points_update

    def get_total_points(self):
        try:
            return int(self.get_profile_value("total_points", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def get_points_profile(self):
        return get_honor_profile(self.get_total_points())

    def add_pomodoro_points(self):
        old_profile = self.get_points_profile()
        new_points = old_profile["points"] + POINTS_PER_POMODORO
        self.set_profile_value("total_points", new_points)

        new_profile = get_honor_profile(new_points)
        new_profile.update({
            "earned": POINTS_PER_POMODORO,
            "old_points": old_profile["points"],
            "old_title": old_profile["title"],
            "leveled_up": old_profile["title"] != new_profile["title"],
        })
        return new_profile

    def update_task_pomodoro_count(self, task_id):
        if not self.json_path:
            return False, None
        tasks_data = self.load_tasks()
        updated = False
        target_task_data = None
        index = -1

        for i, task_data in enumerate(tasks_data):
            if task_data['id'] == task_id and not task_data['completed']:
                task_data['completed_pomodoros'] = task_data.get('completed_pomodoros', 0) + 1
                target = task_data.get('target_pomodoros', 1)

                if task_data['completed_pomodoros'] >= target:
                    task_data['completed'] = True
                    task_data['finished_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    created_at = datetime.strptime(task_data['created_at'], "%Y-%m-%d %H:%M:%S")
                    task_data['cost_sec'] = int((datetime.now() - created_at).total_seconds())

                tasks_data[i] = task_data
                updated = True
                target_task_data = task_data
                index = i
                break

        if updated:
            self.save_tasks(tasks_data)

        # 返回更新后的数据，让计时器去刷新内存
        return updated, target_task_data

    def save_task_history(self, task):
        if not self.db_path:
            return
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("INSERT INTO task_history (task_id, title, completed_at, cost_sec) VALUES (?,?,?,?)",
                    (task.id, task.title,
                     task.finished_at.strftime("%Y-%m-%d %H:%M:%S"),
                     task.cost_sec))
        conn.commit()
        conn.close()

    def delete_task_history(self, task_id):
        if not self.db_path:
            return
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM task_history WHERE task_id=?", (task_id,))
        conn.commit()
        conn.close()

    def get_pomodoro_stats(self):
        if not self.db_path:
            return {
                "today_focus": 0,
                "weekly_count": 0,
                "total_focus": 0
            }
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT SUM(duration) FROM pomodoro_records WHERE DATE(start_time) = DATE('now') AND phase='work'")
        today_focus = cur.fetchone()[0] or 0
        cur.execute(
            "SELECT COUNT(*) FROM pomodoro_records WHERE DATE(start_time) >= DATE('now', '-7 days') AND phase='work'")
        weekly_count = cur.fetchone()[0] or 0
        cur.execute("SELECT SUM(duration) FROM pomodoro_records WHERE phase='work'")
        total_focus = cur.fetchone()[0] or 0
        conn.close()
        return {
            "today_focus": today_focus,
            "weekly_count": weekly_count,
            "total_focus": total_focus
        }


class SessionManager:
    def __init__(self):
        self.db_path = GLOBAL_DB_PATH
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS app_profile
        (key TEXT PRIMARY KEY,
         value TEXT NOT NULL)''')
        conn.commit()
        conn.close()

    def set_value(self, key, value):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO app_profile (key, value) VALUES (?, ?)",
            (key, str(value))
        )
        conn.commit()
        conn.close()

    def get_value(self, key, default=None):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT value FROM app_profile WHERE key=?", (key,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else default

    def clear_value(self, key):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM app_profile WHERE key=?", (key,))
        conn.commit()
        conn.close()

    def save_account_session(self, username, token):
        self.set_value("account_username", username)
        # 安全修复：使用 Windows DPAPI 加密 token 后存储
        encrypted_token = _dpapi_protect(token.encode("utf-8"))
        self.set_value("account_token", encrypted_token)

    def get_account_session(self):
        username = self.get_value("account_username")
        stored_token = self.get_value("account_token")
        if username and stored_token:
            try:
                # 安全修复：使用 Windows DPAPI 解密 token
                token = _dpapi_unprotect(stored_token).decode("utf-8")
                return {"username": username, "token": token}
            except Exception:
                # 解密失败：可能是旧版明文 token，自动迁移到加密存储
                if len(stored_token) > 10:
                    self.save_account_session(username, stored_token)
                    return {"username": username, "token": stored_token}
                # 无效数据，清除
                self.clear_account_session()
                return None
        return None

    def clear_account_session(self):
        self.clear_value("account_username")
        self.clear_value("account_token")

# ====================== 可视化图表 ======================
def show_chart(dm, schedule):
    tasks_data = dm.load_tasks()
    completed = sum(1 for t in tasks_data if t["completed"])
    total = len(tasks_data)

    if not dm.db_path:
        messagebox.showinfo("提示", "请先登录账号后查看图表")
        return

    conn = sqlite3.connect(dm.db_path)
    cursor = conn.cursor()
    records = cursor.execute(
        "SELECT start_time, duration FROM pomodoro_records WHERE phase='work'"
    ).fetchall()
    history_records = cursor.execute(
        "SELECT completed_at, cost_sec FROM task_history"
    ).fetchall()
    conn.close()

    work_time = {}
    for record in records:
        date = record[0].split(" ")[0]
        work_time[date] = work_time.get(date, 0) + record[1]

    today = datetime.now().date()
    last_7_days = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    last_7_minutes = [round(work_time.get(d, 0) / 60, 1) for d in last_7_days]

    # 来自数据库 task_history 的已完成任务统计
    history_by_day = {}
    for completed_at, _ in history_records:
        d = completed_at.split(" ")[0]
        history_by_day[d] = history_by_day.get(d, 0) + 1
    completed_7d = [history_by_day.get(d, 0) for d in last_7_days]

    def format_seconds(seconds):
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}秒"
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}分{seconds}秒" if seconds else f"{minutes}分"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}时{minutes}分" if minutes else f"{hours}时"

    bucket_labels = [
        "0-30秒",
        "30秒-1分钟",
        "1分钟-1分30秒",
        "1分30秒-2分钟",
        "2分钟-2分30秒",
        "2分30秒-3分钟",
        "3分钟及以上",
    ]
    time_bucket_counts = {i: 0 for i in range(len(bucket_labels))}
    for _, cost_sec in history_records:
        safe_cost = max(int(cost_sec or 0), 0)
        bucket_index = min(safe_cost // 30, len(bucket_labels) - 1)
        time_bucket_counts[bucket_index] = time_bucket_counts.get(bucket_index, 0) + 1
    bucket_counts = [time_bucket_counts[i] for i in range(len(bucket_labels))]

    fig, axes = plt.subplots(2, 3, figsize=(16, 10))
    fig.suptitle("效率统计仪表盘", fontsize=16, fontweight="bold")

    # ---------- (0,0) 任务完成率环形图 ----------
    ax = axes[0, 0]
    if total > 0:
        sizes = [completed, total - completed]
        _, _, autotexts = ax.pie(
            sizes,
            labels=[f"已完成 {completed}", f"未完成 {total - completed}"],
            autopct="%1.1f%%",
            colors=["#2ecc71", "#e74c3c"],
            startangle=90,
            wedgeprops={"edgecolor": "white", "linewidth": 2, "width": 0.4},
            textprops={"fontsize": 10},
        )
        for t in autotexts:
            t.set_color("white")
            t.set_fontweight("bold")
        ax.text(0, 0, f"{completed / total * 100:.0f}%",
                ha="center", va="center", fontsize=22, fontweight="bold", color="#2c3e50")
    else:
        ax.text(0.5, 0.5, "暂无任务", ha="center", va="center", fontsize=12)
    ax.set_title(f"任务完成率（共 {total} 个）", fontsize=12, fontweight="bold")

    # ---------- (0,1) 最近 7 天每日专注（分钟） ----------
    ax = axes[0, 1]
    xs1 = list(range(7))
    ax.fill_between(xs1, last_7_minutes, color="#3498db", alpha=0.2)
    ax.plot(xs1, last_7_minutes, marker="o", color="#3498db", linewidth=2, markersize=7)
    avg = sum(last_7_minutes) / 7
    if avg > 0:
        ax.axhline(y=avg, color="#e74c3c", linestyle="--", linewidth=1.2,
                   label=f"日均 {avg:.1f} 分")
        ax.legend(loc="upper right", fontsize=9)
    for i, val in enumerate(last_7_minutes):
        if val > 0:
            ax.text(i, val, f"{val:.0f}", ha="center", va="bottom", fontsize=8)
    ax.set_title("最近 7 天每日专注（分钟）", fontsize=12, fontweight="bold")
    ax.set_xticks(xs1)
    ax.set_xticklabels([d[5:] for d in last_7_days], rotation=30, fontsize=8)
    ax.set_ylabel("分钟")
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.3)

    # ---------- (0,2) 最近 7 天每日完成任务数（来自 task_history） ----------
    ax = axes[0, 2]
    bars2 = ax.bar(range(7), completed_7d, color="#16a085", alpha=0.85, edgecolor="white")
    for bar, val in zip(bars2, completed_7d):
        if val > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                    f"{val}", ha="center", va="bottom", fontsize=9, fontweight="bold")
    ax.set_title(f"最近 7 天完成任务数（共 {sum(completed_7d)} 个）",
                 fontsize=12, fontweight="bold")
    ax.set_xticks(range(7))
    ax.set_xticklabels([d[5:] for d in last_7_days], rotation=30, fontsize=8)
    ax.set_ylabel("任务数")
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(axis="y", alpha=0.3)

    # ---------- (1,0) 各优先级任务情况（堆叠 + 完成率） ----------
    ax = axes[1, 0]
    priority_complete = {1: 0, 2: 0, 3: 0}
    priority_total = {1: 0, 2: 0, 3: 0}
    for task in tasks_data:
        prio = task["priority"]
        priority_total[prio] = priority_total.get(prio, 0) + 1
        if task["completed"]:
            priority_complete[prio] = priority_complete.get(prio, 0) + 1
    complete_counts = [priority_complete[i] for i in [1, 2, 3]]
    uncomplete_counts = [priority_total[i] - priority_complete[i] for i in [1, 2, 3]]
    x = range(3)
    ax.bar(x, complete_counts, color="#2ecc71", label="已完成", edgecolor="white")
    ax.bar(x, uncomplete_counts, bottom=complete_counts, color="#e74c3c",
           alpha=0.7, label="未完成", edgecolor="white")
    for i in range(3):
        tot = priority_total[i + 1]
        if tot > 0:
            rate = priority_complete[i + 1] / tot * 100
            ax.text(i, tot, f"{rate:.0f}%", ha="center", va="bottom",
                    fontsize=9, fontweight="bold", color="#2c3e50")
    ax.set_title("各优先级完成情况", fontsize=12, fontweight="bold")
    ax.set_xticks(list(x))
    ax.set_xticklabels(["高", "中", "低"])
    ax.set_xlabel("优先级")
    ax.set_ylabel("任务数量")
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(axis="y", alpha=0.3)

    # ---------- (1,1) 历史任务耗时分布（来自 task_history） ----------
    ax = axes[1, 1]
    if sum(bucket_counts) > 0:
        bars3 = ax.bar(bucket_labels, bucket_counts, color="#f39c12", alpha=0.85, edgecolor="white")
        for bar, val in zip(bars3, bucket_counts):
            if val > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                        f"{val}", ha="center", va="bottom", fontsize=9, fontweight="bold")
        avg_cost = sum(c or 0 for _, c in history_records) / len(history_records)
        ax.set_title(f"任务耗时分布（共 {sum(bucket_counts)} 个，均 {format_seconds(avg_cost)}）",
                     fontsize=12, fontweight="bold")
        ax.tick_params(axis="x", labelrotation=45, labelsize=8)
    else:
        ax.text(0.5, 0.5, "暂无已完成任务", ha="center", va="center", fontsize=12)
        ax.set_title("任务耗时分布", fontsize=12, fontweight="bold")
    ax.set_xlabel("耗时区间")
    ax.set_ylabel("任务数")
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(axis="y", alpha=0.3)

    # ---------- (1,2) 累积专注时长趋势（分钟） ----------
    ax = axes[1, 2]
    if work_time:
        dates_sorted = sorted(work_time.keys())
        cumulative = []
        running = 0.0
        for d in dates_sorted:
            running += work_time[d] / 60
            cumulative.append(running)
        xs2 = list(range(len(dates_sorted)))
        ax.plot(xs2, cumulative, marker="o", color="#9b59b6", linewidth=2, markersize=6)
        ax.fill_between(xs2, cumulative, color="#9b59b6", alpha=0.2)
        ax.set_xticks(xs2)
        ax.set_xticklabels([d[5:] for d in dates_sorted], rotation=45, fontsize=8)
        ax.set_title(f"累积专注趋势（总 {cumulative[-1]:.1f} 分钟）",
                     fontsize=12, fontweight="bold")
        ax.set_xlabel("日期")
        ax.set_ylabel("累积分钟")
        ax.grid(alpha=0.3)
    else:
        ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", fontsize=12)
        ax.set_title("累积专注时长", fontsize=12, fontweight="bold")

    plt.tight_layout()
    plt.show()

# ====================== 桌面通知 ======================
def send_desktop_notification(title, message):
    try:
        notification.notify(
            title=title,
            message=message,
            timeout=10,
            app_name="智能日程管理器"
        )
    except:
        pass

# ====================== 番茄钟计时器 ======================
# ====================== 番茄钟计时器 ======================
class PomodoroTimer:
    def __init__(self, app, dm, task_id=None):
        self.app = app
        self.dm = dm
        self.task_id = task_id
        self.window = None
        self.running = False
        self.paused = False
        self.remaining_seconds = 0
        self.current_phase = "work"
        self.work_duration = 25
        self.short_break = 5
        self.long_break = 10
        self.finish_work_count = 0
        self.start_time = None

    def start(self):
        self.window = Toplevel(self.app.root)
        self.window.title("番茄钟计时器")
        self.window.geometry("420x330")
        self.window.configure(bg=COLORS["bg"])
        self.window.resizable(False, False)
        self.window.transient(self.app.root)
        self.window.grab_set()

        container = tk.Frame(self.window, bg=COLORS["panel"], padx=26, pady=22)
        container.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)

        self.phase_label = Label(container, text="🍅 专注时间", font=(FONT_FAMILY, 16, "bold"), bg=COLORS["panel"], fg=COLORS["success"])
        self.phase_label.pack(pady=(4, 10))
        self.time_label = Label(container, text="25", font=("Arial", 52, "bold"), bg=COLORS["panel"], fg=COLORS["text"])
        self.time_label.pack(pady=8)

        button_frame = tk.Frame(container, bg=COLORS["panel"])
        button_frame.pack(pady=16)
        self.start_btn = tk.Button(button_frame, text="开始", command=self.start_timer,
                                   bg=COLORS["success"], fg="white", relief=tk.FLAT, font=(FONT_FAMILY, 10), padx=14, pady=7)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.pause_btn = tk.Button(button_frame, text="暂停", command=self.pause_timer,
                                   bg=COLORS["warning"], fg="white", relief=tk.FLAT, font=(FONT_FAMILY, 10), padx=14, pady=7, state=tk.DISABLED)
        self.pause_btn.pack(side=tk.LEFT, padx=5)
        self.reset_btn = tk.Button(button_frame, text="重置", command=self.reset_timer,
                                   bg=COLORS["danger"], fg="white", relief=tk.FLAT, font=(FONT_FAMILY, 10), padx=14, pady=7)
        self.reset_btn.pack(side=tk.LEFT, padx=5)

        self.status_label = Label(container, text="准备就绪", font=(FONT_FAMILY, 10), fg=COLORS["muted"], bg=COLORS["panel"])
        self.status_label.pack(pady=(4, 8))

        if self.task_id:
            task = self.app.schedule.get_task_by_id(self.task_id)
            if task:
                task_label = Label(container, text=f"关联任务：{task.title}", font=(FONT_FAMILY, 9), fg=COLORS["primary"], bg=COLORS["panel"])
                task_label.pack(pady=(4, 2))
                self.pomo_label = Label(container, text=f"番茄进度：{task.completed_pomodoros}/{task.target_pomodoros}",
                                  font=(FONT_FAMILY, 9), fg=COLORS["warning"], bg=COLORS["panel"])
                self.pomo_label.pack(pady=2)

        self.remaining_seconds = self.work_duration
        self.update_display()
        self.window.protocol("WM_DELETE_WINDOW", self.on_close)

    def start_timer(self):
        if not self.running:
            self.running = True
            self.paused = False
            self.start_btn.config(state=tk.DISABLED)
            self.pause_btn.config(state=tk.NORMAL)
            self.status_label.config(text="计时中...", fg=COLORS["success"])
            self.start_time = datetime.now()
            self.run_timer()

    def pause_timer(self):
        if self.running and not self.paused:
            self.paused = True
            self.pause_btn.config(text="继续", command=self.resume_timer)
            self.status_label.config(text="已暂停", fg=COLORS["warning"])

    def resume_timer(self):
        if self.running and self.paused:
            self.paused = False
            self.pause_btn.config(text="暂停", command=self.pause_timer)
            self.status_label.config(text="计时中...", fg=COLORS["success"])
            self.run_timer()

    def reset_timer(self):
        self.running = False
        self.paused = False
        if self.current_phase == "work":
            self.remaining_seconds = self.work_duration
            self.phase_label.config(text="🍅 专注时间", fg=COLORS["success"])
        else:
            if self.finish_work_count % 2 == 0:
                self.remaining_seconds = self.long_break
            else:
                self.remaining_seconds = self.short_break
        self.update_display()
        self.start_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED, text="暂停", command=self.pause_timer)
        self.status_label.config(text="已重置", fg=COLORS["muted"])

    def run_timer(self):
        if self.running and not self.paused and self.remaining_seconds > 0:
            self.remaining_seconds -= 1
            self.update_display()
            self.app.root.after(1000, self.run_timer)
        elif self.remaining_seconds <= 0:
            self.complete_phase()

    def update_display(self):
        if self.time_label.winfo_exists():
            minutes, seconds = divmod(max(self.remaining_seconds, 0), 60)
            self.time_label.config(text=f"{minutes:02d}:{seconds:02d}")

    def complete_phase(self):
        if self.current_phase == "work":
            end_time = datetime.now()
            updated, task_data, points_update = self.dm.save_pomodoro(self.start_time, end_time, "work", self.task_id)
            self.finish_work_count += 1
            self.app.refresh_points_display()
            if points_update:
                level_message = f"\n称号升级：{points_update['title']}!" if points_update["leveled_up"] else ""
                send_desktop_notification(
                    "积分增加",
                    f"完成 1 个番茄，+{points_update['earned']} 积分。{level_message}"
                )
                self.app.sync_points_to_server(show_success=False)

            # 同步内存中的任务对象（DataManager 只写了 JSON）
            if updated and task_data and self.task_id:
                task = self.app.schedule.get_task_by_id(self.task_id)
                if task:
                    task.completed_pomodoros = task_data['completed_pomodoros']

                    if hasattr(self, 'pomo_label') and self.pomo_label.winfo_exists():
                        self.pomo_label.config(text=f"番茄进度：{task.completed_pomodoros}/{task.target_pomodoros}")

                    # 番茄达标：自动完成任务并关闭窗口
                    if task_data.get('completed'):
                        task.completed = True
                        task.finished_at = datetime.strptime(task_data['finished_at'], "%Y-%m-%d %H:%M:%S")
                        task.cost_sec = task_data['cost_sec']
                        self.dm.save_task_history(task)
                        send_desktop_notification("任务完成", f"恭喜完成：{task.title}\n番茄目标已达成!")
                        self.app.refresh_list()
                        self.running = False
                        if self.window and self.window.winfo_exists():
                            self.window.destroy()
                        self.app.show_task_completion_dialog(task, points_update)
                        return

            send_desktop_notification("专注完成", "进入休息时间")

            self.current_phase = "break"
            if self.finish_work_count % 2 == 0:
                self.remaining_seconds = self.long_break
                self.phase_label.config(text="☕ 长休息", fg="#7c3aed")
            else:
                self.remaining_seconds = self.short_break
                self.phase_label.config(text="☕ 短休息", fg=COLORS["warning"])
        else:
            send_desktop_notification("休息结束", "自动进入下一轮专注")
            self.current_phase = "work"
            self.remaining_seconds = self.work_duration
            self.phase_label.config(text="🍅 专注时间", fg=COLORS["success"])

        self.running = False
        self.reset_timer()

    def on_close(self):
        if self.running:
            if messagebox.askyesno("确认", "计时中，确定关闭？"):
                self.running = False
                self.window.destroy()
        else:
            self.window.destroy()

# ====================== 任务详情窗口 ======================
class TaskDetailWindow:
    def __init__(self, parent, app, task=None, scheduled_date=None):
        self.parent = parent
        self.app = app
        self.schedule = app.schedule
        self.dm = app.dm
        self.task = task
        self.initial_scheduled_date = scheduled_date
        self.window = Toplevel(parent)
        self.window.title("编辑任务" if task else "新建任务")
        self.window.geometry("620x560")
        self.window.configure(bg=COLORS["bg"])
        self.window.transient(parent)
        self.window.grab_set()
        self.create_widgets()

    def create_widgets(self):
        container = tk.Frame(self.window, bg=COLORS["panel"], padx=24, pady=22)
        container.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)

        title = "编辑任务" if self.task else "新建任务"
        tk.Label(
            container,
            text=title,
            font=(FONT_FAMILY, 18, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 18))
        container.columnconfigure(1, weight=1)

        def field_label(row, text):
            tk.Label(
                container,
                text=text,
                font=(FONT_FAMILY, 10, "bold"),
                fg=COLORS["muted"],
                bg=COLORS["panel"],
            ).grid(row=row, column=0, sticky="nw", pady=8, padx=(0, 14))

        field_label(1, "任务标题")
        self.title_entry = tk.Entry(container, font=(FONT_FAMILY, 11), relief=tk.SOLID, bd=1)
        self.title_entry.grid(row=1, column=1, sticky="ew", pady=8)

        field_label(2, "任务描述")
        self.desc_text = tk.Text(container, height=5, font=(FONT_FAMILY, 10), relief=tk.SOLID, bd=1)
        self.desc_text.grid(row=2, column=1, sticky="ew", pady=8)

        field_label(3, "截止时间")
        due_frame = tk.Frame(container, bg=COLORS["panel"])
        due_frame.grid(row=3, column=1, sticky="ew", pady=8)
        due_frame.columnconfigure(0, weight=1)
        self.due_entry = tk.Entry(due_frame, font=(FONT_FAMILY, 10), relief=tk.SOLID, bd=1)
        self.due_entry.grid(row=0, column=0, sticky="ew")
        tk.Label(
            due_frame,
            text="支持 2024-12-31 18:00、明天、后天、下周一",
            font=(FONT_FAMILY, 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        field_label(4, "计划进入")
        scheduled_frame = tk.Frame(container, bg=COLORS["panel"])
        scheduled_frame.grid(row=4, column=1, sticky="ew", pady=8)
        scheduled_frame.columnconfigure(0, weight=1)
        self.scheduled_entry = tk.Entry(scheduled_frame, font=(FONT_FAMILY, 10), relief=tk.SOLID, bd=1)
        self.scheduled_entry.grid(row=0, column=0, sticky="ew")
        tk.Label(
            scheduled_frame,
            text="未来日期会进入待定列表，例如 2026-05-28",
            font=(FONT_FAMILY, 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        field_label(5, "优先级")
        self.priority_var = tk.IntVar(value=2)
        priority_frame = tk.Frame(container, bg=COLORS["panel"])
        priority_frame.grid(row=5, column=1, sticky="w", pady=8)
        for text, value in [("高", 1), ("中", 2), ("低", 3)]:
            tk.Radiobutton(
                priority_frame,
                text=text,
                variable=self.priority_var,
                value=value,
                bg=COLORS["panel"],
                fg=COLORS["text"],
                selectcolor=COLORS["panel"],
                font=(FONT_FAMILY, 10),
            ).pack(side=tk.LEFT, padx=(0, 18))

        field_label(6, "目标番茄")
        tomato_frame = tk.Frame(container, bg=COLORS["panel"])
        tomato_frame.grid(row=6, column=1, sticky="w", pady=8)
        self.tomato_var = tk.IntVar(value=1)
        self.tomato_spinbox = tk.Spinbox(tomato_frame, from_=1, to=20, textvariable=self.tomato_var, width=8, font=(FONT_FAMILY, 10))
        self.tomato_spinbox.pack(side=tk.LEFT)
        tk.Label(tomato_frame, text="个", font=(FONT_FAMILY, 10), bg=COLORS["panel"], fg=COLORS["text"]).pack(side=tk.LEFT, padx=8)

        button_frame = tk.Frame(container, bg=COLORS["panel"])
        button_frame.grid(row=7, column=0, columnspan=2, sticky="e", pady=(24, 0))
        if self.task:
            tk.Button(button_frame, text="更新任务", command=self.update_task,
                      bg=COLORS["primary"], fg="white", relief=tk.FLAT, font=(FONT_FAMILY, 10), width=12).pack(side=tk.LEFT, padx=6)
            tk.Button(button_frame, text="删除任务", command=self.delete_task,
                      bg=COLORS["danger"], fg="white", relief=tk.FLAT, font=(FONT_FAMILY, 10), width=12).pack(side=tk.LEFT, padx=6)
        else:
            tk.Button(button_frame, text="创建任务", command=self.create_task,
                      bg=COLORS["success"], fg="white", relief=tk.FLAT, font=(FONT_FAMILY, 10), width=12).pack(side=tk.LEFT, padx=6)
        tk.Button(button_frame, text="取消", command=self.window.destroy,
                  bg="#e2e8f0", fg=COLORS["text"], relief=tk.FLAT, font=(FONT_FAMILY, 10), width=12).pack(side=tk.LEFT, padx=6)

        if self.task:
            self.title_entry.insert(0, self.task.title)
            self.desc_text.insert("1.0", self.task.description)
            if self.task.due_date:
                self.due_entry.insert(0, self.task.due_date.strftime("%Y-%m-%d %H:%M"))
            if self.task.scheduled_date:
                self.scheduled_entry.insert(0, self.task.scheduled_date.strftime("%Y-%m-%d"))
            self.priority_var.set(self.task.priority)
            self.tomato_var.set(self.task.target_pomodoros)
        elif self.initial_scheduled_date:
            self.scheduled_entry.insert(0, self.initial_scheduled_date.strftime("%Y-%m-%d"))

    def parse_datetime(self, time_str):
        if not time_str:
            return None
        time_str = time_str.strip()
        if time_str == "明天":
            return datetime.now() + timedelta(days=1)
        if time_str == "后天":
            return datetime.now() + timedelta(days=2)
        weekdays = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6}
        for day, offset in weekdays.items():
            if time_str == f"下周{day}":
                today = datetime.now()
                days_ahead = offset - today.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                return today + timedelta(days=days_ahead + 7)
        pattern = r'^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{1,2}))?$'
        match = re.match(pattern, time_str)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            hour = int(match.group(4)) if match.group(4) else 23
            minute = int(match.group(5)) if match.group(5) else 59
            return datetime(year, month, day, hour, minute)
        return None

    def parse_scheduled_date(self, date_str):
        if not date_str:
            return None
        parsed = self.parse_datetime(date_str)
        if not parsed:
            return None
        return datetime(parsed.year, parsed.month, parsed.day)

    def get_scheduled_date_from_entry(self):
        scheduled_text = self.scheduled_entry.get().strip()
        scheduled_date = self.parse_scheduled_date(scheduled_text)
        if scheduled_text and not scheduled_date:
            messagebox.showwarning("提示", "请输入有效的计划进入日期，例如：2026-05-28")
            return False, None
        if scheduled_date and scheduled_date.date() <= datetime.now().date():
            scheduled_date = None
        return True, scheduled_date

    def create_task(self):
        title = self.title_entry.get().strip()
        if not title:
            messagebox.showwarning("提示", "请输入任务标题")
            return
        description = self.desc_text.get("1.0", tk.END).strip()
        due_date = self.parse_datetime(self.due_entry.get())
        ok, scheduled_date = self.get_scheduled_date_from_entry()
        if not ok:
            return
        priority = self.priority_var.get()
        target_pomodoros = self.tomato_var.get()

        new_task = Task(title, description, due_date, priority, scheduled_date)
        new_task.target_pomodoros = target_pomodoros
        new_task.completed_pomodoros = 0

        self.schedule.add_task(new_task)
        self.dm.save_tasks([t.to_dict() for t in self.schedule.tasks])

        self.app.refresh_list()
        self.window.destroy()
        messagebox.showinfo("成功", "任务创建成功！")

    def update_task(self):
        title = self.title_entry.get().strip()
        if not title:
            messagebox.showwarning("提示", "请输入任务标题")
            return
        self.task.title = title
        self.task.description = self.desc_text.get("1.0", tk.END).strip()
        self.task.due_date = self.parse_datetime(self.due_entry.get())
        ok, scheduled_date = self.get_scheduled_date_from_entry()
        if not ok:
            return
        self.task.scheduled_date = scheduled_date
        self.task.priority = self.priority_var.get()
        self.task.target_pomodoros = self.tomato_var.get()
        self.dm.save_tasks([t.to_dict() for t in self.schedule.tasks])
        messagebox.showinfo("成功", f"任务更新成功！\n目标番茄：{self.task.target_pomodoros}个")
        self.window.destroy()
        self.app.refresh_list()

    def delete_task(self):
        if messagebox.askyesno("确认", f"确定要删除任务「{self.task.title}」吗？"):
            self.schedule.remove_task(self.task.id)
            self.dm.save_tasks([t.to_dict() for t in self.schedule.tasks])
            messagebox.showinfo("成功", "任务已删除")
            self.window.destroy()
            self.app.refresh_list()

# ====================== 统计面板 ======================
class StatisticsWindow:
    def __init__(self, parent, dm, schedule):
        self.parent = parent
        self.dm = dm
        self.schedule = schedule
        self.window = Toplevel(parent)
        self.window.title("统计面板")
        self.window.geometry("520x520")
        self.window.configure(bg=COLORS["bg"])
        self.create_widgets()
        self.update_stats()

    def create_stat_card(self, parent, title, value="0"):
        card = tk.Frame(parent, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
        tk.Label(card, text=title, font=(FONT_FAMILY, 10), fg=COLORS["muted"], bg=COLORS["panel"]).pack(anchor="w", padx=16, pady=(14, 0))
        label = tk.Label(card, text=value, font=(FONT_FAMILY, 20, "bold"), fg=COLORS["text"], bg=COLORS["panel"])
        label.pack(anchor="w", padx=16, pady=(2, 14))
        return card, label

    def create_widgets(self):
        container = tk.Frame(self.window, bg=COLORS["bg"], padx=22, pady=20)
        container.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            container,
            text="统计面板",
            font=(FONT_FAMILY, 18, "bold"),
            fg=COLORS["text"],
            bg=COLORS["bg"],
        ).pack(anchor="w")
        tk.Label(
            container,
            text="任务完成情况与番茄钟专注数据",
            font=(FONT_FAMILY, 10),
            fg=COLORS["muted"],
            bg=COLORS["bg"],
        ).pack(anchor="w", pady=(2, 16))

        grid = tk.Frame(container, bg=COLORS["bg"])
        grid.pack(fill="x")
        self.total_card, self.total_label = self.create_stat_card(grid, "总任务")
        self.completed_card, self.completed_label = self.create_stat_card(grid, "已完成")
        self.uncompleted_card, self.uncompleted_label = self.create_stat_card(grid, "未完成")
        self.rate_card, self.rate_label = self.create_stat_card(grid, "完成率")
        for index, card in enumerate([self.total_card, self.completed_card, self.uncompleted_card, self.rate_card]):
            card.grid(row=index // 2, column=index % 2, sticky="ew", padx=6, pady=6)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        pomo_frame = tk.Frame(container, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
        pomo_frame.pack(fill="x", pady=(14, 8))
        tk.Label(pomo_frame, text="番茄钟", font=(FONT_FAMILY, 11, "bold"), fg=COLORS["text"], bg=COLORS["panel"]).pack(anchor="w", padx=16, pady=(14, 6))
        self.today_label = tk.Label(pomo_frame, text="今日专注: 0秒", font=(FONT_FAMILY, 10), fg=COLORS["text"], bg=COLORS["panel"])
        self.today_label.pack(anchor="w", padx=16, pady=2)
        self.weekly_label = tk.Label(pomo_frame, text="近 7 天专注次数: 0", font=(FONT_FAMILY, 10), fg=COLORS["text"], bg=COLORS["panel"])
        self.weekly_label.pack(anchor="w", padx=16, pady=2)
        self.total_pomo_label = tk.Label(pomo_frame, text="总专注时长: 0秒", font=(FONT_FAMILY, 10), fg=COLORS["text"], bg=COLORS["panel"])
        self.total_pomo_label.pack(anchor="w", padx=16, pady=(2, 14))

        points_frame = tk.Frame(container, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
        points_frame.pack(fill="x", pady=(6, 8))
        tk.Label(points_frame, text="积分与称号", font=(FONT_FAMILY, 11, "bold"), fg=COLORS["text"], bg=COLORS["panel"]).pack(anchor="w", padx=16, pady=(14, 6))
        self.points_label = tk.Label(points_frame, text="积分: 0", font=(FONT_FAMILY, 10), fg=COLORS["text"], bg=COLORS["panel"])
        self.points_label.pack(anchor="w", padx=16, pady=2)
        self.honor_label = tk.Label(points_frame, text="称号: 起步者", font=(FONT_FAMILY, 10), fg=COLORS["text"], bg=COLORS["panel"])
        self.honor_label.pack(anchor="w", padx=16, pady=2)
        self.next_honor_label = tk.Label(points_frame, text="", font=(FONT_FAMILY, 10), fg=COLORS["muted"], bg=COLORS["panel"])
        self.next_honor_label.pack(anchor="w", padx=16, pady=(2, 14))

        button_frame = tk.Frame(container, bg=COLORS["bg"])
        button_frame.pack(fill="x", pady=(8, 0))
        tk.Button(button_frame, text="刷新数据", command=self.update_stats,
                  bg=COLORS["primary"], fg="white", relief=tk.FLAT, font=(FONT_FAMILY, 10), padx=14, pady=8).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(button_frame, text="查看详细图表", command=lambda: show_chart(self.dm, self.schedule),
                  bg="#e2e8f0", fg=COLORS["text"], relief=tk.FLAT, font=(FONT_FAMILY, 10), padx=14, pady=8).pack(side=tk.LEFT)

    def update_stats(self):
        stats = self.schedule.get_statistics()
        pomo_stats = self.dm.get_pomodoro_stats()
        self.total_label.config(text=str(stats['total']))
        self.completed_label.config(text=str(stats['completed']))
        self.uncompleted_label.config(text=str(stats['total'] - stats['completed']))
        self.rate_label.config(text=f"{stats['completion_rate']:.1f}%")
        self.today_label.config(text=f"今日专注: {format_duration(pomo_stats['today_focus'])}")
        self.weekly_label.config(text=f"近 7 天专注次数: {pomo_stats['weekly_count']}")
        self.total_pomo_label.config(text=f"总专注时长: {format_duration(pomo_stats['total_focus'])}")
        points_profile = self.dm.get_points_profile()
        self.points_label.config(text=f"积分: {points_profile['points']}")
        self.honor_label.config(text=f"称号: {points_profile['title']}")
        if points_profile["next_title"]:
            self.next_honor_label.config(
                text=f"距离「{points_profile['next_title']}」还需 {points_profile['points_to_next']} 积分"
            )
        else:
            self.next_honor_label.config(text="已获得最高称号")

# ====================== GUI主窗口 ======================
class AppGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("智能日程管理器 | 番茄钟 + 任务管理")
        self.root.geometry("1100x700")
        self.root.minsize(980, 620)
        try:
            self.root.iconbitmap("icon.ico")
        except:
            pass
        self.session_manager = SessionManager()
        self.account_session = self.session_manager.get_account_session()
        self.dm = DataManager(self.account_session["username"] if self.account_session else None)
        self.schedule = Schedule()
        ensure_score_server_running()
        self.load_current_account_data()
        self.uncompleted_task_ids = []
        self.pending_task_ids = []
        self.completed_task_ids = []
        self.calendar_year = datetime.now().year
        self.calendar_month = datetime.now().month
        self.create_widgets()
        self.refresh_list()
        self.schedule_pending_refresh()
        self.root.after(100, lambda: send_desktop_notification("欢迎回来",
                                                               f"您有 {len(self.schedule.get_uncompleted_tasks())} 个待办任务"))

    def load_current_account_data(self):
        self.schedule = Schedule()
        for task_data in self.dm.load_tasks():
            self.schedule.add_task(Task.from_dict(task_data))

    def switch_account_data(self, username=None):
        self.dm = DataManager(username)
        self.load_current_account_data()
        self.refresh_list()

    def setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except:
            pass
        style.configure("App.TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("App.TNotebook.Tab", padding=(18, 9), font=(FONT_FAMILY, 10))
        style.map("App.TNotebook.Tab", background=[("selected", COLORS["panel"])])
        style.configure(
            "Task.Treeview",
            background=COLORS["panel"],
            fieldbackground=COLORS["panel"],
            foreground=COLORS["text"],
            rowheight=34,
            borderwidth=0,
            font=(FONT_FAMILY, 10),
        )
        style.configure(
            "Task.Treeview.Heading",
            background="#eef3f8",
            foreground=COLORS["muted"],
            relief="flat",
            font=(FONT_FAMILY, 10, "bold"),
            padding=(8, 8),
        )
        style.map("Task.Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", COLORS["text"])])
        style.configure("Vertical.TScrollbar", background=COLORS["border"], troughcolor=COLORS["bg"])

    def create_button(self, parent, text, command, variant="primary", width=None):
        colors = {
            "primary": (COLORS["primary"], "white"),
            "success": (COLORS["success"], "white"),
            "warning": (COLORS["warning"], "white"),
            "danger": (COLORS["danger"], "white"),
            "muted": ("#e2e8f0", COLORS["text"]),
            "sidebar": (COLORS["sidebar"], COLORS["sidebar_text"]),
        }
        bg, fg = colors.get(variant, colors["primary"])
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
            relief=tk.FLAT,
            bd=0,
            font=(FONT_FAMILY, 10),
            cursor="hand2",
            padx=14,
            pady=8,
            width=width,
        )
        return btn

    def select_tab_by_type(self, list_type):
        frames = {
            "uncompleted": self.uncompleted_frame,
            "pending": self.pending_frame,
            "completed": self.completed_frame,
            "calendar": self.calendar_frame,
        }
        self.notebook.select(frames[list_type])

    def get_current_list_type(self):
        selected_tab = self.notebook.tab(self.notebook.select(), "text")
        if selected_tab == "已完成":
            return "completed"
        if selected_tab == "待定":
            return "pending"
        if selected_tab == "日历":
            return "calendar"
        return "uncompleted"

    def format_datetime_label(self, value, fmt="%Y-%m-%d %H:%M"):
        return value.strftime(fmt) if value else "无"

    def priority_label(self, priority):
        return {1: "高", 2: "中", 3: "低"}.get(priority, "低")

    def refresh_points_display(self):
        profile = self.dm.get_points_profile()
        if hasattr(self, "honor_title_label"):
            self.honor_title_label.config(text=profile["title"])
            self.points_status_label.config(text=f"{profile['points']} 积分")
            if profile["next_title"]:
                self.next_honor_status_label.config(
                    text=f"距「{profile['next_title']}」还需 {profile['points_to_next']} 积分"
                )
            else:
                self.next_honor_status_label.config(text="已获得最高称号")
        if hasattr(self, "account_status_label"):
            if self.account_session:
                self.account_status_label.config(text=f"账号：{self.account_session['username']}")
            else:
                self.account_status_label.config(text="未登录")
        return profile

    def show_task_completion_dialog(self, task, points_update=None):
        quote, source = random.choice(PERSEVERANCE_QUOTES)
        profile = points_update or self.dm.get_points_profile()

        dialog = Toplevel(self.root)
        dialog.title("任务完成")
        dialog.geometry("500x360")
        dialog.configure(bg=COLORS["bg"])
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        panel = tk.Frame(dialog, bg=COLORS["panel"], padx=24, pady=22)
        panel.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)
        tk.Label(
            panel,
            text="任务完成",
            font=(FONT_FAMILY, 17, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel"],
        ).pack(anchor="w")
        tk.Label(
            panel,
            text=f"「{task.title}」已完成",
            font=(FONT_FAMILY, 10),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
            wraplength=430,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(4, 16))

        quote_frame = tk.Frame(panel, bg="#f8fafc", highlightbackground=COLORS["border"], highlightthickness=1)
        quote_frame.pack(fill="x", pady=(0, 14))
        tk.Label(
            quote_frame,
            text=quote,
            font=(FONT_FAMILY, 13, "bold"),
            fg=COLORS["text"],
            bg="#f8fafc",
            wraplength=410,
            justify=tk.LEFT,
        ).pack(anchor="w", padx=16, pady=(14, 4))
        tk.Label(
            quote_frame,
            text=f"来源：{source}",
            font=(FONT_FAMILY, 10),
            fg=COLORS["muted"],
            bg="#f8fafc",
        ).pack(anchor="e", padx=16, pady=(0, 14))

        if points_update and points_update.get("earned"):
            if points_update["leveled_up"]:
                points_text = (
                    f"本次番茄 +{points_update['earned']} 积分，"
                    f"新称号：{points_update['title']}"
                )
            else:
                points_text = (
                    f"本次番茄 +{points_update['earned']} 积分，"
                    f"当前 {points_update['points']} 积分，称号：{points_update['title']}"
                )
        else:
            points_text = f"当前 {profile['points']} 积分，称号：{profile['title']}"
        tk.Label(
            panel,
            text=points_text,
            font=(FONT_FAMILY, 10),
            fg=COLORS["primary"],
            bg=COLORS["panel"],
            wraplength=430,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 12))

        tk.Button(
            panel,
            text="继续",
            command=dialog.destroy,
            bg=COLORS["primary"],
            fg="white",
            relief=tk.FLAT,
            font=(FONT_FAMILY, 10),
            padx=18,
            pady=8,
        ).pack(anchor="e")
        dialog.bind("<Return>", lambda _event: dialog.destroy())

    def show_honor_overview(self, _event=None):
        profile = self.dm.get_points_profile()
        current_points = profile["points"]

        dialog = Toplevel(self.root)
        dialog.title("称号总览")
        dialog.geometry("560x520")
        dialog.configure(bg=COLORS["bg"])
        dialog.resizable(False, False)
        dialog.transient(self.root)

        container = tk.Frame(dialog, bg=COLORS["bg"], padx=22, pady=20)
        container.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            container,
            text="称号总览",
            font=(FONT_FAMILY, 18, "bold"),
            fg=COLORS["text"],
            bg=COLORS["bg"],
        ).pack(anchor="w")
        tk.Label(
            container,
            text=f"当前 {current_points} 积分，称号：{profile['title']}。每完成 1 个工作番茄 +{POINTS_PER_POMODORO} 积分。",
            font=(FONT_FAMILY, 10),
            fg=COLORS["muted"],
            bg=COLORS["bg"],
            wraplength=510,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(4, 14))

        list_frame = tk.Frame(container, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
        list_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("title", "threshold", "status")
        tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            style="Task.Treeview",
            selectmode="none",
            height=len(HONOR_LEVELS),
        )
        tree.heading("title", text="称号")
        tree.heading("threshold", text="所需积分")
        tree.heading("status", text="状态")
        tree.column("title", width=160, minwidth=120, anchor=tk.W, stretch=True)
        tree.column("threshold", width=110, minwidth=90, anchor=tk.CENTER, stretch=False)
        tree.column("status", width=210, minwidth=180, anchor=tk.W, stretch=True)
        tree.tag_configure("earned", foreground=COLORS["success"])
        tree.tag_configure("current", foreground=COLORS["primary"])
        tree.tag_configure("locked", foreground=COLORS["muted"])

        for threshold, title in HONOR_LEVELS:
            if title == profile["title"]:
                status = "当前称号"
                tags = ("current",)
            elif current_points >= threshold:
                status = "已获得"
                tags = ("earned",)
            else:
                status = f"还需 {threshold - current_points} 积分"
                tags = ("locked",)
            tree.insert("", tk.END, values=(title, threshold, status), tags=tags)

        tree.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        button_frame = tk.Frame(container, bg=COLORS["bg"])
        button_frame.pack(fill="x", pady=(14, 0))
        tk.Button(
            button_frame,
            text="关闭",
            command=dialog.destroy,
            bg=COLORS["primary"],
            fg="white",
            relief=tk.FLAT,
            font=(FONT_FAMILY, 10),
            padx=18,
            pady=8,
        ).pack(side=tk.RIGHT)
        dialog.bind("<Return>", lambda _event: dialog.destroy())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

    def sync_points_to_server(self, show_success=False):
        if not self.account_session:
            if show_success:
                messagebox.showinfo("提示", "请先登录账号")
            return False
        try:
            result = score_server_request(
                "POST",
                "/sync_points",
                {"points": self.dm.get_total_points()},
                token=self.account_session["token"],
            )
        except RuntimeError as exc:
            if show_success:
                messagebox.showerror("同步失败", str(exc))
            return False
        if not result.get("ok"):
            if show_success:
                messagebox.showerror("同步失败", result.get("error", "服务器请求失败"))
            return False
        if show_success:
            messagebox.showinfo("同步成功", "积分已同步到排行榜")
        return True

    def show_account_dialog(self):
        dialog = Toplevel(self.root)
        dialog.title("账号登录 / 注册")
        dialog.geometry("520x410")
        dialog.minsize(480, 380)
        dialog.configure(bg=COLORS["bg"])
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        panel = tk.Frame(dialog, bg=COLORS["panel"], padx=24, pady=22)
        panel.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)
        tk.Label(panel, text="账号登录 / 注册", font=(FONT_FAMILY, 16, "bold"),
                 fg=COLORS["text"], bg=COLORS["panel"]).pack(anchor="w")
        tk.Label(panel, text=f"服务器：{SCORE_SERVER_URL}", font=(FONT_FAMILY, 9),
                 fg=COLORS["muted"], bg=COLORS["panel"]).pack(anchor="w", pady=(4, 16))

        form_frame = tk.Frame(panel, bg=COLORS["panel"])
        form_frame.pack(fill="x")

        tk.Label(form_frame, text="用户名", font=(FONT_FAMILY, 10, "bold"),
                 fg=COLORS["text"], bg=COLORS["panel"]).pack(anchor="w")
        username_var = tk.StringVar(value=self.account_session["username"] if self.account_session else "")
        username_entry = tk.Entry(form_frame, textvariable=username_var, font=(FONT_FAMILY, 11), relief=tk.SOLID, bd=1)
        username_entry.pack(fill="x", pady=(6, 10))

        tk.Label(form_frame, text="密码", font=(FONT_FAMILY, 10, "bold"),
                 fg=COLORS["text"], bg=COLORS["panel"]).pack(anchor="w")
        password_var = tk.StringVar()
        password_entry = tk.Entry(form_frame, textvariable=password_var, show="*", font=(FONT_FAMILY, 11), relief=tk.SOLID, bd=1)
        password_entry.pack(fill="x", pady=(6, 8))

        status_label = tk.Label(form_frame, text="用户名 3-20 位，只能包含字母、数字和下划线；密码至少 6 位。", font=(FONT_FAMILY, 9),
                                fg=COLORS["muted"], bg=COLORS["panel"], wraplength=440, justify=tk.LEFT)
        status_label.pack(anchor="w", fill="x")

        def submit(mode):
            username = username_var.get().strip()
            password = password_var.get()
            if not username or not password:
                messagebox.showwarning("提示", "请输入用户名和密码")
                return
            path = "/register" if mode == "register" else "/login"
            payload = {"username": username, "password": password}
            if mode == "register":
                payload["points"] = 0
            try:
                result = score_server_request("POST", path, payload)
            except RuntimeError as exc:
                status_label.config(text=str(exc), fg=COLORS["danger"])
                return
            if not result.get("ok"):
                status_label.config(text=result.get("error", "请求失败"), fg=COLORS["danger"])
                return

            self.account_session = {"username": result["username"], "token": result["token"]}
            self.session_manager.save_account_session(result["username"], result["token"])
            self.switch_account_data(result["username"])
            if mode == "login":
                self.sync_points_to_server(show_success=False)
            self.refresh_points_display()
            dialog.destroy()
            messagebox.showinfo("成功", f"已登录：{result['username']}")

        action_frame = tk.Frame(panel, bg=COLORS["panel"])
        action_frame.pack(fill="x", side=tk.BOTTOM, pady=(22, 0))
        action_frame.columnconfigure(0, weight=1)
        tk.Button(action_frame, text="登录", command=lambda: submit("login"),
                  bg=COLORS["primary"], fg="white", relief=tk.FLAT, font=(FONT_FAMILY, 10), width=10, pady=8).grid(row=0, column=1, padx=(0, 8), sticky="e")
        tk.Button(action_frame, text="注册并登录", command=lambda: submit("register"),
                  bg=COLORS["success"], fg="white", relief=tk.FLAT, font=(FONT_FAMILY, 10), width=12, pady=8).grid(row=0, column=2, padx=(0, 8), sticky="e")
        tk.Button(action_frame, text="取消", command=dialog.destroy,
                  bg="#e2e8f0", fg=COLORS["text"], relief=tk.FLAT, font=(FONT_FAMILY, 10), width=10, pady=8).grid(row=0, column=3, sticky="e")
        username_entry.focus_set()
        dialog.bind("<Return>", lambda _event: submit("login"))

    def logout_account(self):
        if not self.account_session:
            messagebox.showinfo("提示", "当前没有登录账号")
            return
        if not messagebox.askyesno("确认", f"退出账号「{self.account_session['username']}」吗？"):
            return
        self.sync_points_to_server(show_success=False)
        self.account_session = None
        self.session_manager.clear_account_session()
        self.switch_account_data(None)
        self.refresh_points_display()
        messagebox.showinfo("成功", "已退出登录")

    def delete_account(self):
        if not self.account_session:
            messagebox.showinfo("提示", "请先登录账号")
            return

        username = self.account_session["username"]
        dialog = Toplevel(self.root)
        dialog.title("注销账号")
        dialog.geometry("500x330")
        dialog.configure(bg=COLORS["bg"])
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        panel = tk.Frame(dialog, bg=COLORS["panel"], padx=24, pady=22)
        panel.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)
        tk.Label(panel, text="注销账号", font=(FONT_FAMILY, 16, "bold"),
                 fg=COLORS["danger"], bg=COLORS["panel"]).pack(anchor="w")
        tk.Label(
            panel,
            text=f"账号「{username}」将从服务器删除，并删除本机该账号的任务、番茄记录和积分数据。此操作不可恢复。",
            font=(FONT_FAMILY, 10),
            fg=COLORS["text"],
            bg=COLORS["panel"],
            wraplength=430,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(6, 16))

        tk.Label(panel, text="请输入当前账号密码", font=(FONT_FAMILY, 10, "bold"),
                 fg=COLORS["text"], bg=COLORS["panel"]).pack(anchor="w")
        password_var = tk.StringVar()
        password_entry = tk.Entry(panel, textvariable=password_var, show="*", font=(FONT_FAMILY, 11), relief=tk.SOLID, bd=1)
        password_entry.pack(fill="x", pady=(6, 8))
        status_label = tk.Label(panel, text="密码用于确认注销操作。", font=(FONT_FAMILY, 9),
                                fg=COLORS["muted"], bg=COLORS["panel"], wraplength=430, justify=tk.LEFT)
        status_label.pack(anchor="w", fill="x")

        def confirm_delete():
            password = password_var.get()
            if not password:
                messagebox.showwarning("提示", "请输入密码")
                return
            if not messagebox.askyesno("最终确认", f"确定永久注销账号「{username}」吗？"):
                return
            try:
                result = score_server_request(
                    "POST",
                    "/delete_account",
                    {"password": password},
                    token=self.account_session["token"],
                )
            except RuntimeError as exc:
                status_label.config(text=str(exc), fg=COLORS["danger"])
                return
            if not result.get("ok"):
                status_label.config(text=result.get("error", "注销失败"), fg=COLORS["danger"])
                return

            self.account_session = None
            self.session_manager.clear_account_session()
            self.switch_account_data(None)
            self.refresh_points_display()

            account_dir = os.path.abspath(get_account_dir(username))
            accounts_root = os.path.abspath(ACCOUNTS_DIR)
            if os.path.commonpath([accounts_root, account_dir]) == accounts_root and os.path.isdir(account_dir):
                shutil.rmtree(account_dir)

            dialog.destroy()
            messagebox.showinfo("成功", f"账号「{username}」已注销")

        action_frame = tk.Frame(panel, bg=COLORS["panel"])
        action_frame.pack(fill="x", side=tk.BOTTOM, pady=(22, 0))
        action_frame.columnconfigure(0, weight=1)
        tk.Button(action_frame, text="永久注销", command=confirm_delete,
                  bg=COLORS["danger"], fg="white", relief=tk.FLAT, font=(FONT_FAMILY, 10), width=12, pady=8).grid(row=0, column=1, padx=(0, 8), sticky="e")
        tk.Button(action_frame, text="取消", command=dialog.destroy,
                  bg="#e2e8f0", fg=COLORS["text"], relief=tk.FLAT, font=(FONT_FAMILY, 10), width=10, pady=8).grid(row=0, column=2, sticky="e")
        password_entry.focus_set()
        dialog.bind("<Return>", lambda _event: confirm_delete())

    def show_leaderboard(self):
        dialog = Toplevel(self.root)
        dialog.title("积分排行榜")
        dialog.geometry("650x520")
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self.root)

        container = tk.Frame(dialog, bg=COLORS["bg"], padx=22, pady=20)
        container.pack(fill=tk.BOTH, expand=True)
        header = tk.Frame(container, bg=COLORS["bg"])
        header.pack(fill="x")
        tk.Label(header, text="积分排行榜", font=(FONT_FAMILY, 18, "bold"),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(side=tk.LEFT)
        tk.Button(header, text="刷新", command=lambda: load_data(),
                  bg=COLORS["primary"], fg="white", relief=tk.FLAT, font=(FONT_FAMILY, 10), padx=14, pady=7).pack(side=tk.RIGHT)

        account_text = f"当前账号：{self.account_session['username']}" if self.account_session else "当前未登录，仅可查看排行榜"
        status_label = tk.Label(container, text=account_text, font=(FONT_FAMILY, 10),
                                fg=COLORS["muted"], bg=COLORS["bg"])
        status_label.pack(anchor="w", pady=(4, 12))

        list_frame = tk.Frame(container, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
        list_frame.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        columns = ("rank", "username", "points", "honor", "updated")
        tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            yscrollcommand=scrollbar.set,
            style="Task.Treeview",
            selectmode="browse",
        )
        headings = {
            "rank": "排名",
            "username": "用户",
            "points": "积分",
            "honor": "称号",
            "updated": "更新时间",
        }
        widths = {"rank": 70, "username": 150, "points": 90, "honor": 130, "updated": 160}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor=tk.CENTER, stretch=column == "username")
        tree.tag_configure("me", foreground=COLORS["primary"])
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(14, 0), pady=14)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 14), pady=14)
        scrollbar.config(command=tree.yview)

        def load_data():
            if self.account_session:
                self.sync_points_to_server(show_success=False)
            for item in tree.get_children():
                tree.delete(item)
            try:
                result = score_server_request("GET", "/leaderboard")
            except RuntimeError as exc:
                status_label.config(text=str(exc), fg=COLORS["danger"])
                return
            rows = result.get("leaderboard", [])
            for row in rows:
                tags = ("me",) if self.account_session and row["username"] == self.account_session["username"] else ()
                tree.insert(
                    "",
                    tk.END,
                    values=(row["rank"], row["username"], row["points"], row["honor_title"], row["updated_at"]),
                    tags=tags,
                )
            status_label.config(text=f"已加载 {len(rows)} 位用户", fg=COLORS["muted"])

        button_frame = tk.Frame(container, bg=COLORS["bg"])
        button_frame.pack(fill="x", pady=(14, 0))
        tk.Button(button_frame, text="同步我的积分", command=lambda: self.sync_points_to_server(show_success=True),
                  bg=COLORS["success"], fg="white", relief=tk.FLAT, font=(FONT_FAMILY, 10), padx=14, pady=8).pack(side=tk.LEFT)
        tk.Button(button_frame, text="关闭", command=dialog.destroy,
                  bg="#e2e8f0", fg=COLORS["text"], relief=tk.FLAT, font=(FONT_FAMILY, 10), padx=14, pady=8).pack(side=tk.RIGHT)
        load_data()

    def create_widgets(self):
        self.setup_styles()
        self.root.configure(bg=COLORS["bg"])
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="刷新数据", command=self.refresh_list)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.quit)
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="视图", menu=view_menu)
        view_menu.add_command(label="统计面板", command=self.show_statistics)
        view_menu.add_command(label="生成图表", command=lambda: show_chart(self.dm, self.schedule))
        view_menu.add_command(label="日历页面", command=self.show_calendar_tab)
        account_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="账号", menu=account_menu)
        account_menu.add_command(label="登录 / 注册", command=self.show_account_dialog)
        account_menu.add_command(label="同步积分", command=lambda: self.sync_points_to_server(show_success=True))
        account_menu.add_command(label="积分排行榜", command=self.show_leaderboard)
        account_menu.add_separator()
        account_menu.add_command(label="退出登录", command=self.logout_account)
        account_menu.add_command(label="注销账号", command=self.delete_account)
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="关于", command=self.show_about)

        main_frame = tk.Frame(self.root, bg=COLORS["bg"])
        main_frame.pack(fill=tk.BOTH, expand=True)

        left_frame = tk.Frame(main_frame, padx=18, pady=20, bg=COLORS["sidebar"], width=210)
        left_frame.pack(side=tk.LEFT, fill=tk.Y)
        left_frame.pack_propagate(False)
        tk.Label(
            left_frame,
            text="智能日程",
            font=(FONT_FAMILY, 18, "bold"),
            fg="white",
            bg=COLORS["sidebar"],
        ).pack(anchor="w")
        tk.Label(
            left_frame,
            text="任务 · 番茄钟 · 统计",
            font=(FONT_FAMILY, 9),
            fg="#94a3b8",
            bg=COLORS["sidebar"],
        ).pack(anchor="w", pady=(4, 22))

        profile_panel = tk.Frame(left_frame, bg="#22304a", padx=12, pady=10)
        profile_panel.pack(fill="x", pady=(0, 16))
        profile_panel.bind("<Button-1>", self.show_honor_overview)
        profile_panel.config(cursor="hand2")
        profile_header_label = tk.Label(
            profile_panel,
            text="积分称号",
            font=(FONT_FAMILY, 9),
            fg="#94a3b8",
            bg="#22304a",
            cursor="hand2",
        )
        profile_header_label.pack(anchor="w")
        self.honor_title_label = tk.Label(
            profile_panel,
            text="起步者",
            font=(FONT_FAMILY, 13, "bold"),
            fg="white",
            bg="#22304a",
            cursor="hand2",
        )
        self.honor_title_label.pack(anchor="w", pady=(3, 0))
        self.points_status_label = tk.Label(
            profile_panel,
            text="0 积分",
            font=(FONT_FAMILY, 9),
            fg="#dbe4f0",
            bg="#22304a",
            cursor="hand2",
        )
        self.points_status_label.pack(anchor="w", pady=(2, 0))
        self.next_honor_status_label = tk.Label(
            profile_panel,
            text="",
            font=(FONT_FAMILY, 8),
            fg="#94a3b8",
            bg="#22304a",
            wraplength=160,
            justify=tk.LEFT,
            cursor="hand2",
        )
        self.next_honor_status_label.pack(anchor="w", pady=(2, 0))
        for widget in (
            profile_header_label,
            self.honor_title_label,
            self.points_status_label,
            self.next_honor_status_label,
        ):
            widget.bind("<Button-1>", self.show_honor_overview)

        self.account_status_label = tk.Label(
            left_frame,
            text="未登录",
            font=(FONT_FAMILY, 9),
            fg="#94a3b8",
            bg=COLORS["sidebar"],
        )
        self.account_status_label.pack(anchor="w", pady=(0, 8))
        self.create_button(left_frame, "登录 / 注册", self.show_account_dialog, "muted", 18).pack(fill="x", pady=3)
        self.create_button(left_frame, "积分排行榜", self.show_leaderboard, "muted", 18).pack(fill="x", pady=3)
        self.create_button(left_frame, "退出登录", self.logout_account, "danger", 18).pack(fill="x", pady=3)
        self.create_button(left_frame, "注销账号", self.delete_account, "danger", 18).pack(fill="x", pady=3)

        self.create_button(left_frame, "未完成任务", lambda: self.select_tab_by_type("uncompleted"), "sidebar", 18).pack(fill="x", pady=3)
        self.create_button(left_frame, "待定任务", lambda: self.select_tab_by_type("pending"), "sidebar", 18).pack(fill="x", pady=3)
        self.create_button(left_frame, "已完成任务", lambda: self.select_tab_by_type("completed"), "sidebar", 18).pack(fill="x", pady=3)
        self.create_button(left_frame, "日历计划", lambda: self.select_tab_by_type("calendar"), "sidebar", 18).pack(fill="x", pady=3)

        tk.Frame(left_frame, bg="#2b3854", height=1).pack(fill="x", pady=18)
        self.create_button(left_frame, "新建任务", self.new_task, "primary", 18).pack(fill="x", pady=4)
        self.create_button(left_frame, "开始专注", self.start_pomodoro, "warning", 18).pack(fill="x", pady=4)
        self.create_button(left_frame, "查看统计", self.show_statistics, "muted", 18).pack(fill="x", pady=4)

        right_frame = tk.Frame(main_frame, padx=20, pady=18, bg=COLORS["bg"])
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        header_frame = tk.Frame(right_frame, bg=COLORS["bg"])
        header_frame.pack(fill="x", pady=(0, 12))
        title_frame = tk.Frame(header_frame, bg=COLORS["bg"])
        title_frame.pack(side=tk.LEFT)
        tk.Label(
            title_frame,
            text="任务工作台",
            font=(FONT_FAMILY, 20, "bold"),
            fg=COLORS["text"],
            bg=COLORS["bg"],
        ).pack(anchor="w")
        self.summary_label = tk.Label(
            title_frame,
            text="",
            font=(FONT_FAMILY, 10),
            fg=COLORS["muted"],
            bg=COLORS["bg"],
        )
        self.summary_label.pack(anchor="w", pady=(2, 0))

        toolbar = tk.Frame(header_frame, bg=COLORS["bg"])
        toolbar.pack(side=tk.RIGHT)
        self.create_button(toolbar, "完成", self.complete_task, "success").pack(side=tk.LEFT, padx=3)
        self.create_button(toolbar, "恢复", self.restore_task, "muted").pack(side=tk.LEFT, padx=3)
        self.create_button(toolbar, "编辑", self.edit_task, "muted").pack(side=tk.LEFT, padx=3)
        self.create_button(toolbar, "删除", self.delete_task, "danger").pack(side=tk.LEFT, padx=3)

        search_frame = tk.Frame(right_frame, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
        search_frame.pack(fill="x", pady=(0, 12))
        tk.Label(search_frame, text="搜索", font=(FONT_FAMILY, 10), fg=COLORS["muted"], bg=COLORS["panel"]).pack(side=tk.LEFT, padx=(12, 6), pady=9)
        self.search_entry = tk.Entry(search_frame, font=(FONT_FAMILY, 11), bd=0, relief=tk.FLAT, bg=COLORS["panel"], fg=COLORS["text"])
        self.search_entry.pack(side=tk.LEFT, fill="x", expand=True, padx=(0, 12), pady=9)
        self.search_entry.bind("<KeyRelease>", lambda e: self.refresh_list())

        cards_frame = tk.Frame(right_frame, bg=COLORS["bg"])
        cards_frame.pack(fill="x", pady=(0, 12))
        self.summary_cards = {}
        for key, title in [("total", "总任务"), ("uncompleted", "未完成"), ("pending", "待定"), ("completed", "已完成")]:
            card = tk.Frame(cards_frame, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
            card.pack(side=tk.LEFT, fill="x", expand=True, padx=(0, 10))
            tk.Label(card, text=title, font=(FONT_FAMILY, 9), fg=COLORS["muted"], bg=COLORS["panel"]).pack(anchor="w", padx=14, pady=(10, 0))
            value = tk.Label(card, text="0", font=(FONT_FAMILY, 18, "bold"), fg=COLORS["text"], bg=COLORS["panel"])
            value.pack(anchor="w", padx=14, pady=(0, 10))
            self.summary_cards[key] = value

        self.notebook = ttk.Notebook(right_frame, style="App.TNotebook")
        self.notebook.pack(fill="both", expand=True)
        self.uncompleted_frame = tk.Frame(self.notebook, bg=COLORS["panel"])
        self.notebook.add(self.uncompleted_frame, text="未完成")
        self.pending_frame = tk.Frame(self.notebook, bg=COLORS["panel"])
        self.notebook.add(self.pending_frame, text="待定")
        self.completed_frame = tk.Frame(self.notebook, bg=COLORS["panel"])
        self.notebook.add(self.completed_frame, text="已完成")
        self.calendar_frame = tk.Frame(self.notebook, bg=COLORS["panel"])
        self.notebook.add(self.calendar_frame, text="日历")
        self.task_views = {}
        self.create_task_list(self.uncompleted_frame, "uncompleted")
        self.create_task_list(self.pending_frame, "pending")
        self.create_task_list(self.completed_frame, "completed")
        self.create_calendar_page(self.calendar_frame)

        self.status_bar = tk.Label(self.root, text="就绪", bd=0, bg="#e8eef6", fg=COLORS["muted"], anchor=tk.W, padx=12, pady=5)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def create_task_list(self, parent, list_type):
        list_frame = tk.Frame(parent, bg=COLORS["panel"], padx=12, pady=12)
        list_frame.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        columns_map = {
            "uncompleted": ("priority", "tomato", "title", "due"),
            "pending": ("scheduled", "priority", "tomato", "title", "due"),
            "completed": ("tomato", "title", "finished", "cost"),
        }
        heading_map = {
            "priority": "优先级",
            "tomato": "番茄进度",
            "title": "任务标题",
            "due": "截止时间",
            "scheduled": "计划进入",
            "finished": "完成时间",
            "cost": "耗时",
        }
        width_map = {
            "priority": 80,
            "tomato": 90,
            "title": 360,
            "due": 150,
            "scheduled": 120,
            "finished": 150,
            "cost": 90,
        }
        tree = ttk.Treeview(
            list_frame,
            columns=columns_map[list_type],
            show="headings",
            yscrollcommand=scrollbar.set,
            style="Task.Treeview",
            selectmode="browse",
        )
        for column in columns_map[list_type]:
            tree.heading(column, text=heading_map[column])
            anchor = tk.W if column == "title" else tk.CENTER
            stretch = column == "title"
            tree.column(column, width=width_map[column], minwidth=70, anchor=anchor, stretch=stretch)
        tree.tag_configure("high", foreground="#b91c1c")
        tree.tag_configure("medium", foreground="#92400e")
        tree.tag_configure("low", foreground="#166534")
        tree.tag_configure("completed", foreground=COLORS["muted"])
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        scrollbar.config(command=tree.yview)
        if list_type == "completed":
            self.completed_list = tree
        elif list_type == "pending":
            self.pending_list = tree
        else:
            self.uncompleted_list = tree
        self.task_views[list_type] = tree
        tree.bind("<Double-Button-1>", lambda e: self.edit_task(list_type))

    def clear_task_view(self, tree):
        for item in tree.get_children():
            tree.delete(item)

    def task_matches_search(self, task, search_text):
        if not search_text:
            return True
        return search_text in task.title.lower() or search_text in task.description.lower()

    def insert_task_row(self, tree, list_type, task):
        priority = self.priority_label(task.priority)
        tomato = f"{task.completed_pomodoros}/{task.target_pomodoros}"
        due = self.format_datetime_label(task.due_date)
        tags = {1: ("high",), 2: ("medium",), 3: ("low",)}.get(task.priority, ("low",))
        if list_type == "uncompleted":
            values = (priority, tomato, task.title, due)
        elif list_type == "pending":
            scheduled = self.format_datetime_label(task.scheduled_date, "%Y-%m-%d")
            values = (scheduled, priority, tomato, task.title, due)
        else:
            finish = self.format_datetime_label(task.finished_at)
            values = (tomato, task.title, finish, format_duration(task.cost_sec))
            tags = ("completed",)
        tree.insert("", tk.END, iid=task.id, values=values, tags=tags)

    def refresh_list(self):
        self.release_due_pending_tasks()
        self.clear_task_view(self.uncompleted_list)
        self.clear_task_view(self.pending_list)
        self.clear_task_view(self.completed_list)
        self.uncompleted_task_ids.clear()
        self.pending_task_ids.clear()
        self.completed_task_ids.clear()
        self.schedule.smart_sort()
        search_text = self.search_entry.get().strip().lower()
        uncompleted = self.schedule.get_uncompleted_tasks()
        pending = self.schedule.get_pending_tasks()
        completed = self.schedule.get_completed_tasks()

        for task in uncompleted:
            if not self.task_matches_search(task, search_text):
                continue
            self.insert_task_row(self.uncompleted_list, "uncompleted", task)
            self.uncompleted_task_ids.append(task.id)

        for task in pending:
            if not self.task_matches_search(task, search_text):
                continue
            self.insert_task_row(self.pending_list, "pending", task)
            self.pending_task_ids.append(task.id)

        for task in completed:
            if not self.task_matches_search(task, search_text):
                continue
            self.insert_task_row(self.completed_list, "completed", task)
            self.completed_task_ids.append(task.id)

        total = len(self.schedule.tasks)
        completed = sum(1 for t in self.schedule.tasks if t.completed)
        pending_count = len(self.schedule.get_pending_tasks())
        uncompleted_count = len(self.schedule.get_uncompleted_tasks())
        self.summary_cards["total"].config(text=str(total))
        self.summary_cards["uncompleted"].config(text=str(uncompleted_count))
        self.summary_cards["pending"].config(text=str(pending_count))
        self.summary_cards["completed"].config(text=str(completed))
        if self.account_session:
            self.summary_label.config(text=f"今天还有 {uncompleted_count} 个可执行任务，{pending_count} 个未来计划")
        else:
            self.summary_label.config(text="请先登录账号，登录后加载该账号的独立任务数据")
        points_profile = self.refresh_points_display()
        self.status_bar.config(
            text=(
                f"总任务: {total} | 未完成: {uncompleted_count} | 待定: {pending_count} | "
                f"已完成: {completed} | 积分: {points_profile['points']} | 称号: {points_profile['title']}"
            )
        )
        self.refresh_calendar()

    def get_selected_id(self, list_type):
        try:
            if list_type == "auto":
                list_type = self.get_current_list_type()
            if list_type in (True, "completed"):
                tree = self.completed_list
            elif list_type == "pending":
                tree = self.pending_list
            elif list_type == "calendar":
                messagebox.showwarning("提示", "请先切换到任务列表并选择任务")
                return None
            else:
                tree = self.uncompleted_list
            selected = tree.selection()
            if not selected:
                raise IndexError
            return selected[0]
        except:
            messagebox.showwarning("提示", "请选择任务")
            return None

    def new_task(self):
        if not self.account_session:
            messagebox.showinfo("提示", "请先登录账号后创建任务")
            return
        TaskDetailWindow(self.root, self)

    def edit_task(self, list_type="auto"):
        tid = self.get_selected_id(list_type)
        if tid:
            task = self.schedule.get_task_by_id(tid)
            if task:
                TaskDetailWindow(self.root, self, task)

    def delete_task(self):
        tid = self.get_selected_id("auto")
        if tid:
            task = self.schedule.get_task_by_id(tid)
            if task and messagebox.askyesno("确认", f"确定删除任务「{task.title}」吗？"):
                self.schedule.remove_task(tid)
                self.dm.delete_task_history(tid)
                self.dm.save_tasks([t.to_dict() for t in self.schedule.tasks])
                self.refresh_list()
                messagebox.showinfo("成功", "任务已删除")

    def complete_task(self):
        tid = self.get_selected_id("uncompleted")
        if tid:
            task = self.schedule.get_task_by_id(tid)
            if task and not task.completed:
                dialog = Toplevel(self.root)
                dialog.title("任务完成")
                dialog.geometry("390x260")
                dialog.configure(bg=COLORS["bg"])
                dialog.transient(self.root)
                dialog.grab_set()

                panel = tk.Frame(dialog, bg=COLORS["panel"], padx=22, pady=20)
                panel.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
                tk.Label(panel, text="标记完成", font=(FONT_FAMILY, 16, "bold"),
                         fg=COLORS["text"], bg=COLORS["panel"]).pack(anchor="w")
                tk.Label(panel, text=f"任务：{task.title}", font=(FONT_FAMILY, 10),
                         fg=COLORS["muted"], bg=COLORS["panel"]).pack(anchor="w", pady=(4, 14))
                tk.Label(panel, text="实际耗时（秒）", font=(FONT_FAMILY, 10, "bold"),
                         fg=COLORS["text"], bg=COLORS["panel"]).pack(anchor="w")
                cost_var = tk.StringVar(value="1500")
                cost_entry = tk.Entry(panel, textvariable=cost_var, font=(FONT_FAMILY, 11), relief=tk.SOLID, bd=1)
                cost_entry.pack(fill="x", pady=(6, 6))
                tk.Label(panel, text="耗时会计入任务历史与统计图表", font=(FONT_FAMILY, 9),
                         fg=COLORS["muted"], bg=COLORS["panel"]).pack(anchor="w")

                def confirm_complete():
                    try:
                        cost = int(cost_var.get())
                        if cost <= 0:
                            raise ValueError
                        if cost > 28800:
                            if not messagebox.askyesno("确认", f"耗时{cost}秒超过8小时，确认吗？"):
                                return
                    except:
                        messagebox.showerror("错误", "请输入有效的秒数（1-28800）")
                        return
                    task.completed = True
                    task.finished_at = datetime.now()
                    task.cost_sec = cost
                    self.dm.save_task_history(task)
                    self.dm.save_tasks([t.to_dict() for t in self.schedule.tasks])
                    send_desktop_notification("任务完成", f"恭喜完成：{task.title}\n实际耗时：{cost}秒")
                    dialog.destroy()
                    self.refresh_list()
                    self.show_task_completion_dialog(task)
                action_frame = tk.Frame(panel, bg=COLORS["panel"])
                action_frame.pack(fill="x", pady=(18, 0))
                tk.Button(action_frame, text="确认完成", command=confirm_complete,
                          bg=COLORS["success"], fg="white", relief=tk.FLAT, font=(FONT_FAMILY, 10), padx=14, pady=8).pack(side=tk.LEFT)
                tk.Button(action_frame, text="取消", command=dialog.destroy,
                          bg="#e2e8f0", fg=COLORS["text"], relief=tk.FLAT, font=(FONT_FAMILY, 10), padx=14, pady=8).pack(side=tk.LEFT, padx=8)
        elif tid and task and task.completed:
            messagebox.showinfo("提示", "任务已经完成了")

    def restore_task(self):
        tid = self.get_selected_id("completed")
        if not tid:
            return
        task = self.schedule.get_task_by_id(tid)
        if not task or not task.completed:
            messagebox.showinfo("提示", "请选择已完成任务")
            return
        if not messagebox.askyesno("确认", f"确定将任务「{task.title}」恢复为未完成吗？"):
            return
        task.completed = False
        task.finished_at = None
        task.cost_sec = 0
        task.completed_pomodoros = 0
        task.scheduled_date = None
        self.dm.delete_task_history(task.id)
        self.dm.save_tasks([t.to_dict() for t in self.schedule.tasks])
        self.refresh_list()
        messagebox.showinfo("成功", "任务已恢复到未完成列表")

    def release_due_pending_tasks(self):
        today = datetime.now().date()
        changed = False
        for task in self.schedule.tasks:
            if not task.completed and task.scheduled_date and task.scheduled_date.date() <= today:
                task.scheduled_date = None
                changed = True
        if changed:
            self.dm.save_tasks([t.to_dict() for t in self.schedule.tasks])

    def schedule_pending_refresh(self):
        self.release_due_pending_tasks()
        self.refresh_list()
        self.root.after(60000, self.schedule_pending_refresh)

    def show_calendar_tab(self):
        self.notebook.select(self.calendar_frame)

    def create_calendar_page(self, parent):
        parent.configure(bg=COLORS["panel"])
        top_frame = tk.Frame(parent, bg=COLORS["panel"])
        top_frame.pack(fill="x", padx=14, pady=14)
        self.create_button(top_frame, "上个月", self.prev_month, "muted", 10).pack(side=tk.LEFT)
        self.calendar_title = tk.Label(top_frame, text="", font=(FONT_FAMILY, 15, "bold"), bg=COLORS["panel"], fg=COLORS["text"])
        self.calendar_title.pack(side=tk.LEFT, expand=True)
        self.create_button(top_frame, "下个月", self.next_month, "muted", 10).pack(side=tk.RIGHT)

        self.calendar_grid = tk.Frame(parent, bg=COLORS["panel"])
        self.calendar_grid.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    def refresh_calendar(self):
        if not hasattr(self, "calendar_grid"):
            return
        for widget in self.calendar_grid.winfo_children():
            widget.destroy()

        self.calendar_title.config(text=f"{self.calendar_year}年{self.calendar_month}月")
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        for col, weekday in enumerate(weekdays):
            label = tk.Label(self.calendar_grid, text=weekday, font=(FONT_FAMILY, 10, "bold"),
                             fg=COLORS["muted"], bg="#eef3f8", relief=tk.FLAT)
            label.grid(row=0, column=col, sticky="nsew", padx=2, pady=2)
            self.calendar_grid.columnconfigure(col, weight=1)

        month_days = calendar.monthcalendar(self.calendar_year, self.calendar_month)
        today = datetime.now().date()
        task_counts = {}
        for task in self.schedule.tasks:
            if not task.completed and task.scheduled_date:
                task_date = task.scheduled_date.date()
                task_counts[task_date] = task_counts.get(task_date, 0) + 1

        for row_index, week in enumerate(month_days, start=1):
            self.calendar_grid.rowconfigure(row_index, weight=1)
            for col, day in enumerate(week):
                if day == 0:
                    blank = tk.Label(self.calendar_grid, text="", relief=tk.FLAT, bg="#f8fafc")
                    blank.grid(row=row_index, column=col, sticky="nsew", padx=2, pady=2)
                    continue
                current_date = datetime(self.calendar_year, self.calendar_month, day)
                current_day = current_date.date()
                count = task_counts.get(current_day, 0)
                text = f"{day}"
                if count:
                    text += f"\n待定 {count}"
                if current_day == today:
                    text += "\n今天"
                bg = "#dbeafe" if current_day == today else COLORS["panel"]
                fg = COLORS["text"]
                if current_day < today:
                    bg = "#f1f5f9"
                    fg = "#94a3b8"
                if count:
                    bg = "#ecfdf5" if current_day != today else "#bfdbfe"
                button = tk.Button(
                    self.calendar_grid,
                    text=text,
                    bg=bg,
                    fg=fg,
                    activebackground=bg,
                    activeforeground=fg,
                    relief=tk.FLAT,
                    bd=0,
                    font=(FONT_FAMILY, 10),
                    command=lambda d=current_date: self.new_calendar_task(d)
                )
                button.grid(row=row_index, column=col, sticky="nsew", padx=2, pady=2)

    def prev_month(self):
        if self.calendar_month == 1:
            self.calendar_month = 12
            self.calendar_year -= 1
        else:
            self.calendar_month -= 1
        self.refresh_calendar()

    def next_month(self):
        if self.calendar_month == 12:
            self.calendar_month = 1
            self.calendar_year += 1
        else:
            self.calendar_month += 1
        self.refresh_calendar()

    def new_calendar_task(self, selected_date):
        TaskDetailWindow(self.root, self, scheduled_date=selected_date)

    def start_pomodoro(self):
        if not self.account_session:
            messagebox.showinfo("提示", "请先登录账号后开始专注")
            return
        tid = self.get_selected_id("uncompleted")
        if tid:
            timer = PomodoroTimer(self, self.dm, tid)
            timer.start()

    def show_statistics(self):
        StatisticsWindow(self.root, self.dm, self.schedule)

    def show_about(self):
        about_text = """智能日程管理器 v2.0
功能特性：
✅ 任务管理与优先级排序
✅ 番茄钟计时器（达标自动完成任务）
✅ 积分称号与完成激励
✅ 账号登录与积分排行榜
✅ 数据统计与图表
✅ 桌面通知提醒
版本：2026.04"""
        messagebox.showinfo("关于", about_text)

# ====================== 程序入口 ======================
if __name__ == "__main__":
    root = tk.Tk()
    app = AppGUI(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        root.destroy()
