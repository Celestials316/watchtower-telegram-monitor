FROM alpine:3.19

LABEL maintainer="Your Name <your.email@example.com>" \
      description="Docker container monitoring with Telegram notifications" \
      version="3.3.0"

# 安装依赖
RUN apk add --no-cache \
    bash \
    curl \
    docker-cli \
    coreutils \
    grep \
    sed \
    tzdata \
    jq \
    && rm -rf /var/cache/apk/*

# 设置时区
ENV TZ=Asia/Shanghai

# 创建工作目录
WORKDIR /app

# 复制脚本文件
COPY scripts/monitor.sh /app/monitor.sh
COPY scripts/manage.sh /app/manage.sh

# 设置执行权限
RUN chmod +x /app/*.sh

# 创建数据目录
RUN mkdir -p /data

# 健康检查
HEALTHCHECK --interval=60s --timeout=10s --retries=3 --start-period=15s \
    CMD ps aux | grep -v grep | grep -q 'docker logs' || exit 1

# 设置入口点
ENTRYPOINT ["/bin/bash"]
CMD ["/app/monitor.sh"]
