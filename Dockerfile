FROM python:3.12-slim

WORKDIR /app

# 中文字体 (报表 PNG 渲染要用)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# web 服务用这条; 机器人(worker)服务用 Custom Start Command 覆盖成 python -m cgroup.bot.main
CMD ["sh", "-c", "uvicorn cgroup.web.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
