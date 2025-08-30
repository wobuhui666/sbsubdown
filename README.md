# 自动下载最新动漫剧集到 Alist

这是一个Docker化的Python应用程序，它可以自动从指定的数据源获取最新的“电视动画”剧集，并将其添加到Alist进行离线下载。

此脚本现在支持**定时追新**、**断点续传**和**自动重命名**功能。

## 功能

- **定时检查**: 定期从 `https://cloud.sbsub.com/data/data.json` 获取最新的动漫数据。
- **断点续传与下载验证**: 采用“添加并验证”模式。脚本不仅会将下载任务添加到Alist，还会定期检查任务状态。只有当任务在Alist中被确认为**真正完成**后，才会更新记录，确保了下载的可靠性。
- **自动追新**: 自动发现所有未下载的新剧集，并按顺序将它们添加到下载队列。
- **文件重命名**: 根据剧集的官方集数和日语标题，将下载的文件重命名为更清晰的格式 (例如: `1231 有鬼啊！.mkv`)。
- **自动添加 Trackers**: 为每个磁力链接附加一组预设的Trackers以提高下载速度。
- **灵活配置**: 通过环境变量轻松配置 Alist 连接、下载路径、状态文件和更新频率。

## Web UI

此应用程序现在包含一个简单的 Web UI，用于显示服务的当前状态。您可以随时通过浏览器访问它，以查看最后**确认下载完成**的剧集编号。

要访问 Web UI，请在浏览器中打开 `http://<your-host-ip>:5000`。

## 如何运行

### 1. 先决条件

- 已安装 [Docker](https://www.docker.com/)。
- 拥有一个正在运行的 Alist 实例。

### 2. 配置

在运行容器之前，您需要设置以下环境变量：

#### 必要环境变量:
- `ALIST_URL`: 您的Alist实例地址 (例如: `http://192.168.1.10:5244`)。
- `ALIST_USERNAME`: 您的Alist用户名。
- `ALIST_PASSWORD`: 您的Alist密码。

#### 可选环境变量 (推荐配置):
- `DOWNLOAD_PATH`: 在 Alist 中保存下载文件的路径 (默认: `/downloads/conan`)。
- `STATE_FILE_PATH`: 用于存储下载状态的 `state.json` 文件的路径 (默认: `/data/state.json`)。**强烈建议将其持久化**。
- `START_EPISODE`: 如果状态文件不存在，从该集数开始下载 (默认: `0`)。
- `UPDATE_INTERVAL_SECONDS`: 每次检查新剧集之间的时间间隔（秒）(默认: `3600`)。

### 3. 构建并运行 Docker 容器

#### 构建镜像
在项目根目录下（与 `Dockerfile` 文件位于同一目录），运行以下命令来构建Docker镜像：
```bash
docker build -t sbsubdown .
```

#### 运行容器
使用上一步构建的镜像来运行容器。为了持久化下载状态，**强烈建议**使用 `-v` 参数挂载一个本地目录到容器的 `/data` 目录。

以下是一个完整的 `docker run` 示例命令：
```bash
docker run -d --restart=always \
  -p 5000:5000 \
  -e ALIST_URL="http://your-alist-url:5244" \
  -e ALIST_USERNAME="your-username" \
  -e ALIST_PASSWORD="your-password" \
  -e DOWNLOAD_PATH="/downloads/conan" \
  -e UPDATE_INTERVAL_SECONDS="1800" \
  -v ./my_data:/data \
  --name sbsubdown \
  sbsubdown
```
将环境变量和 `./my_data` 替换为您的实际配置。

**命令解释**:
- `-d`: 在后台运行容器。
- `-p 5000:5000`: 将主机的 5000 端口映射到容器的 5000 端口，以便您可以从外部访问 Web UI。
- `--restart=always`: 容器退出时自动重启，确保服务持续运行。
- `-v ./my_data:/data`: 将当前目录下的 `my_data` 文件夹挂载到容器的 `/data` 目录。这会使容器在 `my_data` 文件夹中创建 `state.json` 状态文件，从而在容器重启后保留下载进度。
