# 使用官方的 Python 运行时作为基础镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 将依赖文件复制到工作目录
COPY requirements.txt .

# 安装所需的包
RUN pip install --no-cache-dir -r requirements.txt

# 将主应用程序代码复制到工作目录
COPY main.py .
# 为状态文件创建挂载点
VOLUME /data

# 暴露 Web UI 的端口
EXPOSE 5000

# 设置容器启动时要运行的命令
CMD ["python", "main.py"]