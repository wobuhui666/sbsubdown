import os
import requests
import json
import sys
import time
import threading
import logging
import functools
from flask import Flask, render_template

# --- 1. 配置模块 (已更新) ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
ALIST_URL = os.getenv("ALIST_URL")
ALIST_USERNAME = os.getenv("ALIST_USERNAME")
ALIST_PASSWORD = os.getenv("ALIST_PASSWORD")
DOWNLOAD_PATH = os.getenv("DOWNLOAD_PATH")
# 新增：必须设置你的 Alist 存储的根挂载路径，例如 "/al" 或 "/media"
ALIST_MOUNT_PATH = os.getenv("ALIST_MOUNT_PATH")
STATE_FILE_PATH = os.getenv("STATE_FILE_PATH", "/data/state.json")
START_EPISODE = int(os.getenv("START_EPISODE", 0))
IDLE_CHECK_INTERVAL_SECONDS = int(os.getenv("IDLE_CHECK_INTERVAL_SECONDS", 3600))
ACTIVE_POLLING_INTERVAL_SECONDS = int(os.getenv("ACTIVE_POLLING_INTERVAL_SECONDS", 600))
ALIST_TOOL = os.getenv("ALIST_TOOL", "aria2")
ALIST_DELETE_POLICY = os.getenv("ALIST_DELETE_POLICY", "delete_on_upload_succeed")
MAX_RENAME_ATTEMPTS = 5
DATA_URL = "https://cloud.sbsub.com/data/data.json"
TRACKERS_TO_ADD = ("&tr=http://open.acgtracker.com:1096/announce" "&tr=http://tracker.cyber-gateway.net:6969/announce" "&tr=http://tracker.acgnx.se/announce" "&tr=http://share.camoe.cn:8080/announce" "&tr=http://t.acg.rip:6699/announce" "&tr=https://tr.bangumi.moe:9696/announce" "&tr=https://tracker.forever-legend.net:443/announce" "&tr=https://tracker.gbitt.info:443/announce" "&tr=https://tracker.lilithraws.org:443/announce" "&tr=https://tracker.moe.pm:443/announce")
app = Flask(__name__)

# --- 2. 通用重试装饰器 (无变化) ---
def retry_on_failure(retries=3, delay=5, allowed_exceptions=(requests.exceptions.RequestException,)):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(1, retries + 1):
                try: return func(*args, **kwargs)
                except allowed_exceptions as e:
                    if i == retries: logging.error(f"函数 '{func.__name__}' 在 {retries} 次尝试后最终失败。错误: {e}"); return None
                    logging.warning(f"函数 '{func.__name__}' 失败 (第 {i}/{retries} 次尝试)。将在 {delay} 秒后重试... 错误: {e}"); time.sleep(delay)
        return wrapper
    return decorator

# --- 3. 核心功能函数 (多处已更新) ---
def check_env_vars():
    # 更新：增加了对 ALIST_MOUNT_PATH 的检查
    if not all([ALIST_URL, ALIST_USERNAME, ALIST_PASSWORD, DOWNLOAD_PATH, STATE_FILE_PATH, ALIST_MOUNT_PATH]):
        logging.error("环境变量 ALIST_URL, ALIST_USERNAME, ALIST_PASSWORD, DOWNLOAD_PATH, STATE_FILE_PATH, ALIST_MOUNT_PATH 必须全部设置。"); sys.exit(1)

@retry_on_failure(retries=3, delay=3)
def _get_task_list_from_v4_api(token, endpoint_url):
    headers = {"Authorization": token}
    try:
        response = requests.get(endpoint_url, headers=headers, timeout=10)
        response.raise_for_status()
        if not response.text: logging.error(f"从 {endpoint_url} 收到的响应为空。"); return []
        return response.json().get("data", [])
    except json.JSONDecodeError:
        logging.error(f"解析 Alist 任务列表失败！URL: {endpoint_url}"); logging.error(f"服务器状态码: {response.status_code}"); logging.error(f"服务器原始响应 (前500字符): {response.text[:500]}"); raise

