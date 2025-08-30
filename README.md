# 自动下载最新动漫剧集到 Alist

这是一个Docker化的Python应用程序，它可以自动从指定的数据源获取最新的“电视动画”剧集，提取特定格式的磁力链接，并将其添加到Alist进行离线下载。

## 功能

- 从 `https://cloud.sbsub.com/data/data.json` 获取最新的动漫数据。
- 自动识别并筛选出最新一集（支持小数集数）。
- 忽略标题中包含特定标记（`（本集未被日本官网计入总集数）`）的剧集。
- 提取 `简繁日MKV` 格式的磁力链接。
- 自动为磁力链接添加一组预设的Trackers。
- 通过Alist API将处理后的磁力链接添加到离线下载任务中。

## 如何运行

### 1. 先决条件

- 已安装 [Docker](https://www.docker.com/)。
- 拥有一个正在运行的 Alist 实例。

### 2. 配置

在运行容器之前，您需要设置以下环境变量来配置Alist连接信息：

- `ALIST_URL`: 您的Alist实例地址 (例如: `http://192.168.1.10:5244`)。
- `ALIST_USERNAME`: 您的Alist用户名。
- `ALIST_PASSWORD`: 您的Alist密码。

### 3. 构建并运行 Docker 容器

您可以使用以下命令来构建和运行此应用程序的Docker容器。

#### 构建镜像
在项目根目录下（与 `Dockerfile` 文件位于同一目录），运行以下命令来构建Docker镜像：
```bash
docker build -t alist-downloader .
```

#### 运行容器
使用上一步构建的镜像来运行容器，并通过 `-e` 参数传入所需的环境变量：
```bash
docker run --rm \
  -e ALIST_URL="http://your-alist-url:5244" \
  -e ALIST_USERNAME="your-username" \
  -e ALIST_PASSWORD="your-password" \
  alist-downloader
```
将 `"http://your-alist-url:5244"`, `"your-username"`, 和 `"your-password"` 替换为您的实际Alist配置。

`--rm` 参数会在容器执行完毕后自动删除它，适合一次性任务。如果您希望定期运行，可以考虑使用 `cron` 或其他调度工具来执行 `docker run` 命令。
