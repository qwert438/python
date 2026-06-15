from datetime import datetime
import json
import os
import re
import sqlite3


POINTS_PER_POMODORO = 25
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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


class Task:
    def __init__(self, title, description="", due_date=None, priority=3, scheduled_date=None):
        self.id = datetime.now().strftime("%Y%m%d%H%M%S%f")
        self.title = title
        self.description = description
        self.due_date = due_date
        self.scheduled_date = scheduled_date
        self.priority = priority
        self.completed = False
        self.created_at = datetime.now()
        self.finished_at = None
        self.cost_sec = 0
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
            "completed_pomodoros": self.completed_pomodoros,
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
        task = Task(data["title"], data.get("description", ""))
        task.id = data["id"]
        task.due_date = Task.parse_saved_datetime(data.get("due_date"))
        task.scheduled_date = Task.parse_saved_datetime(data.get("scheduled_date"))
        task.priority = data.get("priority", 3)
        task.completed = data.get("completed", False)
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


class Schedule:
    def __init__(self):
        self.tasks = []

    def add_task(self, task):
        self.tasks.append(task)

    def remove_task(self, task_id):
        self.tasks = [task for task in self.tasks if task.id != task_id]

    def get_task_by_id(self, task_id):
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    @staticmethod
    def priority_sort_key(task):
        due_date = task.due_date or datetime.max
        return task.priority, due_date, task.created_at

    def get_statistics(self):
        total = len(self.tasks)
        completed = sum(1 for task in self.tasks if task.completed)
        completion_rate = (completed / total * 100) if total > 0 else 0
        priority_stats = {1: 0, 2: 0, 3: 0}
        for task in self.tasks:
            if task.completed:
                priority_stats[task.priority] = priority_stats.get(task.priority, 0) + 1
        return {
            "total": total,
            "completed": completed,
            "completion_rate": completion_rate,
            "priority_stats": priority_stats,
        }

    def get_uncompleted_tasks(self):
        return sorted(
            [task for task in self.tasks if not task.completed and not task.is_pending()],
            key=self.priority_sort_key,
        )

    def get_completed_tasks(self):
        return sorted(
            [task for task in self.tasks if task.completed],
            key=lambda task: task.finished_at or task.created_at,
            reverse=True,
        )

    def get_pending_tasks(self):
        return sorted(
            [task for task in self.tasks if task.is_pending()],
            key=lambda task: (task.scheduled_date, task.priority, task.created_at),
        )


class DataManager:
    def __init__(self, username=None):
        self.username = username
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
        cur.execute(
            """CREATE TABLE IF NOT EXISTS pomodoro_records
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             start_time TEXT,
             end_time TEXT,
             phase TEXT,
             duration INT,
             task_id TEXT)"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS task_history
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             task_id TEXT,
             title TEXT,
             completed_at TEXT,
             cost_sec INT)"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS user_profile
            (key TEXT PRIMARY KEY,
             value TEXT NOT NULL)"""
        )
        cur.execute("SELECT value FROM user_profile WHERE key='total_points'")
        if cur.fetchone() is None:
            cur.execute("INSERT INTO user_profile (key, value) VALUES (?, ?)", ("total_points", "0"))
        conn.commit()
        conn.close()

    def set_profile_value(self, key, value):
        if not self.db_path:
            return
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO user_profile (key, value) VALUES (?, ?)", (key, str(value)))
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

    def save_tasks(self, tasks_data):
        if not self.json_path:
            return
        with open(self.json_path, "w", encoding="utf-8") as file:
            json.dump(tasks_data, file, ensure_ascii=False, indent=2)

    def load_tasks(self):
        if not self.json_path:
            return []
        try:
            with open(self.json_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (OSError, json.JSONDecodeError):
            return []

    def save_pomodoro(self, start_time, end_time, phase, task_id=None):
        if not self.db_path:
            return False, None, None
        duration = int((end_time - start_time).total_seconds())
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pomodoro_records (start_time, end_time, phase, duration, task_id) VALUES (?,?,?,?,?)",
            (
                start_time.strftime("%Y-%m-%d %H:%M:%S"),
                end_time.strftime("%Y-%m-%d %H:%M:%S"),
                phase,
                duration,
                task_id,
            ),
        )
        conn.commit()
        conn.close()

        points_update = self.add_pomodoro_points() if phase == "work" else None
        if phase == "work" and task_id:
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
        new_profile.update(
            {
                "earned": POINTS_PER_POMODORO,
                "old_points": old_profile["points"],
                "old_title": old_profile["title"],
                "leveled_up": old_profile["title"] != new_profile["title"],
            }
        )
        return new_profile

    def update_task_pomodoro_count(self, task_id):
        if not self.json_path:
            return False, None
        tasks_data = self.load_tasks()
        target_task_data = None

        for index, task_data in enumerate(tasks_data):
            if task_data["id"] == task_id and not task_data["completed"]:
                task_data["completed_pomodoros"] = task_data.get("completed_pomodoros", 0) + 1
                target = task_data.get("target_pomodoros", 1)
                if task_data["completed_pomodoros"] >= target:
                    task_data["completed"] = True
                    task_data["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    created_at = datetime.strptime(task_data["created_at"], "%Y-%m-%d %H:%M:%S")
                    task_data["cost_sec"] = int((datetime.now() - created_at).total_seconds())
                tasks_data[index] = task_data
                target_task_data = task_data
                break

        if target_task_data:
            self.save_tasks(tasks_data)
            return True, target_task_data
        return False, None

    def save_task_history(self, task):
        if not self.db_path or not task.finished_at:
            return
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO task_history (task_id, title, completed_at, cost_sec) VALUES (?,?,?,?)",
            (task.id, task.title, task.finished_at.strftime("%Y-%m-%d %H:%M:%S"), task.cost_sec),
        )
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
            return {"today_focus": 0, "weekly_count": 0, "total_focus": 0}
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT SUM(duration) FROM pomodoro_records WHERE DATE(start_time) = DATE('now') AND phase='work'")
        today_focus = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM pomodoro_records WHERE DATE(start_time) >= DATE('now', '-7 days') AND phase='work'")
        weekly_count = cur.fetchone()[0] or 0
        cur.execute("SELECT SUM(duration) FROM pomodoro_records WHERE phase='work'")
        total_focus = cur.fetchone()[0] or 0
        conn.close()
        return {
            "today_focus": today_focus,
            "weekly_count": weekly_count,
            "total_focus": total_focus,
        }
