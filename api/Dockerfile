FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV HEADLESS=true
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# 替换为清华源
RUN sed -i 's@http://deb.debian.org@https://mirrors.tuna.tsinghua.edu.cn@g' /etc/apt/sources.list.d/debian.sources 2>/dev/null ||     sed -i 's@http://deb.debian.org@https://mirrors.tuna.tsinghua.edu.cn@g' /etc/apt/sources.list 2>/dev/null || true

# 安装系统依赖（含 VNC + Chromium）
RUN apt-get update && apt-get install -y --no-install-recommends  wget curl gnupg ca-certificates git  ffmpeg libsm6 libxext6 libxrender1  libgbm1 libasound2 libatk1.0-0 libatk-bridge2.0-0  libcups2 libdrm2 libxkbcommon0 libxcomposite1  libxdamage1 libxfixes3 libxrandr2 libpango-1.0-0  libcairo2 libnspr4 libnss3  xvfb tigervnc-standalone-server tigervnc-common x11vnc  websockify novnc python3-numpy chromium  fonts-wqy-zenhei fonts-wqy-microhei fonts-noto-cjk procps  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 设置 pip 国内源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 安装 Playwright Python 包，但跳过浏览器下载，统一使用系统 Chromium
RUN pip install playwright

# 复制依赖文件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt  && pip install --no-cache-dir asyncpg redis websockify

# 复制应用代码
COPY . .

# 创建数据目录
RUN mkdir -p /app/data/browser_profiles /app/data/videos /app/data/audio /app/data/output /app/data/thumbnails /app/data/logs

EXPOSE 8000 5900 6080 9222

# 启动脚本默认由 docker-compose 覆盖；保留一个正确的兜底命令
CMD ["bash", "-lc", "set -e; pkill -f websockify 2>/dev/null || true; pkill -f x11vnc 2>/dev/null || true; pkill -f 'Xvfb :99' 2>/dev/null || true; rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true; Xvfb :99 -screen 0 1920x1080x24 -ac & for i in $(seq 1 20); do [ -S /tmp/.X11-unix/X99 ] && break; sleep 0.5; done; [ -S /tmp/.X11-unix/X99 ] || { echo Xvfb failed; exit 1; }; export DISPLAY=:99; x11vnc -display :99 -rfbport 5900 -forever -shared -nopw -localhost -o /tmp/x11vnc.log & for i in $(seq 1 20); do python -c 'import socket; s=socket.socket(); s.settimeout(0.2); s.connect((\"127.0.0.1\",5900)); s.close()' && break || sleep 0.5; done; python -c 'import socket; s=socket.socket(); s.settimeout(0.5); s.connect((\"127.0.0.1\",5900)); s.close()' || { echo x11vnc failed; cat /tmp/x11vnc.log || true; exit 1; }; websockify --web=/usr/share/novnc 6080 localhost:5900 & exec python main.py server --host 0.0.0.0 --port 8000"]