def get_completed_transfer_tasks(token):
    logging.info("正在检查已完成的转存任务列表...")
    transfer_done_url = f"{ALIST_URL}/api/task/offline_download_transfer/done"
    tasks = _get_task_list_from_v4_api(token, transfer_done_url)
    if tasks is not None:
        logging.info(f"成功获取到 {len(tasks)} 个已完成的转存任务。")
    return tasks

@retry_on_failure(retries=3, delay=5)
def get_alist_token():
    login_url = f"{ALIST_URL}/api/auth/login"; payload = {"username": ALIST_USERNAME, "password": ALIST_PASSWORD}
    try: response = requests.post(login_url, json=payload, timeout=10); response.raise_for_status(); return response.json()["data"]["token"]
    except json.JSONDecodeError: logging.error(f"解析 Alist token 失败！"); logging.error(f"服务器状态码: {response.status_code}"); logging.error(f"服务器原始响应 (前500字符): {response.text[:500]}"); raise

@retry_on_failure(retries=3, delay=3)
def add_offline_download(token, magnet_link):
    download_url = f"{ALIST_URL}/api/fs/add_offline_download"; headers = {"Authorization": token}; payload = {"path": DOWNLOAD_PATH, "urls": [magnet_link], "tool": ALIST_TOOL, "delete_policy": ALIST_DELETE_POLICY}
    try:
        response = requests.post(download_url, headers=headers, json=payload, timeout=10); response.raise_for_status(); response_data = response.json(); task_id = response_data.get("data", {}).get("tasks", [{}])[0].get("id")
        if task_id: logging.info(f"成功将任务添加到 Alist 目录 '{DOWNLOAD_PATH}'，任务ID: {task_id}"); return task_id
        logging.warning(f"添加下载任务成功，但响应中未找到任务 ID。响应: {response.text}"); return None
    except json.JSONDecodeError: logging.error(f"解析 Alist 添加任务响应失败！"); logging.error(f"服务器状态码: {response.status_code}"); logging.error(f"服务器原始响应 (前500字符): {response.text[:500]}"); raise

@retry_on_failure(retries=3, delay=2)
def list_files(token, path):
    list_url = f"{ALIST_URL}/api/fs/list"; headers = {"Authorization": token}; payload = {"path": path, "page": 1, "per_page": 0}
    try:
        response = requests.post(list_url, headers=headers, json=payload, timeout=15); response.raise_for_status()
        if not response.text: logging.error(f"从 {list_url} (路径: {path}) 收到的响应为空。"); return None
        response_data = response.json()
        if response_data.get("code") == 200: return response_data.get("data", {}).get("content", [])
        logging.error(f"列出目录 '{path}' 文件失败。服务器响应: {response.text}"); return None
    except json.JSONDecodeError: logging.error(f"解析 Alist 目录列表失败！"); logging.error(f"服务器状态码: {response.status_code}"); logging.error(f"服务器原始响应 (前500字符): {response.text[:500]}"); raise

