# -*- coding: utf-8 -*-
"""
智能日程管理器 - Flask Web 界面
功能与 main.py 完全一致，通过浏览器访问
启动: python web_app.py  ->  http://127.0.0.1:5000
"""
import io, os, json, sqlite3, re, random, secrets, shutil, threading, time, difflib
from datetime import datetime, timedelta

from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file

import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
plt.rcParams["font.family"] = ["SimHei", "Microsoft YaHei"]

# ====================== 常量 ======================
POINTS_PER_POMODORO = 25
SCORE_SERVER_URL = "http://127.0.0.1:8000"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GLOBAL_DB_PATH = os.path.join(BASE_DIR, "app_global.db")
ACCOUNTS_DIR = os.path.join(BASE_DIR, "accounts")
HONOR_LEVELS = [
    (0, "起步者"), (50, "坚持新星"), (150, "专注学徒"), (350, "恒心行者"),
    (700, "毅力达人"), (1200, "破局先锋"), (2000, "长期主义者"), (3200, "自律大师"),
    (5000, "登峰者"),
]
PERSEVERANCE_QUOTES = [
    ("锲而不舍，金石可镂。", "《荀子》"), ("路漫漫其修远兮，吾将上下而求索。", "屈原"),
    ("千磨万击还坚劲，任尔东西南北风。", "郑燮"), ("宝剑锋从磨砺出，梅花香自苦寒来。", "古训"),
    ("绳锯木断，水滴石穿。", "古训"), ("不积跬步，无以至千里；不积小流，无以成江海。", "《荀子》"),
    ("合抱之木，生于毫末；九层之台，起于累土。", "《道德经》"), ("行百里者半九十。", "《战国策》"),
    ("精诚所至，金石为开。", "王充"), ("伟大的作品不是靠力量，而是靠坚持来完成的。", "约翰逊"),
]

web_app = Flask(__name__)
web_app.secret_key = secrets.token_hex(32)

# ====================== Task 实体 ======================
class Task:
    def __init__(self, title, description="", due_date=None, priority=3, scheduled_date=None):
        self.id = datetime.now().strftime("%Y%m%d%H%M%S%f"); self.title = title
        self.description = description; self.due_date = due_date
        self.scheduled_date = scheduled_date; self.priority = priority
        self.completed = False; self.created_at = datetime.now(); self.finished_at = None
        self.cost_sec = 0; self.target_pomodoros = 1; self.completed_pomodoros = 0

    def to_dict(self):
        return {"id": self.id, "title": self.title, "description": self.description,
                "due_date": self.due_date.strftime("%Y-%m-%d %H:%M") if self.due_date else None,
                "scheduled_date": self.scheduled_date.strftime("%Y-%m-%d") if self.scheduled_date else None,
                "priority": self.priority, "completed": self.completed,
                "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": self.finished_at.strftime("%Y-%m-%d %H:%M:%S") if self.finished_at else None,
                "cost_sec": self.cost_sec, "target_pomodoros": self.target_pomodoros,
                "completed_pomodoros": self.completed_pomodoros}

    @staticmethod
    def parse_saved_datetime(value):
        if not value: return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try: return datetime.strptime(value, fmt)
            except ValueError: pass
        return None

    @staticmethod
    def from_dict(data):
        task = Task(data["title"], data["description"]); task.id = data["id"]
        task.due_date = Task.parse_saved_datetime(data.get("due_date"))
        task.scheduled_date = Task.parse_saved_datetime(data.get("scheduled_date"))
        task.priority = data["priority"]; task.completed = data["completed"]
        task.created_at = datetime.strptime(data["created_at"], "%Y-%m-%d %H:%M:%S")
        if data.get("finished_at"): task.finished_at = datetime.strptime(data["finished_at"], "%Y-%m-%d %H:%M:%S")
        task.cost_sec = data.get("cost_sec", 0); task.target_pomodoros = data.get("target_pomodoros", 1)
        task.completed_pomodoros = data.get("completed_pomodoros", 0)
        return task

    def is_pending(self, today=None):
        if self.completed or not self.scheduled_date: return False
        return self.scheduled_date.date() > (today or datetime.now().date())

    def calculate_priority_score(self):
        now = datetime.now(); urgency = 0
        if self.due_date:
            hours_left = (self.due_date - now).total_seconds() / 3600
            if hours_left <= 0: urgency = 100
            elif hours_left <= 24: urgency = 80
            elif hours_left <= 72: urgency = 50
            elif hours_left <= 168: urgency = 20
            else: urgency = 10
        else: urgency = 5
        return urgency + (4 - self.priority) * 25 + (30 if not self.completed else 0)

class Schedule:
    def __init__(self): self.tasks = []
    def add_task(self, task): self.tasks.append(task)
    def remove_task(self, tid): self.tasks = [t for t in self.tasks if t.id != tid]
    def get_task_by_id(self, tid):
        for t in self.tasks:
            if t.id == tid: return t
        return None
    @staticmethod
    def priority_sort_key(task): return task.priority, (task.due_date or datetime.max), task.created_at
    def smart_sort(self): self.tasks.sort(key=self.priority_sort_key)

    def get_statistics(self):
        total = len(self.tasks); comp = sum(1 for t in self.tasks if t.completed)
        rate = (comp / total * 100) if total > 0 else 0
        priority_stats = {1: 0, 2: 0, 3: 0}
        for t in self.tasks:
            if t.completed: priority_stats[t.priority] += 1
        return {"total": total, "completed": comp, "completion_rate": rate,
                "uncompleted": total - comp, "pending": len([t for t in self.tasks if t.is_pending()]),
                "priority_stats": priority_stats}

    def get_uncompleted_tasks(self):
        return sorted([t for t in self.tasks if not t.completed and not t.is_pending()], key=self.priority_sort_key)
    def get_completed_tasks(self):
        return sorted([t for t in self.tasks if t.completed], key=lambda t: t.finished_at or t.created_at, reverse=True)
    def get_pending_tasks(self):
        return sorted([t for t in self.tasks if t.is_pending()], key=lambda t: (t.scheduled_date, t.priority, t.created_at))

# ====================== 工具函数 ======================
def safe_account_name(username):
    # 保留中文、字母、数字、下划线，其余字符替换为下划线
    return re.sub(r"[^\w一-鿿]", "_", username.strip()) or "user"

def get_account_dir(username):
    return os.path.join(ACCOUNTS_DIR, safe_account_name(username))

def get_honor_profile(points):
    ct, ctitle = HONOR_LEVELS[0]; nl = None
    for i, (th, title) in enumerate(HONOR_LEVELS):
        if points >= th:
            ct, ctitle = th, title
            nl = HONOR_LEVELS[i + 1] if i + 1 < len(HONOR_LEVELS) else None
        else: nl = (th, title); break
    if nl: nt, ntit = nl; ptn = nt - points
    else: nt = ntit = None; ptn = 0
    return {"points": points, "title": ctitle, "next_title": ntit, "next_threshold": nt, "points_to_next": ptn}

