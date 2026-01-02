FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制代码
COPY squeeze_monitor.py .

# 创建数据目录
RUN mkdir -p /app/data

WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1

# 运行程序
CMD ["python", "-u", "squeeze_monitor.py"]
