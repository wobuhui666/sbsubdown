import os
import requests
import json
import sys
import time
import threading
import logging
import functools
from flask import Flask, render_template

# --- 1. 配置模块 ---
# 配置日志，确保日志会实时输出
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    stream=sys.stdout)

# 从环境变量获取 Alist 配置
ALIST_URL = os.getenv("ALIST_URL")
ALIST_USERNAME = os.getenv("ALIST_USERNAME")
ALIST_PASSWORD = os.getenv("ALIST_PASSWORD")
DOWNLOAD_PATH = os.getenv("DOWNLOAD_PATH", "/downloads/conan")
STATE_FILE_PATH = os.getenv("STATE_FILE_PATH", "/data/state.json")
START_EPISODE = int(os.getenv("START_EPISODE", 0))
UPDATE_INTERVAL_SECONDS = int(os.getenv("UPDATE_INTERVAL_SECONDS", 3600))
ALIST_TOOL = os.getenv("ALIST_TOOL", "aria2")
ALIST_DELETE_POLICY = os.getenv("ALIST_DELETE_POLICY", "delete_on_upload_succeed")

# 持久化重试的最大次数
MAX_RENAME_ATTEMPTS = 5

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

# 初始化 Flask App
app = Flask(__name__)

# --- 2. 通用重试装饰器 ---
def retry_on_failure(retries=3, delay=5, allowed_exceptions=(requests.exceptions.RequestException,)):
    """
    一个通用的重试装饰器，用于处理瞬时网络故障。
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(1, retries + 1):
                try:
                    return func(*args, **kwargs)
                except allowed_exceptions as e:
                    if i == retries:
                        logging.error(f"函数 '{func.__name__}' 在 {retries} 次尝试后最终失败。错误: {e}")
                        return None
                    logging.warning(f"函数 '{func.__name__}' 失败 (第 {i}/{retries} 次尝试)。将在 {delay} 秒后重试... 错误: {e}")
                    time.sleep(delay)
        return wrapper
    return decorator

# --- 3. 核心功能函数 ---
def check_env_vars():
    """检查所需的环境变量是否已设置"""
    if not all([ALIST_URL, ALIST_USERNAME, ALIST_PASSWORD, DOWNLOAD_PATH, STATE_FILE_PATH]):
        logging.error("环境变量 ALIST_URL, ALIST_USERNAME, ALIST_PASSWORD, DOWNLOAD_PATH, STATE_FILE_PATH 必须全部设置。")
        sys.exit(1)

@retry_on_failure(retries=3, delay=5)
def get_alist_token():
    """获取 Alist API token"""
    login_url = f"{ALIST_URL}/api/auth/login"
    payload = {"username": ALIST_USERNAME, "password": ALIST_PASSWORD}
    response = requests.post(login_url, json=payload, timeout=10)
    response.raise_for_status()
    return response.json()["data"]["token"]

@retry_on_failure(retries=3, delay=3)
def add_offline_download(token, magnet_link):
    """通过 Alist API 添加离线下载任务并返回任务 ID"""
    download_url = f"{ALIST_URL}/api/fs/add_offline_download"
    headers = {"Authorization": token}
    payload = {"path": DOWNLOAD_PATH, "urls": [magnet_link], "tool": ALIST_TOOL, "delete_policy": ALIST_DELETE_POLICY}
    response = requests.post(download_url, headers=headers, json=payload, timeout=10)
    response.raise_for_status()
    response_data = response.json()
    task_id = response_data.get("data", {}).get("tasks", [{}])[0].get("id")
    if task_id:
        logging.info(f"成功将任务添加到 Alist 目录 '{DOWNLOAD_PATH}'，任务ID: {task_id}")
        return task_id
    logging.warning(f"添加下载任务成功，但响应中未找到任务 ID。响应: {response.text}")
    return None

@retry_on_failure(retries=3, delay=3)
def get_offline_download_tasks(token):
    """获取 Alist 中的所有离线下载任务"""
    tasks_url = f"{ALIST_URL}/api/fs/offline_download_tasks"
    headers = {"Authorization": token}
    response = requests.get(tasks_url, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json().get("data", [])

@retry_on_failure(retries=3, delay=2)
def list_files(token, path):
    """通过 Alist API 列出指定路径下的文件"""
    list_url = f"{ALIST_URL}/api/fs/list"
    headers = {"Authorization": token}
    payload = {"path": path, "page": 1, "per_page": 0}
    response = requests.post(list_url, headers=headers, json=payload, timeout=15)
    response.raise_for_status()
    response_data = response.json()
    if response_data.get("code") == 200:
        return response_data.get("data", {}).get("content", [])
    logging.error(f"列出目录 '{path}' 文件失败。服务器响应: {response.text}")
    return None

@retry_on_failure(retries=3, delay=2)
def rename_file(token, src_directory, original_name, new_name):
    """通过 Alist API 重命名文件"""
    rename_url = f"{ALIST_URL}/api/fs/rename"
    headers = {"Authorization": token}
    payload = {"src_dir": src_directory, "name": original_name, "new_name": new_name}
    response = requests.post(rename_url, headers=headers, json=payload, timeout=10)
    response.raise_for_status()
    if response.json().get("code") == 200:
        logging.info(f"成功将 '{original_name}' 重命名为 '{new_name}'")
        return True
    logging.error(f"重命名文件 '{original_name}' 失败。服务器响应: {response.text}")
    return False

def load_state():
    """从 state.json 加载状态，如果不存在则创建"""
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, 'r') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logging.warning(f"读取或解析状态文件失败: {e}。将使用默认状态。")
    return {"last_completed_episode": float(START_EPISODE), "pending_tasks": []}

def save_state(state):
    """将状态保存到 state.json"""
    try:
        os.makedirs(os.path.dirname(STATE_FILE_PATH), exist_ok=True)
        with open(STATE_FILE_PATH, 'w') as f:
            json.dump(state, f, indent=2)
    except IOError as e:
        logging.error(f"无法写入状态文件 '{STATE_FILE_PATH}': {e}")
        sys.exit(1)

@retry_on_failure(retries=3, delay=10, allowed_exceptions=(requests.exceptions.RequestException, json.JSONDecodeError))
def fetch_data_from_source(url):
    """专门用于获取数据源的函数，以便应用重试逻辑"""
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.json()

def find_new_episodes(last_completed_episode):
    """获取数据并找到所有比记录新的剧集"""
    logging.info("正在从数据源获取最新剧集列表...")
    data = fetch_data_from_source(DATA_URL)
    if not data:
        logging.error("获取数据失败，已跳过本次剧集检查。")
        return []
    
    tv_shows = data.get("res", [])[0][4]
    new_episodes = []
    for episode_num_str, value in tv_shows.items():
        if "（本集未被日本官网计入总集数）" in value[1]: continue
        try:
            if float(episode_num_str) > last_completed_episode:
                new_episodes.append((float(episode_num_str), value))
        except ValueError:
            logging.warning(f"无法解析集数 '{episode_num_str}'，已跳过。")
    new_episodes.sort(key=lambda x: x[0])
    return new_episodes

# --- 4. 主工作循环 ---
def run_update_checker():
    """后台运行的更新检查器，采用“添加并验证”模式"""
    logging.info("更新检查器线程已启动...")
    while True:
        logging.info("-" * 30)
        state = load_state()
        last_completed_episode = state.get("last_completed_episode", float(START_EPISODE))
        pending_tasks = state.get("pending_tasks", [])
        
        logging.info(f"当前最后确认完成的集数是: {last_completed_episode}")
        logging.info(f"有 {len(pending_tasks)} 个任务待处理。")

        token = get_alist_token()
        if not token:
            logging.warning("无法获取 Alist token，将在下次检查时重试。")
            time.sleep(UPDATE_INTERVAL_SECONDS)
            continue

        # --- 阶段一：检查并添加新剧集 ---
        logging.info("\n--- 阶段一：检查并添加新剧集 ---")
        new_episodes = find_new_episodes(last_completed_episode)
        if not new_episodes:
            logging.info("未发现需要下载的新剧集。")
        else:
            logging.info(f"发现 {len(new_episodes)} 个新剧集，准备添加到 Alist...")
            for episode_num, episode_info in new_episodes:
                if any(p.get('episode_number') == episode_num for p in pending_tasks):
                    logging.info(f"剧集 {episode_num} 已在待处理列表中，跳过添加。")
                    continue
                
                downloads = episode_info[7].get("WEBRIP", [])
                magnet_found = False
                for item in downloads:
                    if len(item) > 2 and item[1] == "简繁日MKV":
                        magnet = item[2] + "".join(TRACKERS_TO_ADD)
                        desired_filename = f"{episode_info[0]} {episode_info[1]}.mkv"
                        task_id = add_offline_download(token, magnet)
                        if task_id:
                            pending_tasks.append({
                                "task_id": task_id,
                                "episode_number": episode_num,
                                "desired_filename": desired_filename,
                                "rename_attempts": 0  # 初始化重试计数
                            })
                            save_state({**state, "pending_tasks": pending_tasks})
                            logging.info(f"剧集 {episode_num} (任务ID: {task_id}) 已添加到待处理列表。")
                            magnet_found = True
                            break
                if not magnet_found:
                    logging.warning(f"在剧集 {episode_num} 中未找到 '简繁日MKV' 格式的下载链接。")
        
        # --- 阶段二：检查待处理任务的状态 ---
        logging.info("\n--- 阶段二：检查待处理任务的状态 ---")
        if not pending_tasks:
            logging.info("没有待处理的任务需要检查。")
        else:
            alist_tasks = get_offline_download_tasks(token)
            if alist_tasks is None:
                logging.warning("无法获取 Alist 任务列表，将在下次检查时重试。")
            else:
                tasks_to_remove = []
                state_changed = False
                alist_tasks_map = {task.get('id'): task for task in alist_tasks}

                for task in pending_tasks:
                    alist_task = alist_tasks_map.get(task["task_id"])
                    if not alist_task: continue

                    if alist_task.get("status") == "done":
                        desired_filename = task.get("desired_filename")
                        episode_num_str = str(task['episode_number']).split('.')[0]
                        logging.info(f"任务 {task['task_id']} (剧集 {episode_num_str}) 已完成。开始在 '{DOWNLOAD_PATH}' 中扫描下载结果...")

                        content_in_download_path = list_files(token, DOWNLOAD_PATH)
                        target_file, source_dir, found = None, "", False
                        
                        if content_in_download_path is not None:
                            match_pattern = f"[SBSUB][CONAN][{episode_num_str}]"
                            for item in content_in_download_path:
                                name = item.get("name", "")
                                if item.get("is_dir") and episode_num_str in name:
                                    sub_dir_path = os.path.join(DOWNLOAD_PATH, name)
                                    files_in_subdir = list_files(token, sub_dir_path)
                                    if files_in_subdir:
                                        for sub_file in files_in_subdir:
                                            sub_name = sub_file.get("name", "")
                                            if not sub_file.get("is_dir") and sub_name.startswith(match_pattern) and sub_name.endswith(".mkv"):
                                                target_file, source_dir, found = sub_name, sub_dir_path, True
                                                logging.info(f"在子目录 '{name}' 中找到目标文件: '{target_file}'")
                                                break
                                elif not item.get("is_dir") and name.startswith(match_pattern) and name.endswith(".mkv"):
                                    target_file, source_dir, found = name, DOWNLOAD_PATH, True
                                    logging.info(f"在主下载目录中找到目标文件: '{target_file}'")
                                if found: break

                        rename_success = False
                        if found and target_file:
                            if target_file != desired_filename:
                                rename_success = rename_file(token, source_dir, target_file, desired_filename)
                            else:
                                logging.info("文件名已符合要求，无需重命名。")
                                rename_success = True
                        
                        if rename_success:
                            tasks_to_remove.append(task)
                        else:
                            task['rename_attempts'] = task.get('rename_attempts', 0) + 1
                            if not found:
                                logging.error(f"扫描完 '{DOWNLOAD_PATH}' 后未能找到匹配文件。当前尝试次数: {task['rename_attempts']}/{MAX_RENAME_ATTEMPTS}。")
                            else:
                                logging.error(f"重命名剧集 {episode_num_str} 失败。当前尝试次数: {task['rename_attempts']}/{MAX_RENAME_ATTEMPTS}。")
                            
                            if task['rename_attempts'] >= MAX_RENAME_ATTEMPTS:
                                logging.critical(f"剧集 {episode_num_str} 已达到最大重试次数，将放弃该任务！")
                                tasks_to_remove.append(task)
                        state_changed = True

                    elif alist_task.get("status") == "error":
                        logging.error(f"剧集 {task['episode_number']} (任务ID: {task['task_id']}) 下载失败。错误: {alist_task.get('error')}")
                        tasks_to_remove.append(task)
                        state_changed = True
                    else:
                        logging.info(f"状态：剧集 {task['episode_number']} 仍在下载中 (状态: {alist_task.get('status')})。")

                if state_changed:
                    state["pending_tasks"] = [p for p in pending_tasks if p not in tasks_to_remove]
                    completed_episodes = {state["last_completed_episode"]}
                    for removed_task in tasks_to_remove:
                        alist_task = alist_tasks_map.get(removed_task["task_id"])
                        # 仅当任务成功（或被放弃）时，才将其视为可更新last_completed_episode的候选
                        if (alist_task and alist_task.get("status") == "done") or removed_task.get('rename_attempts', 0) >= MAX_RENAME_ATTEMPTS:
                             completed_episodes.add(removed_task["episode_number"])
                    
                    if completed_episodes:
                        new_last_completed = max(completed_episodes)
                        if new_last_completed > state["last_completed_episode"]:
                            state["last_completed_episode"] = new_last_completed
                            logging.info(f"更新最后完成的集数为: {new_last_completed}")
                    
                    save_state(state)
                    logging.info("状态文件已更新。")

        logging.info(f"\n所有检查完成。等待 {UPDATE_INTERVAL_SECONDS} 秒后进行下一次检查...")
        time.sleep(UPDATE_INTERVAL_SECONDS)

# --- 5. Web 服务器 ---
@app.route('/')
def status_page():
    """显示当前状态的 Web 页面"""
    state = load_state()
    return render_template('index.html', 
                           last_episode=state.get('last_completed_episode', 'N/A'), 
                           pending_tasks=state.get('pending_tasks', []))

# --- 6. 启动入口 ---
if __name__ == "__main__":
    check_env_vars()
    logging.info("脚本启动...")
    
    # 在后台线程中运行更新检查器
    checker_thread = threading.Thread(target=run_update_checker, daemon=True)
    checker_thread.start()
    
    # 启动 Flask web 服务器 (需要一个 templates/index.html 文件)
    # 请确保您有一个名为 "templates" 的文件夹，并且其中包含一个 "index.html" 文件。
    # 一个简单的 index.html 示例:
    # <!DOCTYPE html>
    # <html>
    # <head><title>下载器状态</title></head>
    # <body>
    #   <h1>下载器状态</h1>
    #   <p>最新完成的剧集: {{ last_episode }}</p>
    #   <h2>待处理任务:</h2>
    #   <ul>
    #     {% for task in pending_tasks %}
    #       <li>剧集 {{ task.episode_number }} (任务ID: {{ task.task_id }}) - 重命名尝试: {{ task.rename_attempts }}</li>
    #     {% else %}
    #       <li>无待处理任务</li>
    #     {% endfor %}
    #   </ul>
    # </body>
    # </html>
    app.run(host='0.0.0.0', port=5000)