def format_duration(seconds):
    seconds = int(seconds or 0)
    if seconds < 60: return f"{seconds}秒"
    m, s = divmod(seconds, 60)
    if m < 60: return f"{m}分{s}秒" if s else f"{m}分"
    h, m = divmod(m, 60); return f"{h}时{m}分" if m else f"{h}时"

def parse_natural_datetime(value):
    """解析自然语言日期，date-only 格式默认 23:59（与 main.py 一致）"""
    value = value.strip()
    if not value: return None
    now = datetime.now()
    natural = {"明天": now + timedelta(days=1), "后天": now + timedelta(days=2), "今天": now}
    for text, dt in natural.items():
        if value == text: return dt.replace(hour=18, minute=0, second=0, microsecond=0)
    for i, name in enumerate(["周一", "周二", "周三", "周四", "周五", "周六", "周日"]):
        if value == f"下{name}":
            days_ahead = (i - now.weekday()) % 7 + 7
            return (now + timedelta(days=days_ahead)).replace(hour=18, minute=0, second=0, microsecond=0)
    # 带时间的格式
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try: return datetime.strptime(value, fmt)
        except ValueError: pass
    # 纯日期格式 → 默认 23:59（与 main.py 一致）
    try: return datetime.strptime(value, "%Y-%m-%d").replace(hour=23, minute=59, second=0)
    except ValueError: pass
    return None

