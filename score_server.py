# -*- coding: utf-8 -*-
"""积分排行服务器 —— Flask Web 界面 + REST API（加分项）"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

HOST = "127.0.0.1"
PORT = 8000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "score_server.db")
TOKEN_TTL_DAYS = 30
MAX_BODY_SIZE = 8192
POINTS_PER_POMODORO = 25
MAX_POINTS_PER_SYNC = 500

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


class RateLimiter:
    """简单的 IP 速率限制器，防止暴力破解"""

    def __init__(self, max_requests=10, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self.attempts = defaultdict(list)

    def is_allowed(self, ip):
        now = time.time()
        self.attempts[ip] = [t for t in self.attempts[ip] if now - t < self.window]
        if len(self.attempts[ip]) >= self.max_requests:
            return False
        self.attempts[ip].append(now)
        return True


_login_limiter = RateLimiter(max_requests=10, window_seconds=60)
_register_limiter = RateLimiter(max_requests=5, window_seconds=60)


def get_honor_title(points):
    title = HONOR_LEVELS[0][1]
    for threshold, level_title in HONOR_LEVELS:
        if points >= threshold:
            title = level_title
        else:
            break
    return title


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_time(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def hash_password(password, salt=None):
    salt = salt or os.urandom(16)
    password_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000)
    return (
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(password_hash).decode("ascii"),
    )


def verify_password(password, salt_text, hash_text):
    salt = base64.b64decode(salt_text.encode("ascii"))
    _, candidate_hash = hash_password(password, salt)
    return hmac.compare_digest(candidate_hash, hash_text)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS users
        (id INTEGER PRIMARY KEY AUTOINCREMENT,
         username TEXT UNIQUE NOT NULL,
         password_salt TEXT NOT NULL,
         password_hash TEXT NOT NULL,
         points INTEGER NOT NULL DEFAULT 0,
         created_at TEXT NOT NULL,
         updated_at TEXT NOT NULL)"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS sessions
        (token TEXT PRIMARY KEY,
         user_id INTEGER NOT NULL,
         expires_at TEXT NOT NULL,
         created_at TEXT NOT NULL,
         FOREIGN KEY(user_id) REFERENCES users(id))"""
    )
    conn.commit()
    conn.close()


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    created_at = now_text()
    expires_at = (datetime.now() + timedelta(days=TOKEN_TTL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (token, user_id, expires_at, created_at),
    )
    conn.commit()
    conn.close()
    return token


def get_current_user():
    auth = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not auth.startswith(prefix):
        return None
    token = auth[len(prefix):].strip()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """SELECT users.id, users.username, users.points, sessions.expires_at
           FROM sessions
           JOIN users ON users.id = sessions.user_id
           WHERE sessions.token=?""",
        (token,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    user_id, username, points, expires_at = row
    if parse_time(expires_at) < datetime.now():
        return None
    return {"id": user_id, "username": username, "points": points}


def get_leaderboard_data(limit=50):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """SELECT username, points, updated_at
           FROM users
           ORDER BY points DESC, updated_at ASC
           LIMIT ?""",
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    leaderboard = []
    for index, (username, points, updated_at) in enumerate(rows, start=1):
        leaderboard.append({
            "rank": index,
            "username": username,
            "points": points,
            "honor_title": get_honor_title(points),
            "updated_at": updated_at,
        })
    return leaderboard


def get_server_stats():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0] or 0
    cur.execute("SELECT AVG(points) FROM users")
    avg_points = cur.fetchone()[0] or 0
    cur.execute("SELECT MAX(points) FROM users")
    max_points = cur.fetchone()[0] or 0
    conn.close()
    return {
        "total_users": total_users,
        "avg_points": round(avg_points, 1),
        "max_points": max_points,
        "max_honor": get_honor_title(max_points),
    }


# ==================== Web 仪表盘页面 ====================

DASHBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>智能日程管理器 - 积分排行榜</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Microsoft YaHei', '微软雅黑', 'PingFang SC', sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            min-height: 100vh;
            color: #e2e8f0;
        }
        .header {
            background: rgba(30, 41, 59, 0.9);
            backdrop-filter: blur(10px);
            border-bottom: 1px solid #334155;
            padding: 20px 40px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 { font-size: 24px; font-weight: 700; color: #f1f5f9; }
        .header h1 span { color: #3b82f6; }
        .header .subtitle { font-size: 13px; color: #64748b; margin-top: 4px; }
        .container { max-width: 1100px; margin: 0 auto; padding: 30px 20px; }
        .stats-row {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            margin-bottom: 30px;
        }
        @media (max-width: 768px) { .stats-row { grid-template-columns: repeat(2, 1fr); } }
        .stat-card {
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 20px;
            text-align: center;
        }
        .stat-card .value {
            font-size: 32px;
            font-weight: 700;
            color: #3b82f6;
            margin-bottom: 4px;
        }
        .stat-card .label { font-size: 13px; color: #64748b; }
        .honor-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 24px;
            justify-content: center;
        }
        .honor-badge {
            background: rgba(30, 41, 59, 0.6);
            border: 1px solid #334155;
            border-radius: 20px;
            padding: 6px 16px;
            font-size: 12px;
            color: #94a3b8;
        }
        .honor-badge.earned { border-color: #22c55e; color: #22c55e; }
        .leaderboard-section {
            background: rgba(30, 41, 59, 0.6);
            border: 1px solid #334155;
            border-radius: 16px;
            overflow: hidden;
        }
        .leaderboard-header {
            background: rgba(15, 23, 42, 0.6);
            padding: 16px 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .leaderboard-header h2 { font-size: 18px; color: #f1f5f9; }
        .refresh-btn {
            background: #3b82f6;
            color: white;
            border: none;
            padding: 8px 18px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 13px;
            font-family: inherit;
        }
        .refresh-btn:hover { background: #2563eb; }
        table { width: 100%; border-collapse: collapse; }
        th {
            background: rgba(15, 23, 42, 0.4);
            padding: 14px 18px;
            text-align: left;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #64748b;
            font-weight: 600;
        }
        td { padding: 14px 18px; border-top: 1px solid #1e293b; font-size: 14px; }
        tr:hover td { background: rgba(59, 130, 246, 0.05); }
        .rank-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 32px; height: 32px;
            border-radius: 50%;
            font-weight: 700;
        }
        .rank-1 { background: #fbbf24; color: #1e293b; }
        .rank-2 { background: #94a3b8; color: #1e293b; }
        .rank-3 { background: #cd853f; color: #1e293b; }
        .honor-tag {
            display: inline-block;
            background: rgba(59, 130, 246, 0.15);
            color: #60a5fa;
            padding: 3px 10px;
            border-radius: 10px;
            font-size: 12px;
        }
        .footer {
            text-align: center;
            padding: 30px;
            color: #475569;
            font-size: 12px;
        }
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #64748b;
        }
        .empty-state .icon { font-size: 48px; margin-bottom: 16px; }
        .api-section {
            margin-top: 24px;
            background: rgba(30, 41, 59, 0.4);
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 20px 24px;
        }
        .api-section h3 { font-size: 15px; color: #94a3b8; margin-bottom: 12px; }
        .api-endpoints { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }
        .api-endpoint {
            background: rgba(15, 23, 42, 0.4);
            padding: 8px 14px;
            border-radius: 6px;
            font-size: 12px;
            font-family: 'Consolas', 'Courier New', monospace;
        }
        .method { color: #22c55e; font-weight: 700; }
        .path { color: #94a3b8; }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>🍅 <span>智能日程管理器</span></h1>
            <div class="subtitle">Smart Schedule Manager · 积分排行榜 · Flask Web 界面</div>
        </div>
        <div style="text-align:right">
            <div style="font-size:14px;color:#f1f5f9;">总用户 <strong>{{ stats.total_users }}</strong></div>
            <div style="font-size:12px;color:#64748b;">平均 {{ stats.avg_points }} 分 · 最高 {{ stats.max_points }} 分</div>
        </div>
    </div>

    <div class="container">
        <!-- 统计卡片 -->
        <div class="stats-row">
            <div class="stat-card">
                <div class="value">{{ stats.total_users }}</div>
                <div class="label">注册用户</div>
            </div>
            <div class="stat-card">
                <div class="value">{{ stats.avg_points }}</div>
                <div class="label">人均积分</div>
            </div>
            <div class="stat-card">
                <div class="value">{{ stats.max_points }}</div>
                <div class="label">最高积分</div>
            </div>
            <div class="stat-card">
                <div class="value">{{ stats.max_honor }}</div>
                <div class="label">最高称号</div>
            </div>
        </div>

        <!-- 称号徽章 -->
        <div class="honor-badges">
            {% for threshold, title in honor_levels %}
            <span class="honor-badge {% if stats.max_points >= threshold %}earned{% endif %}">
                {{ title }} ({{ threshold }}+)
            </span>
            {% endfor %}
        </div>

        <!-- 排行榜 -->
        <div class="leaderboard-section">
            <div class="leaderboard-header">
                <h2>🏆 积分排行榜 TOP 50</h2>
                <button class="refresh-btn" onclick="location.reload()">刷新</button>
            </div>
            {% if leaderboard %}
            <table>
                <thead>
                    <tr>
                        <th>排名</th>
                        <th>用户</th>
                        <th>积分</th>
                        <th>称号</th>
                        <th>更新时间</th>
                    </tr>
                </thead>
                <tbody>
                    {% for user in leaderboard %}
                    <tr>
                        <td>
                            {% if user.rank == 1 %}
                            <span class="rank-badge rank-1">🥇</span>
                            {% elif user.rank == 2 %}
                            <span class="rank-badge rank-2">🥈</span>
                            {% elif user.rank == 3 %}
                            <span class="rank-badge rank-3">🥉</span>
                            {% else %}
                            <span style="padding-left:8px;color:#64748b;">{{ user.rank }}</span>
                            {% endif %}
                        </td>
                        <td style="font-weight:600;">{{ user.username }}</td>
                        <td style="color:#3b82f6;font-weight:700;">{{ user.points }}</td>
                        <td><span class="honor-tag">{{ user.honor_title }}</span></td>
                        <td style="color:#64748b;font-size:12px;">{{ user.updated_at }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <div class="empty-state">
                <div class="icon">📭</div>
                <div>暂无排行数据</div>
                <div style="font-size:12px;margin-top:8px;">注册并完成番茄钟后即可上榜</div>
            </div>
            {% endif %}
        </div>

        <!-- API 接口列表 -->
        <div class="api-section">
            <h3>📡 REST API 接口列表</h3>
            <div class="api-endpoints">
                <div class="api-endpoint"><span class="method">GET</span> <span class="path">/health</span></div>
                <div class="api-endpoint"><span class="method">GET</span> <span class="path">/leaderboard</span></div>
                <div class="api-endpoint"><span class="method">POST</span> <span class="path">/register</span></div>
                <div class="api-endpoint"><span class="method">POST</span> <span class="path">/login</span></div>
                <div class="api-endpoint"><span class="method">POST</span> <span class="path">/sync_points</span></div>
                <div class="api-endpoint"><span class="method">POST</span> <span class="path">/delete_account</span></div>
            </div>
        </div>
    </div>

    <div class="footer">
        Smart Schedule Manager v2.0 · Flask Web Dashboard · {{ now }}
    </div>
</body>
</html>"""


# ==================== Web 仪表盘路由 ====================

@app.route("/")
def dashboard():
    """Web 仪表盘首页 —— 展示排行榜和统计信息"""
    leaderboard = get_leaderboard_data(50)
    stats = get_server_stats()
    return render_template_string(
        DASHBOARD_TEMPLATE,
        leaderboard=leaderboard,
        stats=stats,
        honor_levels=HONOR_LEVELS,
        now=now_text(),
    )


# ==================== REST API 路由 ====================

@app.route("/health", methods=["GET"])
def api_health():
    """健康检查"""
    return jsonify({"ok": True, "message": "score server running (Flask)"})


@app.route("/leaderboard", methods=["GET"])
def api_leaderboard():
    """获取排行榜"""
    leaderboard = get_leaderboard_data()
    return jsonify({"ok": True, "leaderboard": leaderboard})


@app.route("/register", methods=["POST"])
def api_register():
    """注册新用户"""
    client_ip = request.remote_addr
    if not _register_limiter.is_allowed(client_ip):
        return jsonify({"ok": False, "error": "注册请求过于频繁，请稍后再试"}), 429

    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    points = 0

    if not (3 <= len(username) <= 20):
        return jsonify({"ok": False, "error": "用户名长度应为 3-20 个字符"}), 400
    if not re.match(r'^[A-Za-z0-9_]+$', username):
        return jsonify({"ok": False, "error": "用户名只能包含字母、数字和下划线"}), 400
    if len(password) < 6:
        return jsonify({"ok": False, "error": "密码至少 6 位"}), 400

    salt_text, hash_text = hash_password(password)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO users (username, password_salt, password_hash, points, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (username, salt_text, hash_text, points, now_text(), now_text()),
        )
        user_id = cur.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"ok": False, "error": "用户名已存在"}), 409
    conn.close()

    token = create_session(user_id)
    return jsonify({
        "ok": True,
        "token": token,
        "username": username,
        "points": points,
        "honor_title": get_honor_title(points),
    })


@app.route("/login", methods=["POST"])
def api_login():
    """登录"""
    client_ip = request.remote_addr
    if not _login_limiter.is_allowed(client_ip):
        return jsonify({"ok": False, "error": "登录请求过于频繁，请稍后再试"}), 429

    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, password_salt, password_hash, points FROM users WHERE username=?",
        (username,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({"ok": False, "error": "用户名或密码错误"}), 401

    user_id, salt_text, hash_text, points = row
    if not verify_password(password, salt_text, hash_text):
        return jsonify({"ok": False, "error": "用户名或密码错误"}), 401

    token = create_session(user_id)
    return jsonify({
        "ok": True,
        "token": token,
        "username": username,
        "points": points,
        "honor_title": get_honor_title(points),
    })


@app.route("/sync_points", methods=["POST"])
def api_sync_points():
    """同步积分"""
    user = get_current_user()
    if not user:
        return jsonify({"ok": False, "error": "请先登录"}), 401

    data = request.get_json(silent=True) or {}
    client_points = max(int(data.get("points", 0) or 0), 0)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT points FROM users WHERE id=?", (user["id"],))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"ok": False, "error": "用户不存在"}), 404

    current_points = row[0]
    if client_points < current_points:
        conn.close()
        return jsonify({"ok": False, "error": "积分不能减少"}), 400

    increase = client_points - current_points
    if increase > MAX_POINTS_PER_SYNC:
        conn.close()
        return jsonify({"ok": False, "error": f"单次同步积分增长超过上限 {MAX_POINTS_PER_SYNC}"}), 400

    new_points = client_points
    cur.execute(
        "UPDATE users SET points=?, updated_at=? WHERE id=?",
        (new_points, now_text(), user["id"]),
    )
    conn.commit()
    conn.close()
    return jsonify({
        "ok": True,
        "username": user["username"],
        "points": new_points,
        "honor_title": get_honor_title(new_points),
    })


@app.route("/delete_account", methods=["POST"])
def api_delete_account():
    """注销账号"""
    user = get_current_user()
    if not user:
        return jsonify({"ok": False, "error": "请先登录"}), 401

    data = request.get_json(silent=True) or {}
    password = str(data.get("password", ""))

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT password_salt, password_hash FROM users WHERE id=?",
        (user["id"],),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"ok": False, "error": "账号不存在"}), 404

    salt_text, hash_text = row
    if not verify_password(password, salt_text, hash_text):
        conn.close()
        return jsonify({"ok": False, "error": "密码错误，无法注销账号"}), 401

    cur.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    cur.execute("DELETE FROM users WHERE id=?", (user["id"],))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "username": user["username"]})


# ==================== 服务器启动 ====================

def run_server():
    """启动 Flask 服务器"""
    init_db()
    print(f"[OK] 积分排行服务器已启动(Flask): http://{HOST}:{PORT}")
    print(f"     Web 仪表盘: http://{HOST}:{PORT}/")
    print(f"     REST API: http://{HOST}:{PORT}/health")
    print("    按 Ctrl+C 停止服务器")
    # Flask 在生产环境使用 threaded=True 处理并发
    app.run(host=HOST, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    run_server()
