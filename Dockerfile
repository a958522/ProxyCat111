FROM python:3.11

WORKDIR /app

# 安装 curl (用于国家检测)
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip -i https://pypi.mirrors.ustc.edu.cn/simple/ && \
    pip install --no-cache-dir -r requirements.txt -i https://pypi.mirrors.ustc.edu.cn/simple/

COPY . .

RUN rm -f config/config.ini

VOLUME ["/app/config", "/app/logs"]

# 暴露端口
EXPOSE 1080 5000

CMD ["python", "app.py"]
