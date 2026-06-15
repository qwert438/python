from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

from core import DataManager, Schedule, Task, format_duration


app = Flask(__name__)
app.secret_key = "smart-schedule-web-dev-key"


PRIORITY_LABELS = {
    1: "高优先级",
    2: "中优先级",
    3: "低优先级",
}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def current_username():
    return session.get("username")


def current_data_manager():
    return DataManager(current_username())


def load_schedule(dm=None):
    dm = dm or current_data_manager()
    schedule = Schedule()
    for task_data in dm.load_tasks():
        try:
            schedule.add_task(Task.from_dict(task_data))
        except Exception:
            continue
    release_due_pending_tasks(schedule)
    return schedule


def save_schedule(schedule, dm=None):
    dm = dm or current_data_manager()
    dm.save_tasks([task.to_dict() for task in schedule.tasks])


def release_due_pending_tasks(schedule):
    today = datetime.now().date()
    changed = False
    for task in schedule.tasks:
        if not task.completed and task.scheduled_date and task.scheduled_date.date() <= today:
            task.scheduled_date = None
            changed = True
    return changed


def parse_datetime(value):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt == "%Y-%m-%d":
                return parsed.replace(hour=23, minute=59)
            return parsed
        except ValueError:
            pass
    raise ValueError("请输入有效的截止时间")


def parse_scheduled_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("请输入有效的计划日期") from exc
    if parsed.date() <= datetime.now().date():
        return None
    return parsed


def build_task_from_form(form, task=None):
    title = (form.get("title") or "").strip()
    if not title:
        raise ValueError("任务标题不能为空")

    description = (form.get("description") or "").strip()
    due_date = parse_datetime(form.get("due_date"))
    scheduled_date = parse_scheduled_date(form.get("scheduled_date"))

    try:
        priority = int(form.get("priority", 3))
        target_pomodoros = int(form.get("target_pomodoros", 1))
    except ValueError as exc:
        raise ValueError("优先级和番茄目标必须是数字") from exc

    if priority not in PRIORITY_LABELS:
        raise ValueError("请选择有效的优先级")
    if target_pomodoros < 1 or target_pomodoros > 20:
        raise ValueError("番茄目标必须在 1 到 20 之间")

    if task is None:
        task = Task(title, description, due_date, priority, scheduled_date)
        task.completed_pomodoros = 0
    else:
        task.title = title
        task.description = description
        task.due_date = due_date
        task.scheduled_date = scheduled_date
        task.priority = priority
    task.target_pomodoros = target_pomodoros
    return task


def task_to_view(task):
    data = task.to_dict()
    data.update(
        {
            "priority_label": PRIORITY_LABELS.get(task.priority, "低优先级"),
            "cost_label": format_duration(task.cost_sec),
            "due_display": task.due_date.strftime("%Y-%m-%d %H:%M") if task.due_date else "无截止时间",
            "due_form": task.due_date.strftime("%Y-%m-%dT%H:%M") if task.due_date else "",
            "scheduled_display": task.scheduled_date.strftime("%Y-%m-%d") if task.scheduled_date else "",
            "scheduled_form": task.scheduled_date.strftime("%Y-%m-%d") if task.scheduled_date else "",
            "created_display": task.created_at.strftime("%Y-%m-%d %H:%M"),
            "finished_display": task.finished_at.strftime("%Y-%m-%d %H:%M") if task.finished_at else "",
            "priority_score": task.calculate_priority_score(),
        }
    )
    return data


def summarize(dm, schedule):
    stats = schedule.get_statistics()
    pomodoro = dm.get_pomodoro_stats()
    points = dm.get_points_profile()
    return {
        "total": stats["total"],
        "completed": stats["completed"],
        "completion_rate": round(stats["completion_rate"], 1),
        "active": len(schedule.get_uncompleted_tasks()),
        "pending": len(schedule.get_pending_tasks()),
        "today_focus": format_duration(pomodoro["today_focus"]),
        "weekly_count": pomodoro["weekly_count"],
        "total_focus": format_duration(pomodoro["total_focus"]),
        "points": points,
    }


def chart_payload(dm, schedule):
    records = []
    history_records = []
    if dm.db_path:
        import sqlite3

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
    for start_time, duration in records:
        date_key = start_time.split(" ")[0]
        work_time[date_key] = work_time.get(date_key, 0) + duration

    completed_by_day = {}
    for completed_at, _ in history_records:
        date_key = completed_at.split(" ")[0]
        completed_by_day[date_key] = completed_by_day.get(date_key, 0) + 1

    today = datetime.now().date()
    days = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    priority_stats = schedule.get_statistics()["priority_stats"]

    return {
        "days": days,
        "focus_minutes": [round(work_time.get(day, 0) / 60, 1) for day in days],
        "completed_tasks": [completed_by_day.get(day, 0) for day in days],
        "priority_labels": ["高", "中", "低"],
        "priority_counts": [priority_stats.get(1, 0), priority_stats.get(2, 0), priority_stats.get(3, 0)],
    }