# 更新：最关键的修复，重写 rename_file 函数
@retry_on_failure(retries=3, delay=2)
def rename_file(token, src_directory, original_name, new_name):
    """
    增强版重命名函数：自动规范化源目录路径，确保其包含挂载点。
    """
    # --- 新增的路径规范化逻辑 ---
    normalized_src_dir = src_directory
    # 确保 ALIST_MOUNT_PATH 结尾没有斜杠，以避免拼接时产生双斜杠
    mount_path_stripped = ALIST_MOUNT_PATH.rstrip('/')
    
    # 检查传入的目录是否已经以正确的挂载路径开头
    if not src_directory.startswith(mount_path_stripped + '/') and src_directory != mount_path_stripped:
        # 如果不是，则强制将它们拼接起来
        src_dir_stripped = src_directory.lstrip('/')
        normalized_src_dir = f"{mount_path_stripped}/{src_dir_stripped}"
        logging.warning(f"检测到原始 src_dir '{src_directory}' 不规范，已自动修正为: '{normalized_src_dir}'")
    # --- 逻辑结束 ---

    rename_url = f"{ALIST_URL}/api/fs/rename"
    headers = {"Authorization": token}
    # 使用修正后的路径
    payload = {"src_dir": normalized_src_dir, "name": original_name, "new_name": new_name}
    
    try:
        # 增加日志，打印出最终发送给 Alist 的载荷
        logging.info(f"正在发送重命名请求到 Alist，载荷: {json.dumps(payload)}")
        
        response = requests.post(rename_url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        response_data = response.json() # 提前解析json
        if response_data.get("code") == 200:
            logging.info(f"成功将 '{original_name}' 重命名为 '{new_name}'")
            return True
        # 如果code不是200，也记录详细信息
        logging.error(f"重命名文件 '{original_name}' 失败。服务器响应: {response.text}")
        return False
    except json.JSONDecodeError:
        logging.error(f"解析 Alist 重命名响应失败！")
        logging.error(f"服务器状态码: {response.status_code}")
        logging.error(f"服务器原始响应 (前500字符): {response.text[:500]}")
        raise

def load_state():
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, 'r') as f: return json.load(f)
        except (IOError, json.JSONDecodeError) as e: logging.warning(f"读取或解析状态文件失败: {e}。将使用默认状态。")
    return {"last_completed_episode": float(START_EPISODE), "pending_tasks": []}

# 更新：修复了 IndentationError
def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE_PATH), exist_ok=True)
        with open(STATE_FILE_PATH, 'w') as f:
            json.dump(state, f, indent=2)
    except IOError as e:
        logging.error(f"无法写入状态文件 '{STATE_FILE_PATH}': {e}")
        sys.exit(1)

@retry_on_failure(retries=3, delay=10)
def fetch_data_from_source(url):
    try: response = requests.get(url, timeout=15); response.raise_for_status(); return response.json()
    except json.JSONDecodeError: logging.error(f"解析数据源 {url} 失败！"); logging.error(f"服务器状态码: {response.status_code}"); logging.error(f"服务器原始响应 (前500字符): {response.text[:500]}"); raise

def find_new_episodes(last_completed_episode):
    logging.info("正在从数据源获取最新剧集列表..."); data = fetch_data_from_source(DATA_URL)
    if not data: logging.error("获取数据失败，已跳过本次剧集检查。"); return []
    tv_shows = data.get("res", [])[0][4]; new_episodes = []
    for episode_num_str, value in tv_shows.items():
        if "（本集未被日本官网计入总集数）" in value[1]: continue
        try:
            if float(episode_num_str) > last_completed_episode: new_episodes.append((float(episode_num_str), value))
        except ValueError: logging.warning(f"无法解析集数 '{episode_num_str}'，已跳过。")
    new_episodes.sort(key=lambda x: x[0]); return new_episodes

