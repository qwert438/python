document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-open-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = document.getElementById(button.dataset.openDialog);
      if (dialog) {
        dialog.showModal();
      }
    });
  });

  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = button.closest("dialog");
      if (dialog) {
        dialog.close();
      }
    });
  });

  document.querySelectorAll("[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) {
        event.preventDefault();
      }
    });
  });

  const tabs = document.querySelectorAll("[data-tab]");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      document.querySelectorAll("[data-panel]").forEach((panel) => {
        panel.classList.toggle("hidden", panel.dataset.panel !== tab.dataset.tab);
      });
    });
  });

  loadCharts();
});

async function loadCharts() {
  const focusCanvas = document.getElementById("focusChart");
  if (!focusCanvas) {
    return;
  }

  const response = await fetch("/api/stats");
  const data = await response.json();
  drawBarChart(focusCanvas, data.days, data.focus_minutes, "#2563eb", "分钟");
  drawLineChart(document.getElementById("completeChart"), data.days, data.completed_tasks, "#16a34a", "个");
  drawDonutChart(document.getElementById("priorityChart"), data.priority_labels, data.priority_counts);
}

function setupCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * ratio;
  canvas.height = rect.height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.font = "12px Microsoft YaHei, Arial";
  ctx.fillStyle = "#64748b";
  ctx.strokeStyle = "#d8e0ea";
  return { ctx, width: rect.width, height: rect.height };
}

function drawAxes(ctx, width, height, maxValue) {
  const left = 38;
  const right = 12;
  const top = 12;
  const bottom = 34;
  const chartWidth = width - left - right;
  const chartHeight = height - top - bottom;

  ctx.strokeStyle = "#d8e0ea";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(left, top);
  ctx.lineTo(left, top + chartHeight);
  ctx.lineTo(width - right, top + chartHeight);
  ctx.stroke();

  for (let i = 0; i <= 4; i += 1) {
    const y = top + chartHeight - (chartHeight * i / 4);
    const value = Math.round(maxValue * i / 4);
    ctx.fillStyle = "#64748b";
    ctx.fillText(String(value), 6, y + 4);
    ctx.strokeStyle = "#eef2f7";
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(width - right, y);
    ctx.stroke();
  }

  return { left, top, chartWidth, chartHeight, bottom };
}

function drawBarChart(canvas, labels, values, color, unit) {
  const { ctx, width, height } = setupCanvas(canvas);
  const maxValue = Math.max(1, ...values);
  const area = drawAxes(ctx, width, height, maxValue);
  const gap = 8;
  const barWidth = Math.max(12, (area.chartWidth - gap * (values.length - 1)) / values.length);

  values.forEach((value, index) => {
    const x = area.left + index * (barWidth + gap);
    const barHeight = area.chartHeight * value / maxValue;
    const y = area.top + area.chartHeight - barHeight;
    ctx.fillStyle = color;
    ctx.fillRect(x, y, barWidth, barHeight);
    ctx.fillStyle = "#64748b";
    ctx.fillText(labels[index].slice(5), x - 2, height - 10);
    if (value > 0) {
      ctx.fillText(`${value}${unit}`, x, y - 5);
    }
  });
}

function drawLineChart(canvas, labels, values, color, unit) {
  const { ctx, width, height } = setupCanvas(canvas);
  const maxValue = Math.max(1, ...values);
  const area = drawAxes(ctx, width, height, maxValue);
  const step = area.chartWidth / Math.max(values.length - 1, 1);

  ctx.beginPath();
  values.forEach((value, index) => {
    const x = area.left + step * index;
    const y = area.top + area.chartHeight - (area.chartHeight * value / maxValue);
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.stroke();

  values.forEach((value, index) => {
    const x = area.left + step * index;
    const y = area.top + area.chartHeight - (area.chartHeight * value / maxValue);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#64748b";
    ctx.fillText(labels[index].slice(5), x - 15, height - 10);
    if (value > 0) {
      ctx.fillText(`${value}${unit}`, x - 8, y - 8);
    }
  });
}

function drawDonutChart(canvas, labels, values) {
  const { ctx, width, height } = setupCanvas(canvas);
  const colors = ["#dc2626", "#d97706", "#16a34a"];
  const total = values.reduce((sum, value) => sum + value, 0);
  const cx = width / 2;
  const cy = height / 2 - 8;
  const radius = Math.min(width, height) * 0.28;
  let start = -Math.PI / 2;

  if (total === 0) {
    ctx.strokeStyle = "#d8e0ea";
    ctx.lineWidth = 28;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = "#64748b";
    ctx.textAlign = "center";
    ctx.fillText("暂无完成任务", cx, cy + 4);
    ctx.textAlign = "start";
    return;
  }

  values.forEach((value, index) => {
    const angle = (value / total) * Math.PI * 2;
    ctx.strokeStyle = colors[index];
    ctx.lineWidth = 28;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, start, start + angle);
    ctx.stroke();
    start += angle;
  });

  const legendY = height - 24;
  labels.forEach((label, index) => {
    const x = 24 + index * 72;
    ctx.fillStyle = colors[index];
    ctx.fillRect(x, legendY - 10, 10, 10);
    ctx.fillStyle = "#64748b";
    ctx.fillText(`${label}: ${values[index]}`, x + 16, legendY);
  });
}
