# 智能日程管理器

番茄钟与智能日程管理工具。项目已移除原 Tkinter 桌面界面，只保留 Flask Web 界面。

## 运行 Flask Web 版

```powershell
python main.py
```

启动后在浏览器打开：

```text
http://127.0.0.1:5000
```

Web 版会使用项目目录下的 `accounts/<用户名>/tasks.json` 和 `pomodoro.db` 保存本地账号数据。
