FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV HEADLESS=true

# 替换为清华源
RUN sed -i 's@http://deb.debian.org@https://mirrors.tuna.tsinghua.edu.cn@g' /etc/apt/sources.list.d/debian.sources 2>/dev/null ||     sed -i 's@http://deb.debian.org@https://mirrors.tuna.tsinghua.edu.cn@g' /etc/apt/sources.list 2>/dev/null || true

# 安装系统依赖（含 VNC）
RUN apt-get update && apt-get install -y --no-install-recommends  wget curl gnupg ca-certificates git  ffmpeg libsm6 libxext6 libxrender1  libgbm1 libasound2 libatk1.0-0 libatk-bridge2.0-0  libcups2 libdrm2 libxkbcommon0 libxcomposite1  libxdamage1 libxfixes3 libxrandr2 libpango-1.0-0  libcairo2 libnspr4 libnss3  xvfb tigervnc-standalone-server tigervnc-common x11vnc  websockify novnc python3-numpy  fonts-wqy-zenhei fonts-wqy-microhei fonts-noto-cjk procps  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 设置 pip 国内源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 安装 Playwright 和浏览器
#RUN pip install --no-cache-dir playwright &&  playwright install-deps chromium &&  playwright install chromium
#RUN pip install  playwright &&  playwright install-deps chromium &&  playwright install chromium
RUN pip install playwright && \
    playwright install-deps chromium && \
    playwright install chromium && \
    CHROME_PATH=$(find /root/.cache/ms-playwright -name chrome | head -n 1) && \
    ln -s $CHROME_PATH /usr/bin/chromium

# 复制依赖文件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt  && pip install --no-cache-dir asyncpg redis websockify

# 复制应用代码
COPY . .

# 创建数据目录
RUN mkdir -p /app/data/browser_profiles /app/data/videos /app/data/audio /app/data/output /app/data/thumbnails /app/data/logs

EXPOSE 8000 5900 6080

# 启动脚本：先启动 Xvfb + VNC，再启动 API
CMD ["bash", "-c", "rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null; Xvfb :99 -screen 0 1920x1080x24 & sleep 2; export DISPLAY=:99; vncserver :99 -geometry 1920x1080 -depth 24 -SecurityTypes None 2>/dev/null || true; sleep 1; websockify --web=/usr/share/novnc 6080 localhost:5900 & sleep 1; exec python main.py server --host 0.0.0.0 --port 8000"]
