import os
import requests
import json
import sys
import time
import threading
from flask import Flask, render_template

# 从环境变量获取 Alist 配置
ALIST_URL = os.getenv("ALIST_URL")
ALIST_USERNAME = os.getenv("ALIST_USERNAME")
ALIST_PASSWORD = os.getenv("ALIST_PASSWORD")
DOWNLOAD_PATH = os.getenv("DOWNLOAD_PATH", "/downloads/conan")
STATE_FILE_PATH = os.getenv("STATE_FILE_PATH", "/data/last_episode.txt")
START_EPISODE = int(os.getenv("START_EPISODE", 0))
UPDATE_INTERVAL_SECONDS = int(os.getenv("UPDATE_INTERVAL_SECONDS", 3600))

# 数据源 URL 和 Tracker 列表
DATA_URL = "https://cloud.sbsub.com/data/data.json"
TRACKERS_TO_ADD = (
    "&tr=http://open.acgtracker.com:1096/announce"
    "&tr=http://tracker.cyber-gateway.net:6969/announce"
    "&tr=http://tracker.acgnx.se/announce"
    "&tr=http://share.camoe.cn:8080/announce"
    "&tr=http://t.acg.rip:6699/announce"
    "&tr=https://tr.bangumi.moe:9696/announce"
    "&tr=https://tracker.forever-legend.net:443/announce"
    "&tr=https://tracker.gbitt.info:443/announce"
    "&tr=https://tracker.lilithraws.org:443/announce"
    "&tr=https://tracker.moe.pm:443/announce"
)

app = Flask(__name__)

def check_env_vars():
    """检查所需的环境变量是否已设置"""
    if not all([ALIST_URL, ALIST_USERNAME, ALIST_PASSWORD, DOWNLOAD_PATH, STATE_FILE_PATH]):
        print("错误：环境变量 ALIST_URL, ALIST_USERNAME, ALIST_PASSWORD, DOWNLOAD_PATH, STATE_FILE_PATH 必须全部设置。")
        sys.exit(1)

def get_alist_token():
    """获取 Alist API token"""
    login_url = f"{ALIST_URL}/api/auth/login"
    payload = {"username": ALIST_USERNAME, "password": ALIST_PASSWORD}
    try:
        response = requests.post(login_url, json=payload)
        response.raise_for_status()
        return response.json()["data"]["token"]
    except requests.exceptions.RequestException as e:
        print(f"获取 Alist token 失败: {e}")
        return None

def add_offline_download(token, magnet_link, new_filename):
    """通过 Alist API 添加离线下载任务并指定文件名"""
    download_url = f"{ALIST_URL}/api/fs/add_offline_download"
    headers = {"Authorization": token}
    # Alist API payload，同时指定路径和磁力链接
    payload = {"path": f"{DOWNLOAD_PATH}/{new_filename}", "url": magnet_link}
    try:
        response = requests.post(download_url, headers=headers, json=payload)
        response.raise_for_status()
        print(f"成功将任务 '{new_filename}' 添加到 Alist 离线下载。")
        print(f"响应: {response.json().get('message')}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"添加 '{new_filename}' 到 Alist 离线下载失败: {e}")
        # 检查响应是否存在，以及是否包含文本
        if response and response.text:
            print(f"服务器响应: {response.text}")
        return False

def get_last_downloaded_episode():
    """从状态文件读取最后下载的集数"""
    try:
        if os.path.exists(STATE_FILE_PATH) and os.path.getsize(STATE_FILE_PATH) > 0:
            with open(STATE_FILE_PATH, 'r') as f:
                content = f.read().strip()
                return float(content)
        else:
            print(f"状态文件 '{STATE_FILE_PATH}' 不存在或为空，将从第 {START_EPISODE} 集开始。")
            return float(START_EPISODE)
    except (IOError, ValueError) as e:
        print(f"读取或解析状态文件失败: {e}。将使用起始集数 {START_EPISODE}。")
        return float(START_EPISODE)

def update_state_file(episode_number):
    """更新状态文件，写入新的集数"""
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(STATE_FILE_PATH), exist_ok=True)
        with open(STATE_FILE_PATH, 'w') as f:
            f.write(str(episode_number))
        print(f"状态文件已更新，最新集数: {episode_number}")
    except IOError as e:
        print(f"错误：无法写入状态文件 '{STATE_FILE_PATH}': {e}")
        sys.exit(1)

def find_new_episodes(last_downloaded_episode):
    """获取数据并找到所有比记录新的剧集"""
    print("正在从数据源获取最新剧集列表...")
    try:
        response = requests.get(DATA_URL)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"获取数据失败: {e}")
        return []
    except json.JSONDecodeError:
        print("解析 JSON 数据失败。")
        return []

    tv_shows = data.get("res", [])[0][4]
    new_episodes = []

    for episode_num_str, value in tv_shows.items():
        title = value[1]
        if "（本集未被日本官网计入总集数）" in title:
            continue

        try:
            current_episode_num = float(episode_num_str)
            if current_episode_num > last_downloaded_episode:
                new_episodes.append((current_episode_num, value))
        except ValueError:
            print(f"警告：无法解析集数 '{episode_num_str}'，已跳过。")
            continue
    
    # 按集数从小到大排序
    new_episodes.sort(key=lambda x: x[0])
    return new_episodes

def run_update_checker():
    """后台运行的更新检查器"""
    print("更新检查器线程已启动...")
    while True:
        print("-" * 30)
        last_episode = get_last_downloaded_episode()
        print(f"当前记录的最新集数是: {last_episode}")
        
        new_episodes = find_new_episodes(last_episode)

        if not new_episodes:
            print("未发现新剧集。")
        else:
            print(f"发现 {len(new_episodes)} 个新剧集，准备处理...")
            token = get_alist_token()
            if not token:
                print("无法获取 Alist token，将在下次检查时重试。")
            else:
                for episode_num, episode_info in new_episodes:
                    downloads = episode_info[7]
                    webrip_downloads = downloads.get("WEBRIP", [])
                    magnet_found = False
                    for item in webrip_downloads:
                        if len(item) > 2 and item[1] == "简繁日MKV":
                            magnet = item[2] + "".join(TRACKERS_TO_ADD)
                            official_episode_num = episode_info[0]
                            japanese_title = episode_info[2]
                            new_filename = f"{official_episode_num} {japanese_title}.mkv"
                            
                            print(f"处理新剧集: {new_filename}")

                            if add_offline_download(token, magnet, new_filename):
                                update_state_file(episode_num)
                                magnet_found = True
                                break
                    
                    if not magnet_found:
                        print(f"警告: 在剧集 {episode_num} 中未找到 '简繁日MKV' 格式的下载链接。")

        print(f"所有任务处理完毕。等待 {UPDATE_INTERVAL_SECONDS} 秒后进行下一次检查...")
        time.sleep(UPDATE_INTERVAL_SECONDS)

@app.route('/')
def status_page():
    """显示当前状态的 Web 页面"""
    if os.path.exists(STATE_FILE_PATH):
        with open(STATE_FILE_PATH, 'r') as f:
            last_episode = f.read().strip()
    else:
        last_episode = "Not started yet"
    return render_template('index.html', last_episode=last_episode)

if __name__ == "__main__":
    check_env_vars()
    print("脚本启动...")
    
    # 在后台线程中运行更新检查器
    checker_thread = threading.Thread(target=run_update_checker, daemon=True)
    checker_thread.start()
    
    # 启动 Flask web 服务器
    app.run(host='0.0.0.0', port=5000)