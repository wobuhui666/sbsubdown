import os
import requests
import json
import sys

# 从环境变量获取 Alist 配置
ALIST_URL = os.getenv("ALIST_URL")
ALIST_USERNAME = os.getenv("ALIST_USERNAME")
ALIST_PASSWORD = os.getenv("ALIST_PASSWORD")

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

def check_env_vars():
    """检查所需的环境变量是否已设置"""
    if not all([ALIST_URL, ALIST_USERNAME, ALIST_PASSWORD]):
        print("错误：环境变量 ALIST_URL, ALIST_USERNAME, ALIST_PASSWORD 必须全部设置。")
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
        sys.exit(1)

def add_offline_download(token, magnet_link):
    """通过 Alist API 添加离线下载任务"""
    download_url = f"{ALIST_URL}/api/fs/add_offline_download"
    headers = {"Authorization": token}
    # Alist API 需要将任务添加到特定路径
    payload = {"path": "/", "urls": [magnet_link]} 
    try:
        response = requests.post(download_url, headers=headers, json=payload)
        response.raise_for_status()
        print("成功将任务添加到 Alist 离线下载。")
        print(f"响应: {response.json().get('message')}")
    except requests.exceptions.RequestException as e:
        print(f"添加到 Alist 离线下载失败: {e}")
        print(f"服务器响应: {response.text}")
        sys.exit(1)

def find_latest_episode_magnet():
    """获取数据并找到最新剧集的磁力链接"""
    try:
        response = requests.get(DATA_URL)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"获取数据失败: {e}")
        return None
    except json.JSONDecodeError:
        print("解析 JSON 数据失败。")
        return None

    tv_shows = data.get("res", [])[0][4]
    latest_episode_num = -1
    latest_episode_info = None

    for episode_num_str, value in tv_shows.items():
        title = value[1]
        # 忽略特定标题的剧集
        if "（本集未被日本官网计入总集数）" in title:
            continue

        try:
            # 转换集数以进行比较，支持浮点数
            current_episode_num = float(episode_num_str)
            if current_episode_num > latest_episode_num:
                latest_episode_num = current_episode_num
                latest_episode_info = value
        except ValueError:
            print(f"警告：无法解析集数 '{episode_num_str}'，已跳过。")
            continue
    
    if not latest_episode_info:
        print("未找到符合条件的最新剧集。")
        return None

    downloads = latest_episode_info[7]
    webrip_downloads = downloads.get("WEBRIP", [])
    
    for item in webrip_downloads:
        # 格式位于 item[1]，磁力链接位于 item[2]
        if len(item) > 2 and item[1] == "简繁日MKV":
            magnet = item[2]
            print(f"找到最新剧集 {latest_episode_num}: {latest_episode_info[1]}")
            print(f"找到 '简繁日MKV' 格式的磁力链接。")
            return magnet + TRACKERS_TO_ADD

    print("在最新剧集中未找到 '简繁日MKV' 格式的下载链接。")
    return None

def main():
    """主函数"""
    check_env_vars()
    magnet_link = find_latest_episode_magnet()
    if magnet_link:
        print("正在获取 Alist token...")
        token = get_alist_token()
        if token:
            print("正在添加任务到 Alist...")
            add_offline_download(token, magnet_link)

if __name__ == "__main__":
    main()