# --- 4. 主工作循环 (逻辑已重构, 日志已增强) ---
def run_update_checker():
    logging.info("更新检查器线程已启动...")
    while True:
        logging.info("-" * 30); state = load_state()
        last_completed_episode = state.get("last_completed_episode", float(START_EPISODE))
        pending_tasks = state.get("pending_tasks", [])
        logging.info(f"当前最后确认完成的集数是: {last_completed_episode}"); logging.info(f"有 {len(pending_tasks)} 个任务待处理。")
        token = get_alist_token()
        if not token:
            logging.warning("无法获取 Alist token，将在下次检查时重试。"); time.sleep(ACTIVE_POLLING_INTERVAL_SECONDS); continue
        
        logging.info("\n--- 阶段一：检查并添加新剧集 ---")
        new_episodes = find_new_episodes(last_completed_episode)
        if not new_episodes: logging.info("未发现需要下载的新剧集。")
        else:
            logging.info(f"发现 {len(new_episodes)} 个新剧集，准备添加到 Alist...")
            for episode_num, episode_info in new_episodes:
                if any(p.get('episode_number') == episode_num for p in pending_tasks): logging.info(f"剧集 {episode_num} 已在待处理列表中，跳过添加。"); continue
                downloads = episode_info[7].get("WEBRIP", []); magnet_found = False
                for item in downloads:
                    if len(item) > 2 and item[1] == "简繁日MKV":
                        magnet = item[2] + "".join(TRACKERS_TO_ADD); desired_filename = f"{episode_info[0]} {episode_info[1]}.mkv"; task_id = add_offline_download(token, magnet)
                        if task_id:
                            pending_tasks.append({"task_id": task_id, "episode_number": episode_num, "desired_filename": desired_filename, "rename_attempts": 0})
                            save_state({**state, "pending_tasks": pending_tasks}); logging.info(f"剧集 {episode_num} (任务ID: {task_id}) 已添加到待处理列表。"); magnet_found = True; break
                if not magnet_found: logging.warning(f"在剧集 {episode_num} 中未找到 '简繁日MKV' 格式的下载链接。")
        
        logging.info("\n--- 阶段二：根据转存结果检查任务状态 ---")
        if not pending_tasks:
            logging.info("没有待处理的任务需要检查。")
        else:
            completed_transfers = get_completed_transfer_tasks(token)
            if completed_transfers is None:
                logging.warning("无法获取 Alist 已完成转存任务列表，将在下次检查时重试。")
            else:
                if completed_transfers:
                    logging.info(f"从 Alist 获取到 {len(completed_transfers)} 个已完成的转存任务名称如下:")
                    for i, transfer in enumerate(completed_transfers):
                        logging.info(f"  {i+1}: {transfer.get('name', 'N/A')}")

                tasks_to_remove, state_changed = [], False
                for task in pending_tasks:
                    episode_num_str = str(task['episode_number']).split('.')[0]
                    match_pattern = f"[SBSUB][CONAN][{episode_num_str}]"
                    logging.info(f"正在为待处理任务 (剧集 {episode_num_str}) 寻找匹配项，搜索模式为: '{match_pattern}'")

                    is_completed = False
                    matched_transfer_name = ""
                    for transfer in completed_transfers:
                        transfer_name = transfer.get('name', '')
                        # 更新：修复匹配逻辑
                        if match_pattern in transfer_name and '.mkv' in transfer_name:
                            is_completed = True
                            matched_transfer_name = transfer_name
                            break
                    
                    if is_completed:
                        logging.info(f"匹配成功！剧集 {episode_num_str} 的转存任务已完成。匹配到的文件名: '{matched_transfer_name}'")
                        logging.info("开始扫描文件系统并准备重命名...")
                        content_in_download_path = list_files(token, DOWNLOAD_PATH)
                        target_file, source_dir, found = None, "", False
                        if content_in_download_path is not None:
                            for item in content_in_download_path:
                                name = item.get("name", "");
                                if item.get("is_dir") and episode_num_str in name:
                                    sub_dir_path = os.path.join(DOWNLOAD_PATH, name)
                                    files_in_subdir = list_files(token, sub_dir_path)
                                    if files_in_subdir:
                                        for sub_file in files_in_subdir:
                                            sub_name = sub_file.get("name", "")
                                            if not sub_file.get("is_dir") and sub_name.startswith(match_pattern) and sub_name.endswith(".mkv"):
                                                target_file, source_dir, found = sub_name, sub_dir_path, True; logging.info(f"在子目录 '{name}' 中找到目标文件: '{target_file}'"); break
                                elif not item.get("is_dir") and name.startswith(match_pattern) and name.endswith(".mkv"):
                                    target_file, source_dir, found = name, DOWNLOAD_PATH, True; logging.info(f"在主下载目录中找到目标文件: '{target_file}'")
                                if found: break
                        
                        rename_success = False
                        if found and target_file:
                            desired_filename = task.get("desired_filename")
                            if target_file != desired_filename: rename_success = rename_file(token, source_dir, target_file, desired_filename)
                            else: logging.info("文件名已符合要求，无需重命名。"); rename_success = True
                        
                        if rename_success: tasks_to_remove.append(task)
                        else:
                            task['rename_attempts'] = task.get('rename_attempts', 0) + 1
                            if not found: logging.error(f"扫描完 '{DOWNLOAD_PATH}' 后未能找到匹配文件。当前尝试次数: {task['rename_attempts']}/{MAX_RENAME_ATTEMPTS}。")
                            else: logging.error(f"重命名剧集 {episode_num_str} 失败。当前尝试次数: {task['rename_attempts']}/{MAX_RENAME_ATTEMPTS}。")
                            if task['rename_attempts'] >= MAX_RENAME_ATTEMPTS:
                                logging.critical(f"剧集 {episode_num_str} 已达到最大重试次数，将放弃该任务！"); tasks_to_remove.append(task)
                        state_changed = True
                    else:
                        logging.info(f"检查了所有已完成的转存任务后，未能找到剧集 {episode_num_str} 的匹配项。该任务的转存尚未完成或文件名不匹配。")

                if state_changed:
                    state["pending_tasks"] = [p for p in pending_tasks if p not in tasks_to_remove]
                    completed_episodes = {state.get("last_completed_episode", float(START_EPISODE))}
                    for removed_task in tasks_to_remove:
                        completed_episodes.add(removed_task["episode_number"])
                    if completed_episodes:
                        new_last_completed = max(completed_episodes)
                        if new_last_completed > state["last_completed_episode"]:
                            state["last_completed_episode"] = new_last_completed; logging.info(f"更新最后完成的集数为: {new_last_completed}")
                    save_state(state); logging.info("状态文件已更新。")

        final_state = load_state()
        if final_state.get("pending_tasks"):
            wait_interval = ACTIVE_POLLING_INTERVAL_SECONDS
            logging.info(f"\n发现待处理任务。将在较短的 {wait_interval} 秒后进行下一次检查...")
        else:
            wait_interval = IDLE_CHECK_INTERVAL_SECONDS
            logging.info(f"\n所有任务已完成。将在常规的 {wait_interval} 秒后进行下一次检查...")
        time.sleep(wait_interval)