@app.template_filter("duration")
def duration_filter(seconds):
    return format_duration(seconds)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        if not username:
            flash("请输入用户名", "error")
            return render_template("login.html")
        session["username"] = username
        DataManager(username)
        flash(f"已进入账号：{username}", "success")
        return redirect(url_for("dashboard"))
    if session.get("username"):
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("已退出当前账号", "success")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    dm = current_data_manager()
    schedule = load_schedule(dm)
    if release_due_pending_tasks(schedule):
        save_schedule(schedule, dm)

    task_groups = {
        "active": [task_to_view(task) for task in schedule.get_uncompleted_tasks()],
        "pending": [task_to_view(task) for task in schedule.get_pending_tasks()],
        "completed": [task_to_view(task) for task in schedule.get_completed_tasks()],
    }
    return render_template(
        "dashboard.html",
        username=current_username(),
        summary=summarize(dm, schedule),
        task_groups=task_groups,
        priority_labels=PRIORITY_LABELS,
        today=datetime.now().strftime("%Y-%m-%d"),
    )


@app.route("/tasks", methods=["POST"])
@login_required
def create_task():
    dm = current_data_manager()
    schedule = load_schedule(dm)
    try:
        schedule.add_task(build_task_from_form(request.form))
        save_schedule(schedule, dm)
        flash("任务已创建", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("dashboard"))


@app.route("/tasks/<task_id>", methods=["POST"])
@login_required
def update_task(task_id):
    dm = current_data_manager()
    schedule = load_schedule(dm)
    task = schedule.get_task_by_id(task_id)
    if not task:
        flash("任务不存在", "error")
        return redirect(url_for("dashboard"))

    try:
        build_task_from_form(request.form, task)
        save_schedule(schedule, dm)
        flash("任务已更新", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("dashboard"))


@app.route("/tasks/<task_id>/complete", methods=["POST"])
@login_required
def complete_task(task_id):
    dm = current_data_manager()
    schedule = load_schedule(dm)
    task = schedule.get_task_by_id(task_id)
    if not task:
        flash("任务不存在", "error")
        return redirect(url_for("dashboard"))

    try:
        cost_sec = int(request.form.get("cost_sec") or 1500)
    except ValueError:
        cost_sec = 1500
    cost_sec = min(max(cost_sec, 1), 28800)

    task.completed = True
    task.finished_at = datetime.now()
    task.cost_sec = cost_sec
    task.completed_pomodoros = max(task.completed_pomodoros, task.target_pomodoros)
    dm.delete_task_history(task.id)
    dm.save_task_history(task)
    save_schedule(schedule, dm)
    flash("任务已标记完成", "success")
    return redirect(url_for("dashboard"))


@app.route("/tasks/<task_id>/restore", methods=["POST"])
@login_required
def restore_task(task_id):
    dm = current_data_manager()
    schedule = load_schedule(dm)
    task = schedule.get_task_by_id(task_id)
    if not task:
        flash("任务不存在", "error")
        return redirect(url_for("dashboard"))

    task.completed = False
    task.finished_at = None
    task.cost_sec = 0
    task.completed_pomodoros = 0
    task.scheduled_date = None
    dm.delete_task_history(task.id)
    save_schedule(schedule, dm)
    flash("任务已恢复为未完成", "success")
    return redirect(url_for("dashboard"))


@app.route("/tasks/<task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id):
    dm = current_data_manager()
    schedule = load_schedule(dm)
    if schedule.get_task_by_id(task_id):
        schedule.remove_task(task_id)
        dm.delete_task_history(task_id)
        save_schedule(schedule, dm)
        flash("任务已删除", "success")
    else:
        flash("任务不存在", "error")
    return redirect(url_for("dashboard"))


@app.route("/pomodoro", methods=["POST"])
@login_required
def record_pomodoro():
    dm = current_data_manager()
    task_id = request.form.get("task_id") or None
    try:
        minutes = int(request.form.get("minutes") or 25)
    except ValueError:
        minutes = 25
    minutes = min(max(minutes, 1), 240)

    end_time = datetime.now()
    start_time = end_time - timedelta(minutes=minutes)
    updated, task_data, points_update = dm.save_pomodoro(start_time, end_time, "work", task_id)

    if updated and task_data and task_data.get("completed"):
        schedule = load_schedule(dm)
        task = schedule.get_task_by_id(task_id)
        if task:
            dm.delete_task_history(task.id)
            dm.save_task_history(task)

    if points_update and points_update.get("leveled_up"):
        flash(f"番茄已记录，积分 +{points_update['earned']}，称号升级为 {points_update['title']}", "success")
    else:
        flash(f"番茄已记录，积分 +{points_update['earned'] if points_update else 0}", "success")
    return redirect(url_for("dashboard"))


@app.route("/api/stats")
@login_required
def api_stats():
    dm = current_data_manager()
    schedule = load_schedule(dm)
    return jsonify(chart_payload(dm, schedule))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
