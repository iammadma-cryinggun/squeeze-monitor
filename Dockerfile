FROM python:3.11-slim

WORKDIR /app

# 1. 先更新系统并设置时区（重要！）
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 2. 复制requirements.txt
COPY requirements.txt .

# 3. 安装依赖（去掉清华源，Zeabur可能访问不到）
RUN pip install --no-cache-dir -r requirements.txt

# 4. 复制所有Python文件
COPY *.py .

# 5. 创建数据目录
RUN mkdir -p data logs

# 6. 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Shanghai

# 7. 运行程序
CMD ["python", "-u", "squeeze_monitor.py"]