# --- 5. Web 服务器 (无变化) ---
@app.route('/')
def status_page():
    state = load_state()
    return render_template('index.html', last_episode=state.get('last_completed_episode', 'N/A'), pending_tasks=state.get('pending_tasks', []))

# --- 6. 启动入口 (无变化) ---
if __name__ == "__main__":
    check_env_vars(); logging.info("脚本启动...")
    if not os.path.exists("templates"): os.makedirs("templates")
    index_html_path = os.path.join("templates", "index.html")
    if not os.path.exists(index_html_path):
        with open(index_html_path, "w", encoding="utf-8") as f:
            f.write("""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>下载器状态</title><style>body { font-family: sans-serif; line-height: 1.6; margin: 2em; } h1, h2 { color: #333; } ul { list-style-type: none; padding-left: 0; } li { background: #f4f4f4; margin-bottom: 5px; padding: 10px; border-radius: 5px; }</style></head><body><h1>Alist 自动下载器状态</h1><p><strong>最新完成的剧集:</strong> {{ last_episode }}</p><h2>待处理任务 ({{ pending_tasks|length }})</h2><ul>{% for task in pending_tasks %}<li>剧集 {{ task.episode_number }} (任务ID: {{ task.task_id }}) - 重命名尝试: {{ task.rename_attempts }} / """ + str(MAX_RENAME_ATTEMPTS) + """</li>{% else %}<li>无待处理任务</li>{% endfor %}</ul></body></html>""")
    
    checker_thread = threading.Thread(target=run_update_checker, daemon=True)
    checker_thread.start()
    app.run(host='0.0.0.0', port=5000)