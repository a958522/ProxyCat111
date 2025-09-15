FROM python:3.11

WORKDIR /app

# 安装 curl 和 cron (用于国家检测和定时任务)
RUN apt-get update && apt-get install -y curl cron && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip -i https://pypi.mirrors.ustc.edu.cn/simple/ && \
    pip install --no-cache-dir -r requirements.txt -i https://pypi.mirrors.ustc.edu.cn/simple/

COPY . .

# 复制清理脚本并设置权限
COPY cleanup_logs.py /app/cleanup_logs.py
RUN chmod +x /app/cleanup_logs.py

# 设置定时任务 - 每天凌晨2点执行清理
RUN echo "0 2 * * * cd /app && python cleanup_logs.py >> /app/logs/cleanup.log 2>&1" | crontab -

RUN rm -f config/config.ini

VOLUME ["/app/config", "/app/logs"]

# 暴露端口
EXPOSE 1080 5000

# 启动 cron 服务和主程序
CMD service cron start && python app.py