# ====================== DataManager ======================
class DataManager:
    def __init__(self, username=None):
        self.username = username; self.is_account_data = bool(username)
        if username:
            ad = get_account_dir(username); os.makedirs(ad, exist_ok=True)
            self.json_path = os.path.join(ad, "tasks.json")
            self.history_path = os.path.join(ad, "history_task.json")  # 预测用历史数据
            self.db_path = os.path.join(ad, "pomodoro.db")
        else:
            self.json_path = None; self.history_path = None; self.db_path = None
        self.init_db()

    def init_db(self):
        if not self.db_path: return
        conn = sqlite3.connect(self.db_path); cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS pomodoro_records
        (id INTEGER PRIMARY KEY AUTOINCREMENT, start_time TEXT, end_time TEXT, phase TEXT, duration INT, task_id TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS task_history
        (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, title TEXT, completed_at TEXT, cost_sec INT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS user_profile (key TEXT PRIMARY KEY, value TEXT NOT NULL)''')
        cur.execute("SELECT value FROM user_profile WHERE key='total_points'")
        if cur.fetchone() is None: cur.execute("INSERT INTO user_profile (key,value) VALUES (?,?)", ("total_points", "0"))
        conn.commit(); conn.close()

    def set_profile_value(self, key, value):
        if not self.db_path: return
        conn = sqlite3.connect(self.db_path); cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO user_profile (key,value) VALUES (?,?)", (key, str(value)))
        conn.commit(); conn.close()

    def get_profile_value(self, key, default=None):
        if not self.db_path: return default
        conn = sqlite3.connect(self.db_path); cur = conn.cursor()
        cur.execute("SELECT value FROM user_profile WHERE key=?", (key,))
        row = cur.fetchone(); conn.close(); return row[0] if row else default

    def clear_profile_value(self, key):
        if not self.db_path: return
        conn = sqlite3.connect(self.db_path); cur = conn.cursor()
        cur.execute("DELETE FROM user_profile WHERE key=?", (key,)); conn.commit(); conn.close()

    def save_tasks(self, data):
        if not self.json_path: return
        with open(self.json_path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

    def load_tasks(self):
        if not self.json_path: return []
        try:
            with open(self.json_path, "r", encoding="utf-8") as f: return json.load(f)
        except: return []

    # ====== history_task.json：预测用历史数据存储 ======
    def load_history_tasks(self):
        """读取历史任务记录（容错：文件不存在或损坏返回空列表）"""
        if not self.history_path: return []
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []

    def append_history_task(self, title, target_pomodoros):
        """追加一条历史任务记录（任务描述+目标番茄数）"""
        if not self.history_path: return
        records = self.load_history_tasks()
        records.append({"title": title, "target_pomodoros": target_pomodoros})
        try:
            os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
            with open(self.history_path, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 写入失败静默处理，不崩溃

    def save_pomodoro(self, start_time, end_time, phase, task_id=None):
        if not self.db_path: return False, None, None
        duration = int((end_time - start_time).total_seconds())
        conn = sqlite3.connect(self.db_path); cur = conn.cursor()
        cur.execute("INSERT INTO pomodoro_records (start_time,end_time,phase,duration,task_id) VALUES (?,?,?,?,?)",
                    (start_time.strftime("%Y-%m-%d %H:%M:%S"), end_time.strftime("%Y-%m-%d %H:%M:%S"), phase, duration, task_id))
        conn.commit(); conn.close()
        points_update = self.add_pomodoro_points() if phase == 'work' else None
        if phase == 'work' and task_id:
            updated, task_data = self.update_task_pomodoro_count(task_id); return updated, task_data, points_update
        return False, None, points_update

    def get_total_points(self):
        try: return int(self.get_profile_value("total_points", 0) or 0)
        except: return 0

    def get_points_profile(self): return get_honor_profile(self.get_total_points())

    def add_pomodoro_points(self):
        old = self.get_points_profile(); np = old["points"] + POINTS_PER_POMODORO
        self.set_profile_value("total_points", np); new = get_honor_profile(np)
        self.add_plant_points(POINTS_PER_POMODORO)  # 同步增加培养积分
        new.update({"earned": POINTS_PER_POMODORO, "old_points": old["points"],
                    "old_title": old["title"], "leveled_up": old["title"] != new["title"]})
        return new

    def update_task_pomodoro_count(self, task_id):
        if not self.json_path: return False, None
        tasks = self.load_tasks(); updated = False; target = None
        for i, td in enumerate(tasks):
            if td['id'] == task_id and not td['completed']:
                td['completed_pomodoros'] = td.get('completed_pomodoros', 0) + 1
                tgt = td.get('target_pomodoros', 1)
                if td['completed_pomodoros'] >= tgt:
                    td['completed'] = True; td['finished_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ca = datetime.strptime(td['created_at'], "%Y-%m-%d %H:%M:%S")
                    td['cost_sec'] = int((datetime.now() - ca).total_seconds())
                tasks[i] = td; updated = True; target = td; break
        if updated: self.save_tasks(tasks)
        return updated, target

    def save_task_history(self, task):
        if not self.db_path: return
        conn = sqlite3.connect(self.db_path); cur = conn.cursor()
        cur.execute("INSERT INTO task_history (task_id,title,completed_at,cost_sec) VALUES (?,?,?,?)",
                    (task.id, task.title, task.finished_at.strftime("%Y-%m-%d %H:%M:%S"), task.cost_sec))
        conn.commit(); conn.close()

    def delete_task_history(self, task_id):
        if not self.db_path: return
        conn = sqlite3.connect(self.db_path); cur = conn.cursor()
        cur.execute("DELETE FROM task_history WHERE task_id=?", (task_id,)); conn.commit(); conn.close()

    def get_pomodoro_stats(self):
        if not self.db_path: return {"today_focus": 0, "weekly_count": 0, "total_focus": 0}
        conn = sqlite3.connect(self.db_path); cur = conn.cursor()
        cur.execute("SELECT SUM(duration) FROM pomodoro_records WHERE DATE(start_time)=DATE('now') AND phase='work'")
        tf = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM pomodoro_records WHERE DATE(start_time)>=DATE('now','-7 days') AND phase='work'")
        wc = cur.fetchone()[0] or 0
        cur.execute("SELECT SUM(duration) FROM pomodoro_records WHERE phase='work'")
        tot = cur.fetchone()[0] or 0
        conn.close(); return {"today_focus": tf, "weekly_count": wc, "total_focus": tot}

    def predict_task_duration(self, title=""):
        """基于历史完成情况的任务耗时预测（与 main.py 一致）"""
        if not self.db_path: return None
        conn = sqlite3.connect(self.db_path); cur = conn.cursor()
        cur.execute("SELECT title, cost_sec FROM task_history")
        rows = cur.fetchall(); conn.close()
        if not rows: return None
        all_costs = [max(int(c or 0), 1) for _, c in rows]
        global_avg = sum(all_costs) / len(all_costs) if all_costs else 0
        title_lower = title.lower().strip() if title else ""
        similar_costs = []
        if title_lower:
            for hist_title, cost_sec in rows:
                hist_lower = (hist_title or "").lower().strip()
                if hist_lower:
                    similarity = difflib.SequenceMatcher(None, title_lower, hist_lower).ratio()
                    if similarity > 0.4: similar_costs.append(max(int(cost_sec or 0), 1))
        if similar_costs and len(similar_costs) >= 2:
            predicted = sum(similar_costs) / len(similar_costs)
            confidence = "high" if len(similar_costs) >= 3 else "medium"
            source = f"基于 {len(similar_costs)} 个相似历史任务"
        elif similar_costs:
            predicted = similar_costs[0]; confidence = "low"; source = "仅 1 个相似历史任务，参考有限"
        else:
            predicted = global_avg; confidence = "low"
            source = f"基于全部 {len(rows)} 个历史任务全局平均"
        return {"predicted_seconds": int(predicted),
                "predicted_text": format_duration(int(predicted)),
                "confidence": confidence, "source": source,
                "total_history": len(rows),
                "global_avg_seconds": int(global_avg),
                "global_avg_text": format_duration(int(global_avg))}

    def _text_similarity(self, a, b):
        """中英文混合文本相似度：SequenceMatcher + 字符集 Jaccard"""
        seq_ratio = difflib.SequenceMatcher(None, a, b).ratio()
        # 字符集 Jaccard（对中文短文本更有效）
        set_a = set(a.replace(' ', '')); set_b = set(b.replace(' ', ''))
        if set_a and set_b:
            char_jaccard = len(set_a & set_b) / len(set_a | set_b)
        else:
            char_jaccard = 0
        # 取两者较高值，字符重叠给 1.2 倍权重
        return max(seq_ratio, char_jaccard * 1.2)

    def predict_pomodoros(self, title=""):
        """基于历史任务标题相似度预测所需番茄数（原生字符串匹配，不依赖第三方NLP库）"""
        # 从 history_task.json 读取历史数据（容错：不存在或损坏返回空列表）
        history = self.load_history_tasks()
        if not history:
            return None
        title_text = title.strip() if title else ""
        if not title_text:
            return None  # 空标题由前端提示
        # 计算每个历史任务标题与输入标题的相似度
        scored = []
        for h in history:
            hist_title = (h.get("title") or "").strip()
            if hist_title:
                sim = self._text_similarity(title_text, hist_title)
                if sim > 0.3:  # 相似度阈值 0.3
                    scored.append((sim, h.get("target_pomodoros", 1)))
        # 按相似度排序，取 Top-5
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:5]
        if len(top) >= 2:
            # 相似度加权平均，向下取整
            total_weight = sum(s for s, _ in top)
            predicted = int(sum(s * v for s, v in top) / total_weight) if total_weight > 0 else 1
            confidence = "high" if len(top) >= 3 else "medium"
            source = f"根据相似历史任务预测，建议番茄数：{predicted}个"
        elif len(top) == 1:
            predicted = top[0][1]; confidence = "low"
            source = f"根据相似历史任务预测，建议番茄数：{predicted}个"
        else:
            return {"predicted": None, "confidence": "none",
                    "source": "暂无相似历史任务，无法预测", "total_history": len(history)}
        return {"predicted": max(1, min(20, predicted)), "confidence": confidence,
                "source": source, "total_history": len(history)}

    # ---- 番茄培养 ----
    PLANT_STAGES = [
        (0, "种子", "🌰"), (50, "发芽", "🌱"), (150, "幼苗", "🌿"),
        (300, "成长", "🪴"), (500, "开花", "🌼"), (750, "结果", "🍅"),
        (1000, "成熟番茄树", "🌳"),
    ]
    WATER_COST = 5
    FERTILIZER_COST = 10
    SUN_COST = 20
    WATER_GROWTH = (10, 15)
    FERTILIZER_GROWTH = (25, 35)
    SUN_GROWTH = (60, 80)

    def get_plant_state(self):
        """获取番茄培养状态"""
        growth = int(self.get_profile_value("plant_growth", 0) or 0)
        water = int(self.get_profile_value("plant_water", 50) or 0)
        fertilizer = int(self.get_profile_value("plant_fertilizer", 50) or 0)
        spent = int(self.get_profile_value("plant_spent", 0) or 0)
        stage_idx = 0; stage_name = self.PLANT_STAGES[0][1]; stage_icon = self.PLANT_STAGES[0][2]
        next_threshold = self.PLANT_STAGES[1][0] if len(self.PLANT_STAGES) > 1 else 99999
        for i, (th, name, icon) in enumerate(self.PLANT_STAGES):
            if growth >= th:
                stage_idx = i; stage_name = name; stage_icon = icon
                next_threshold = self.PLANT_STAGES[i + 1][0] if i + 1 < len(self.PLANT_STAGES) else 99999
        progress = 0
        if stage_idx < len(self.PLANT_STAGES) - 1:
            curr_th = self.PLANT_STAGES[stage_idx][0]
            next_th = self.PLANT_STAGES[stage_idx + 1][0]
            progress = min(100, int((growth - curr_th) / (next_th - curr_th) * 100)) if next_th > curr_th else 100
        else:
            progress = 100
        return {"growth": growth, "water": water, "fertilizer": fertilizer,
                "spent": spent, "stage_idx": stage_idx, "stage_name": stage_name,
                "stage_icon": stage_icon, "progress": progress, "next_threshold": next_threshold,
                "stages": [{"threshold": t, "name": n, "icon": i} for t, n, i in self.PLANT_STAGES]}

    def get_plant_points(self):
        """获取可用于培养的积分（与排名积分独立）"""
        return int(self.get_profile_value("plant_points", 0) or 0)

    def add_plant_points(self, amount):
        """增加培养积分（完成番茄时调用）"""
        cur = self.get_plant_points()
        self.set_profile_value("plant_points", cur + amount)

    def water_plant(self):
        """浇水：消耗培养积分，不影响排名积分和称号"""
        pts = self.get_plant_points()
        if pts < self.WATER_COST: return None, f"培养积分不足，浇水需要 {self.WATER_COST} 积分（当前 {pts}）"
        growth_add = random.randint(*self.WATER_GROWTH)
        self.set_profile_value("plant_growth", int(self.get_profile_value("plant_growth", 0) or 0) + growth_add)
        self.set_profile_value("plant_water", min(100, int(self.get_profile_value("plant_water", 50) or 0) + 15))
        self.set_profile_value("plant_spent", int(self.get_profile_value("plant_spent", 0) or 0) + self.WATER_COST)
        self.set_profile_value("plant_points", pts - self.WATER_COST)
        return self.get_plant_state(), None

    def fertilize_plant(self):
        """施肥：消耗培养积分，不影响排名积分和称号"""
        pts = self.get_plant_points()
        if pts < self.FERTILIZER_COST: return None, f"培养积分不足，施肥需要 {self.FERTILIZER_COST} 积分（当前 {pts}）"
        growth_add = random.randint(*self.FERTILIZER_GROWTH)
        self.set_profile_value("plant_growth", int(self.get_profile_value("plant_growth", 0) or 0) + growth_add)
        self.set_profile_value("plant_fertilizer", min(100, int(self.get_profile_value("plant_fertilizer", 50) or 0) + 12))
        self.set_profile_value("plant_spent", int(self.get_profile_value("plant_spent", 0) or 0) + self.FERTILIZER_COST)
        self.set_profile_value("plant_points", pts - self.FERTILIZER_COST)
        return self.get_plant_state(), None

    def sun_plant(self):
        """晒太阳：消耗培养积分，大幅增加生长值"""
        pts = self.get_plant_points()
        if pts < self.SUN_COST: return None, f"培养积分不足，晒太阳需要 {self.SUN_COST} 积分（当前 {pts}）"
        growth_add = random.randint(*self.SUN_GROWTH)
        self.set_profile_value("plant_growth", int(self.get_profile_value("plant_growth", 0) or 0) + growth_add)
        self.set_profile_value("plant_spent", int(self.get_profile_value("plant_spent", 0) or 0) + self.SUN_COST)
        self.set_profile_value("plant_points", pts - self.SUN_COST)
        return self.get_plant_state(), None

    def release_due_pending_tasks(self):
        """将到期的待办任务的 scheduled_date 清空（与 main.py 一致）"""
        if not self.json_path: return
        tasks = self.load_tasks(); today = datetime.now().date(); changed = False
        for td in tasks:
            if not td.get('completed') and td.get('scheduled_date'):
                try:
                    sd = datetime.strptime(td['scheduled_date'], "%Y-%m-%d").date()
                    if sd <= today: td['scheduled_date'] = None; changed = True
                except: pass
        if changed: self.save_tasks(tasks)

# ====================== SessionManager ======================
class SessionManager:
    def __init__(self): self.db_path = GLOBAL_DB_PATH; self.init_db()
    def init_db(self):
        conn = sqlite3.connect(self.db_path); cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS app_profile (key TEXT PRIMARY KEY, value TEXT NOT NULL)''')
        conn.commit(); conn.close()
    def set_value(self, key, value):
        conn = sqlite3.connect(self.db_path); cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO app_profile (key,value) VALUES (?,?)", (key, str(value)))
        conn.commit(); conn.close()
    def get_value(self, key, default=None):
        conn = sqlite3.connect(self.db_path); cur = conn.cursor()
        cur.execute("SELECT value FROM app_profile WHERE key=?", (key,))
        row = cur.fetchone(); conn.close(); return row[0] if row else default
    def clear_value(self, key):
        conn = sqlite3.connect(self.db_path); cur = conn.cursor()
        cur.execute("DELETE FROM app_profile WHERE key=?", (key,)); conn.commit(); conn.close()

# ====================== HTTP 请求工具（完整错误处理）======================
import urllib.request, urllib.error

def score_server_request(method, path, payload=None, token=None, timeout=5):
    url = f"{SCORE_SERVER_URL}{path}"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token: headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try: return json.loads(exc.read().decode("utf-8"))
        except: raise RuntimeError(f"服务器返回错误: {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("无法连接积分排行服务器，请先运行 score_server.py") from exc
    except TimeoutError as exc:
        raise RuntimeError("连接积分排行服务器超时") from exc

# ====================== Flask 鉴权 ======================
from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session: return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

def get_dm():
    return DataManager(session.get('username'))

def get_schedule():
    dm = get_dm(); schedule = Schedule()
    for td in dm.load_tasks(): schedule.add_task(Task.from_dict(td))
    return schedule, dm

# ====================== 路由 ======================

@web_app.context_processor
def inject_globals():
    if 'username' in session:
        dm = get_dm(); pp = dm.get_points_profile()
        return {"honor_title": pp['title'], "points": pp['points'],
                "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    return {"honor_title": "未登录", "points": 0,
            "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

# ---- 页面路由 ----
@web_app.route("/")
@login_required
def index():
    schedule, dm = get_schedule(); dm.release_due_pending_tasks(); schedule, dm = get_schedule()
    stats = schedule.get_statistics()
    return render_template('index.html', stats=stats,
                           today_str=datetime.now().strftime("%Y年%m月%d日"), active_page='tasks')

@web_app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        action = request.form.get("action", "login")
        if not username or not password:
            return render_template('login.html', error="请输入用户名和密码", active_page='login', server_url=SCORE_SERVER_URL)
        if not (2 <= len(username) <= 20):
            return render_template('login.html', error="用户名长度应为 2-20 个字符", active_page='login', server_url=SCORE_SERVER_URL)
        if not re.match(r'^[\w一-鿿]+$', username):
            return render_template('login.html', error="用户名只能包含中文、字母、数字和下划线", active_page='login', server_url=SCORE_SERVER_URL)
        if len(password) < 6:
            return render_template('login.html', error="密码至少 6 位", active_page='login', server_url=SCORE_SERVER_URL)
        path = "/register" if action == "register" else "/login"
        payload = {"username": username, "password": password}
        if action == "register": payload["points"] = 0
        try: result = score_server_request("POST", path, payload)
        except RuntimeError as exc:
            return render_template('login.html', error=str(exc), active_page='login', server_url=SCORE_SERVER_URL)
        if not result.get("ok"):
            return render_template('login.html', error=result.get("error", "请求失败"), active_page='login', server_url=SCORE_SERVER_URL)
        session['username'] = result['username']; session['token'] = result['token']
        dm = get_dm()
        try: score_server_request("POST", "/sync_points", {"points": dm.get_total_points()}, token=result['token'])
        except: pass
        return redirect(url_for('index'))
    return render_template('login.html', error=None, active_page='login', server_url=SCORE_SERVER_URL)

@web_app.route("/logout")
def logout():
    if 'token' in session and 'username' in session:
        dm = get_dm()
        try: score_server_request("POST", "/sync_points", {"points": dm.get_total_points()}, token=session['token'])
        except: pass
    session.clear(); return redirect(url_for('login_page'))

@web_app.route("/leaderboard")
def leaderboard_page():
    # 自动同步积分
    if 'token' in session and 'username' in session:
        dm = get_dm()
        try: score_server_request("POST", "/sync_points", {"points": dm.get_total_points()}, token=session['token'])
        except: pass
    try:
        result = score_server_request("GET", "/leaderboard")
        lb = result.get("leaderboard", [])
        total_users = len(lb)
        avg_points = round(sum(u['points'] for u in lb) / total_users, 1) if total_users else 0
        max_points = lb[0]['points'] if lb else 0; max_honor = lb[0]['honor_title'] if lb else "无"
        stats = {"total_users": total_users, "avg_points": avg_points, "max_points": max_points, "max_honor": max_honor}
        # 预生成场景数据
        scene_data = {
            "stars": [{"top": random.randint(1,50), "left": random.randint(1,98),
                       "w": random.choice([1,1.5,2]), "h": random.choice([1,1.5,2]),
                       "op": random.choice([0.4,0.5,0.6,0.7,0.8,0.9,1.0]),
                       "dur": random.choice([2,2.5,3,3.5,4]), "delay": -random.randint(0,4)} for _ in range(80)],
            "particles": [{"left": random.randint(5,90), "bottom": random.randint(0,50),
                          "size": random.choice([2,3,4]),
                          "dur": random.choice([8,10,12,14,16]), "delay": -random.randint(0,10)} for _ in range(15)]
        }
        return render_template('leaderboard.html', leaderboard=lb, stats=stats, error=None,
                               active_page='leaderboard', scene=scene_data)
    except RuntimeError as exc:
        return render_template('leaderboard.html', leaderboard=[], stats={"total_users":0,"avg_points":0,"max_points":0,"max_honor":"无"}, error=str(exc), active_page='leaderboard', scene={"stars":[],"particles":[]})

@web_app.route("/stats")
@login_required
def stats_page():
    schedule, dm = get_schedule(); stats = schedule.get_statistics()
    ps = dm.get_pomodoro_stats()
    pomo = {"today_focus_display": format_duration(ps['today_focus']),
            "weekly_count": ps['weekly_count'], "total_focus_display": format_duration(ps['total_focus'])}
    return render_template('stats.html', stats=stats, pomo=pomo, points_info=dm.get_points_profile(),
                           t=int(time.time() * 1000), active_page='stats')

@web_app.route("/honor")
@login_required
def honor_page():
    dm = get_dm(); pp = dm.get_points_profile()
    return render_template('honor.html', points=pp['points'], title=pp['title'],
                           honor_levels=HONOR_LEVELS, active_page='honor')

@web_app.route("/plant")
@login_required
def plant_page():
    dm = get_dm(); ps = dm.get_plant_state()
    return render_template('plant.html', plant=ps, plant_points=dm.get_plant_points(),
                           honor_points=dm.get_total_points(), active_page='plant')

@web_app.route("/plant-guide")
def plant_guide():
    """番茄培养阶段图示"""
    stages = [
        {"name": "种子", "icon": "🌰", "threshold": 0, "desc": "一颗小小的番茄种子，埋入肥沃的土壤中。需要水分和养分才能破土而出。", "height": 10, "leaves": 0, "flowers": 0, "fruits": 0, "color": "#8b7355"},
        {"name": "发芽", "icon": "🌱", "threshold": 50, "desc": "嫩绿的芽尖冲破土壤，向阳光伸展。这是生命的第一次呼吸，脆弱但充满希望。", "height": 40, "leaves": 2, "flowers": 0, "fruits": 0, "color": "#7cb342"},
        {"name": "幼苗", "icon": "🌿", "threshold": 150, "desc": "茎秆逐渐粗壮，叶片展开如手掌。根系深入土壤，开始独立吸收养分。", "height": 90, "leaves": 4, "flowers": 0, "fruits": 0, "color": "#689f38"},
        {"name": "成长", "icon": "🪴", "threshold": 300, "desc": "植株茁壮成长，枝繁叶茂。主干挺拔，侧枝伸展，为开花结果积蓄力量。", "height": 150, "leaves": 6, "flowers": 0, "fruits": 0, "color": "#4caf50"},
        {"name": "开花", "icon": "🌼", "threshold": 500, "desc": "金黄色的番茄花在枝头绽放，引来蜜蜂授粉。每一朵花都是未来的果实。", "height": 200, "leaves": 8, "flowers": 3, "fruits": 0, "color": "#fdd835"},
        {"name": "结果", "icon": "🍅", "threshold": 750, "desc": "青涩的小番茄挂满枝头，逐渐由绿转红。阳光赋予它们甜蜜的滋味。", "height": 250, "leaves": 10, "flowers": 3, "fruits": 4, "color": "#f4511e"},
        {"name": "成熟番茄树", "icon": "🌳", "threshold": 1000, "desc": "一棵硕果累累的番茄树！红透的番茄散发着诱人的光泽。这是坚持与付出的最美回报。", "height": 300, "leaves": 12, "flowers": 5, "fruits": 8, "color": "#d32f2f"},
    ]
    return render_template('plant_guide.html', stages=stages, active_page='plant')

@web_app.route("/plant/<username>")
def view_plant(username):
    """查看其他用户的番茄培养情况"""
    if not username: return redirect(url_for('plant_page'))
    dm = DataManager(username); ps = dm.get_plant_state()
    return render_template('plant.html', plant=ps, plant_points=dm.get_plant_points(),
                           honor_points=dm.get_total_points(), viewing=username, active_page='plant')

@web_app.route("/calendar")
@login_required
def calendar_page():
    schedule, dm = get_schedule(); dm.release_due_pending_tasks(); schedule, dm = get_schedule()
    year = int(request.args.get("year", datetime.now().year))
    month = int(request.args.get("month", datetime.now().month))
    # 构建日历数据
    import calendar as cal_mod
    month_days = cal_mod.monthcalendar(year, month)
    today = datetime.now().date()
    task_counts = {}
    for t in schedule.tasks:
        if not t.completed and t.scheduled_date:
            d = t.scheduled_date.date(); task_counts[d] = task_counts.get(d, 0) + 1
    weeks = []
    for week in month_days:
        week_data = []
        for day in week:
            if day == 0: week_data.append({"day": 0, "date": None})
            else:
                cd = datetime(year, month, day); cday = cd.date()
                week_data.append({
                    "day": day, "date": cd.strftime("%Y-%m-%d"),
                    "is_today": cday == today,
                    "is_past": cday < today,
                    "pending_count": task_counts.get(cday, 0)
                })
        weeks.append(week_data)
    prev_month = month - 1 if month > 1 else 12; prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1; next_year = year if month < 12 else year + 1
    return render_template('calendar.html', year=year, month=month, weeks=weeks,
                           prev_year=prev_year, prev_month=prev_month,
                           next_year=next_year, next_month=next_month, active_page='calendar')

@web_app.route("/about")
def about_page():
    return render_template('about.html', active_page='')

@web_app.route("/delete_account", methods=["GET", "POST"])
@login_required
def delete_account_page():
    if request.method == "POST":
        password = request.form.get("password", "")
        if not password:
            return render_template('delete_account.html', error="请输入密码", active_page='')
        try:
            result = score_server_request("POST", "/delete_account", {"password": password}, token=session.get('token'))
        except RuntimeError as exc:
            return render_template('delete_account.html', error=str(exc), active_page='')
        if not result.get("ok"):
            return render_template('delete_account.html', error=result.get("error", "注销失败"), active_page='')
        # 清理本地数据
        username = session.get('username', '')
        ad = os.path.abspath(get_account_dir(username))
        ar = os.path.abspath(ACCOUNTS_DIR)
        if os.path.commonpath([ar, ad]) == ar and os.path.isdir(ad): shutil.rmtree(ad)
        session.clear(); return redirect(url_for('login_page'))
    return render_template('delete_account.html', error=None, active_page='')

# ---- API 路由 ----
@web_app.route("/api/tasks")
@login_required
def api_tasks():
    dm = get_dm(); dm.release_due_pending_tasks(); schedule, dm = get_schedule()
    view = request.args.get("view", "uncompleted")
    search = request.args.get("search", "").strip().lower()
    if view == "all": tasks = schedule.tasks
    elif view == "pending": tasks = schedule.get_pending_tasks()
    elif view == "completed": tasks = schedule.get_completed_tasks()
    else: tasks = schedule.get_uncompleted_tasks()
    result = []
    for t in tasks:
        if search and search not in t.title.lower() and search not in t.description.lower(): continue
        due_raw = t.due_date.strftime("%Y-%m-%d %H:%M") if t.due_date else None
        sched_raw = t.scheduled_date.strftime("%Y-%m-%d") if t.scheduled_date else None
        # 生成预测
        pred = dm.predict_task_duration(t.title) if view == "uncompleted" else None
        result.append({
            "id": t.id, "title": t.title, "description": t.description,
            "priority": t.priority, "priority_label": {1:"高",2:"中",3:"低"}.get(t.priority,"低"),
            "completed": t.completed, "due_date": due_raw or "无", "due_date_raw": due_raw,
            "scheduled_date": sched_raw or "无", "scheduled_date_raw": sched_raw,
            "target_pomodoros": t.target_pomodoros, "completed_pomodoros": t.completed_pomodoros,
            "finished_at": t.finished_at.strftime("%Y-%m-%d %H:%M:%S") if t.finished_at else None,
            "cost_sec": t.cost_sec, "cost_display": format_duration(t.cost_sec),
            "prediction": pred,
        })
    return jsonify({"ok": True, "view": view, "tasks": result})

@web_app.route("/api/stats")
@login_required
def api_stats():
    schedule, dm = get_schedule(); return jsonify(schedule.get_statistics())

@web_app.route("/api/predict/<title>")
@login_required
def api_predict(title):
    dm = get_dm(); pred = dm.predict_task_duration(title)
    return jsonify({"ok": True, "prediction": pred})

@web_app.route("/api/predict_pomodoros", methods=["POST"])
@login_required
def api_predict_pomodoros():
    """预测番茄数：接收任务标题文本，匹配历史任务标题相似度"""
    data = request.get_json() or {}
    title = data.get("title", "")
    dm = get_dm(); pred = dm.predict_pomodoros(title)
    return jsonify({"ok": True, "prediction": pred})

@web_app.route("/api/tasks/new", methods=["POST"])
@login_required
def api_task_new():
    schedule, dm = get_schedule(); data = request.get_json() or {}
    title = data.get("title", "").strip()
    if not title: return jsonify({"ok": False, "error": "请输入任务标题"}), 400
    task = Task(title, data.get("description", ""),
                parse_natural_datetime(data.get("due_date", "")),
                data.get("priority", 2),
                parse_natural_datetime(data.get("scheduled_date", "")))
    task.target_pomodoros = max(1, min(20, int(data.get("target_pomodoros", 1) or 1)))
    schedule.add_task(task); dm.save_tasks([t.to_dict() for t in schedule.tasks])
    # 创建任务时自动将任务标题+目标番茄数写入 history_task.json（用于后续预测）
    dm.append_history_task(task.title, task.target_pomodoros)
    return jsonify({"ok": True, "task_id": task.id})

@web_app.route("/api/tasks/<tid>/edit", methods=["POST"])
@login_required
def api_task_edit(tid):
    schedule, dm = get_schedule(); task = schedule.get_task_by_id(tid)
    if not task: return jsonify({"ok": False, "error": "任务不存在"}), 404
    data = request.get_json() or {}
    task.title = data.get("title", task.title)
    task.description = data.get("description", task.description)
    task.due_date = parse_natural_datetime(data.get("due_date", "")) or task.due_date
    if "scheduled_date" in data:
        task.scheduled_date = parse_natural_datetime(data.get("scheduled_date", "")) if data.get("scheduled_date", "").strip() else None
    task.priority = data.get("priority", task.priority)
    task.target_pomodoros = max(1, min(20, int(data.get("target_pomodoros", task.target_pomodoros) or task.target_pomodoros)))
    dm.save_tasks([t.to_dict() for t in schedule.tasks])
    return jsonify({"ok": True})

@web_app.route("/api/tasks/<tid>/delete", methods=["POST"])
@login_required
def api_task_delete(tid):
    schedule, dm = get_schedule(); task = schedule.get_task_by_id(tid)
    if not task: return jsonify({"ok": False, "error": "任务不存在"}), 404
    schedule.remove_task(tid); dm.delete_task_history(tid)
    dm.save_tasks([t.to_dict() for t in schedule.tasks])
    return jsonify({"ok": True})

@web_app.route("/api/tasks/<tid>/complete", methods=["POST"])
@login_required
def api_task_complete(tid):
    schedule, dm = get_schedule(); task = schedule.get_task_by_id(tid)
    if not task: return jsonify({"ok": False, "error": "任务不存在"}), 404
    if task.completed: return jsonify({"ok": False, "error": "任务已完成"}), 400
    data = request.get_json() or {}
    try: cost = int(data.get("cost_sec", 1500) or 1500)
    except: return jsonify({"ok": False, "error": "请输入有效的秒数（1-28800）"}), 400
    if cost <= 0: return jsonify({"ok": False, "error": "请输入有效的秒数（1-28800）"}), 400
    if cost > 28800: return jsonify({"ok": False, "confirm": f"耗时{cost}秒超过8小时，确认吗？", "cost": cost}), 400
    task.completed = True; task.finished_at = datetime.now(); task.cost_sec = cost
    dm.save_task_history(task); dm.save_tasks([t.to_dict() for t in schedule.tasks])
    quote, source = random.choice(PERSEVERANCE_QUOTES)
    pp = dm.get_points_profile()
    return jsonify({"ok": True, "quote": f"{quote} ——{source}",
                    "points": pp['points'], "honor_title": pp['title']})

@web_app.route("/api/tasks/<tid>/restore", methods=["POST"])
@login_required
def api_task_restore(tid):
    schedule, dm = get_schedule(); task = schedule.get_task_by_id(tid)
    if not task: return jsonify({"ok": False, "error": "任务不存在"}), 404
    task.completed = False; task.finished_at = None; task.cost_sec = 0
    task.completed_pomodoros = 0; task.scheduled_date = None
    dm.delete_task_history(tid); dm.save_tasks([t.to_dict() for t in schedule.tasks])
    return jsonify({"ok": True})

@web_app.route("/api/pomodoro/complete", methods=["POST"])
@login_required
def api_pomodoro_complete():
    try:
        dm = get_dm(); data = request.get_json() or {}
        task_id = data.get("task_id")
        start_time_str = data.get("start_time")
        try:
            start_time = datetime.fromisoformat(start_time_str) if start_time_str else datetime.now()
            if start_time.tzinfo is not None: start_time = start_time.replace(tzinfo=None)
        except: start_time = datetime.now()
        end_time = datetime.now()
        updated, task_data, points_update = dm.save_pomodoro(start_time, end_time, "work", task_id)
        task_completed = False
        if updated and task_data and task_id:
            schedule, _ = get_schedule()
            task = schedule.get_task_by_id(task_id)
            if task:
                task.completed_pomodoros = task_data['completed_pomodoros']
                if task_data.get('completed'):
                    task.completed = True; task.finished_at = datetime.strptime(task_data['finished_at'], "%Y-%m-%d %H:%M:%S")
                    task.cost_sec = task_data['cost_sec']; dm.save_task_history(task)
                    task_completed = True
        if points_update:
            token = session.get('token')
            if token:
                try: score_server_request("POST", "/sync_points", {"points": dm.get_total_points()}, token=token)
                except: pass
        q = random.choice(PERSEVERANCE_QUOTES)
        return jsonify({
            "ok": True, "task_completed": task_completed,
            "points_earned": points_update.get("earned", 0) if points_update else 0,
            "honor_title": points_update.get("title", "") if points_update else "",
            "leveled_up": points_update.get("leveled_up", False) if points_update else False,
            "total_points": dm.get_total_points(),
            "quote": f"{q[0]} --{q[1]}",
        })
    except Exception as e:
        import traceback
        print(f"[ERROR] Pomodoro: {traceback.format_exc()}")
        return jsonify({"ok": False, "error": str(e)}), 500

@web_app.route("/api/plant")
@login_required
def api_plant():
    dm = get_dm(); return jsonify({"ok": True, "plant": dm.get_plant_state(),
                "plant_points": dm.get_plant_points(), "honor_points": dm.get_total_points()})

@web_app.route("/api/plant/<username>")
def api_plant_user(username):
    dm = DataManager(username)
    return jsonify({"ok": True, "plant": dm.get_plant_state(),
                "plant_points": dm.get_plant_points(), "honor_points": dm.get_total_points(), "username": username})

@web_app.route("/api/plant/water", methods=["POST"])
@login_required
def api_plant_water():
    dm = get_dm(); result, error = dm.water_plant()
    if error: return jsonify({"ok": False, "error": error}), 400
    return jsonify({"ok": True, "plant": result, "plant_points": dm.get_plant_points(),
                    "honor_points": dm.get_total_points(),
                    "msg": f"浇水成功！生长值 +{random.randint(*DataManager.WATER_GROWTH)}"})

@web_app.route("/api/plant/fertilize", methods=["POST"])
@login_required
def api_plant_fertilize():
    dm = get_dm(); result, error = dm.fertilize_plant()
    if error: return jsonify({"ok": False, "error": error}), 400
    return jsonify({"ok": True, "plant": result, "plant_points": dm.get_plant_points(),
                    "honor_points": dm.get_total_points(),
                    "msg": f"施肥成功！生长值 +{random.randint(*DataManager.FERTILIZER_GROWTH)}"})

@web_app.route("/api/plant/sun", methods=["POST"])
@login_required
def api_plant_sun():
    dm = get_dm(); result, error = dm.sun_plant()
    if error: return jsonify({"ok": False, "error": error}), 400
    return jsonify({"ok": True, "plant": result, "plant_points": dm.get_plant_points(),
                    "honor_points": dm.get_total_points(),
                    "msg": f"晒太阳成功！生长值 +{random.randint(*DataManager.SUN_GROWTH)}"})

@web_app.route("/api/points/sync", methods=["POST"])
@login_required
def api_points_sync():
    dm = get_dm(); token = session.get('token')
    if not token: return jsonify({"ok": False, "error": "未登录"}), 401
    try: return jsonify(score_server_request("POST", "/sync_points", {"points": dm.get_total_points()}, token=token))
    except RuntimeError as exc: return jsonify({"ok": False, "error": str(exc)}), 500

@web_app.route("/api/chart/dashboard.png")
@login_required
def api_chart_dashboard():
    dm = get_dm()
    if not dm.db_path: return jsonify({"error": "请先登录"}), 400
    tasks_data = dm.load_tasks()
    completed = sum(1 for t in tasks_data if t["completed"]); total = len(tasks_data)
    conn = sqlite3.connect(dm.db_path); cursor = conn.cursor()
    records = cursor.execute("SELECT start_time, duration FROM pomodoro_records WHERE phase='work'").fetchall()
    history_records = cursor.execute("SELECT completed_at, cost_sec FROM task_history").fetchall()
    conn.close()
    work_time = {}
    for rec in records: date = rec[0].split(" ")[0]; work_time[date] = work_time.get(date, 0) + rec[1]
    today = datetime.now().date()
    last_7_days = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    last_7_minutes = [round(work_time.get(d, 0) / 60, 1) for d in last_7_days]
    history_by_day = {}
    for ca, _ in history_records: d = ca.split(" ")[0]; history_by_day[d] = history_by_day.get(d, 0) + 1
    completed_7d = [history_by_day.get(d, 0) for d in last_7_days]
    bucket_labels = ["0-30秒","30秒-1分钟","1分钟-1分30秒","1分30秒-2分钟","2分钟-2分30秒","2分30秒-3分钟","3分钟及以上"]
    tbc = {i: 0 for i in range(len(bucket_labels))}
    for _, cs in history_records: idx = min(max(int(cs or 0), 0) // 30, len(bucket_labels) - 1); tbc[idx] = tbc.get(idx, 0) + 1
    bucket_counts = [tbc[i] for i in range(len(bucket_labels))]
    fig, axes = plt.subplots(2, 3, figsize=(16, 10))
    fig.suptitle("效率统计仪表盘", fontsize=16, fontweight="bold")
    ax = axes[0,0]
    if total > 0:
        sizes = [completed, total - completed]
        _, _, at = ax.pie(sizes, labels=[f"已完成 {completed}", f"未完成 {total - completed}"],
                          autopct="%1.1f%%", colors=["#2ecc71","#e74c3c"], startangle=90,
                          wedgeprops={"edgecolor":"white","linewidth":2,"width":0.4}, textprops={"fontsize":10})
        for t in at: t.set_color("white"); t.set_fontweight("bold")
        ax.text(0,0,f"{completed/total*100:.0f}%",ha="center",va="center",fontsize=22,fontweight="bold",color="#2c3e50")
    else: ax.text(0.5,0.5,"暂无任务",ha="center",va="center",fontsize=12)
    ax.set_title(f"任务完成率（共 {total} 个）", fontsize=12, fontweight="bold")
    ax = axes[0,1]; xs1 = list(range(7))
    ax.fill_between(xs1, last_7_minutes, color="#3498db", alpha=0.2)
    ax.plot(xs1, last_7_minutes, marker="o", color="#3498db", linewidth=2, markersize=7)
    avg = sum(last_7_minutes)/7
    if avg > 0: ax.axhline(y=avg, color="#e74c3c", linestyle="--", linewidth=1.2, label=f"日均 {avg:.1f} 分"); ax.legend(loc="upper right", fontsize=9)
    for i,v in enumerate(last_7_minutes):
        if v > 0: ax.text(i, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8)
    ax.set_title("最近 7 天每日专注（分钟）", fontsize=12, fontweight="bold")
    ax.set_xticks(xs1); ax.set_xticklabels([d[5:] for d in last_7_days], rotation=30, fontsize=8); ax.set_ylabel("分钟"); ax.set_ylim(bottom=0); ax.grid(alpha=0.3)
    ax = axes[0,2]; b2 = ax.bar(range(7), completed_7d, color="#16a085", alpha=0.85, edgecolor="white")
    for b,v in zip(b2,completed_7d):
        if v>0: ax.text(b.get_x()+b.get_width()/2,b.get_height(),f"{v}",ha="center",va="bottom",fontsize=9,fontweight="bold")
    ax.set_title(f"最近 7 天完成任务数（共 {sum(completed_7d)} 个）", fontsize=12, fontweight="bold")
    ax.set_xticks(range(7)); ax.set_xticklabels([d[5:] for d in last_7_days], rotation=30, fontsize=8); ax.set_ylabel("任务数"); ax.set_ylim(bottom=0); ax.yaxis.set_major_locator(MaxNLocator(integer=True)); ax.grid(axis="y", alpha=0.3)
    # ---- 各优先级完成情况（修复百分比计算与标注位置）----
    ax = axes[1,0]; pc={1:0,2:0,3:0}; pt={1:0,2:0,3:0}
    for t in tasks_data:
        p = t["priority"]
        pt[p] = pt.get(p, 0) + 1          # 该优先级总任务数
        if t["completed"]:
            pc[p] = pc.get(p, 0) + 1      # 该优先级已完成任务数
    cc = [pc[i] for i in [1,2,3]]         # 已完成数列表（绿色柱）
    uc = [pt[i] - pc[i] for i in [1,2,3]] # 未完成数列表（红色柱）
    # 画堆叠柱状图
    ax.bar(range(3), cc, color="#2ecc71", label="已完成", edgecolor="white")
    ax.bar(range(3), uc, bottom=cc, color="#e74c3c", alpha=0.7, label="未完成", edgecolor="white")
    # 修复：逐个优先级独立计算完成率，标注在绿色已完成柱顶部
    for i in range(3):
        total = pt[i + 1]                    # 该优先级总任务数
        completed = pc[i + 1]                # 该优先级已完成数
        if total > 0:
            rate = completed / total * 100   # 完成率百分比
            # 标注位置：y = 绿色柱高度（completed），va="bottom" 让文字底部贴着柱顶
            ax.text(i, completed,
                    f"{rate:.1f}%",
                    ha="center", va="bottom",
                    fontsize=9, fontweight="bold", color="#2c3e50")
        else:
            # 该优先级无任务时显示「无任务」
            ax.text(i, 0, "无任务",
                    ha="center", va="bottom",
                    fontsize=9, color="#94a3b8")
    ax.set_title("各优先级完成情况", fontsize=12, fontweight="bold")
    ax.set_xticks(range(3)); ax.set_xticklabels(["高","中","低"]); ax.set_xlabel("优先级"); ax.set_ylabel("任务数量"); ax.legend(loc="upper right", fontsize=9); ax.yaxis.set_major_locator(MaxNLocator(integer=True)); ax.grid(axis="y", alpha=0.3)
    ax = axes[1,1]
    if sum(bucket_counts)>0:
        b3=ax.bar(bucket_labels,bucket_counts,color="#f39c12",alpha=0.85,edgecolor="white")
        for b,v in zip(b3,bucket_counts):
            if v>0: ax.text(b.get_x()+b.get_width()/2,b.get_height(),f"{v}",ha="center",va="bottom",fontsize=9,fontweight="bold")
        ac=sum(c or 0 for _,c in history_records)/len(history_records)
        ax.set_title(f"任务耗时分布（共 {sum(bucket_counts)} 个，均 {format_duration(ac)}）", fontsize=12, fontweight="bold"); ax.tick_params(axis="x",labelrotation=45,labelsize=8)
    else: ax.text(0.5,0.5,"暂无已完成任务",ha="center",va="center",fontsize=12); ax.set_title("任务耗时分布", fontsize=12, fontweight="bold")
    ax.set_xlabel("耗时区间"); ax.set_ylabel("任务数"); ax.yaxis.set_major_locator(MaxNLocator(integer=True)); ax.grid(axis="y", alpha=0.3)
    ax = axes[1,2]
    if work_time:
        ds=sorted(work_time.keys()); cum=[]; r=0.0
        for d in ds: r+=work_time[d]/60; cum.append(r)
        xs2=list(range(len(ds)))
        ax.plot(xs2,cum,marker="o",color="#9b59b6",linewidth=2,markersize=6)
        ax.fill_between(xs2,cum,color="#9b59b6",alpha=0.2)
        ax.set_xticks(xs2); ax.set_xticklabels([d[5:] for d in ds], rotation=45, fontsize=8)
        ax.set_title(f"累积专注趋势（总 {cum[-1]:.1f} 分钟）", fontsize=12, fontweight="bold")
        ax.set_xlabel("日期"); ax.set_ylabel("累积分钟"); ax.grid(axis="y", alpha=0.3)
    else: ax.text(0.5,0.5,"暂无数据",ha="center",va="center",fontsize=12); ax.set_title("累积专注趋势", fontsize=12, fontweight="bold")
    plt.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    buf.seek(0); plt.close(fig)
    # 禁用浏览器缓存，确保每次请求都是最新图表数据
    from flask import make_response
    resp = make_response(send_file(buf, mimetype='image/png'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

# ====================== 自动启动排行榜服务 ======================
def ensure_score_server_running():
    try: score_server_request("GET", "/health", timeout=1); return True
    except RuntimeError: pass
    try:
        import score_server
        t = threading.Thread(target=score_server.run_server, daemon=True); t.start()
        time.sleep(0.5); score_server_request("GET", "/health", timeout=2); return True
    except Exception: return False

def run_web():
    score_ok = ensure_score_server_running()
    print("=" * 50)
    print("[Smart Schedule] Flask Web 界面")
    print(f"    Web 界面: http://127.0.0.1:5000/")
    if score_ok: print(f"    排行榜服务: http://127.0.0.1:8000/ [OK]")
    else: print(f"    [WARN] 排行榜服务未启动")
    print("    按 Ctrl+C 停止服务器")
    print("=" * 50)
    web_app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)

if __name__ == "__main__":
    run_web()
