import os
import requests
import json
import sys
import time
import threading
import logging
from flask import Flask, render_template

# 配置日志
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
        logging.error("环境变量 ALIST_URL, ALIST_USERNAME, ALIST_PASSWORD, DOWNLOAD_PATH, STATE_FILE_PATH 必须全部设置。")
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
        logging.error(f"获取 Alist token 失败: {e}")
        return None

def add_offline_download(token, magnet_link):
    """通过 Alist API 添加离线下载任务并返回任务 ID"""
    download_url = f"{ALIST_URL}/api/fs/add_offline_download"
    headers = {"Authorization": token}
    payload = {
        "path": DOWNLOAD_PATH,
        "urls": [magnet_link],
        "tool": ALIST_TOOL,
        "delete_policy": ALIST_DELETE_POLICY
    }
    try:
        response = requests.post(download_url, headers=headers, json=payload)
        response.raise_for_status()
        response_data = response.json()
        
        # 根据新的 API 格式提取任务 ID
        task_id = response_data.get("data", {}).get("tasks", [{}])[0].get("id")
        
        if task_id:
            logging.info(f"成功将任务添加到 Alist 目录 '{DOWNLOAD_PATH}'，任务ID: {task_id}")
            return task_id
        else:
            logging.warning(f"添加下载任务成功，但响应中未找到任务 ID。")
            logging.warning(f"服务器响应: {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        logging.error(f"添加 Alist 离线下载失败: {e}")
        if 'response' in locals() and response.text:
            logging.error(f"服务器响应: {response.text}")
        return None

def get_offline_download_tasks(token):
    """获取 Alist 中的所有离线下载任务"""
    tasks_url = f"{ALIST_URL}/api/fs/offline_download_tasks"
    headers = {"Authorization": token}
    try:
        response = requests.get(tasks_url, headers=headers)
        response.raise_for_status()
        return response.json().get("data", [])
    except requests.exceptions.RequestException as e:
        logging.error(f"获取 Alist 离线任务列表失败: {e}")
        return None
    except json.JSONDecodeError:
        logging.error("解析 Alist 任务列表失败。")
        return None

def rename_file(token, original_name, new_name):
    """通过 Alist API 重命名文件"""
    rename_url = f"{ALIST_URL}/api/fs/rename"
    headers = {"Authorization": token}
    payload = {
        "src_dir": DOWNLOAD_PATH,
        "name": original_name,
        "new_name": new_name
    }
    try:
        response = requests.post(rename_url, headers=headers, json=payload)
        response.raise_for_status()
        if response.json().get("code") == 200:
            logging.info(f"成功将 '{original_name}' 重命名为 '{new_name}'")
            return True
        else:
            logging.error(f"重命名文件 '{original_name}' 失败。")
            logging.error(f"服务器响应: {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        logging.error(f"重命名文件 API 请求失败: {e}")
        if 'response' in locals() and response.text:
            logging.error(f"服务器响应: {response.text}")
        return False

def load_state():
    """从 state.json 加载状态，如果不存在则创建"""
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, 'r') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logging.warning(f"读取或解析状态文件失败: {e}。将使用默认状态。")
    
    # 默认状态
    return {
        "last_completed_episode": float(START_EPISODE),
        "pending_tasks": []
    }

def save_state(state):
    """将状态保存到 state.json"""
    try:
        os.makedirs(os.path.dirname(STATE_FILE_PATH), exist_ok=True)
        with open(STATE_FILE_PATH, 'w') as f:
            json.dump(state, f, indent=2)
    except IOError as e:
        logging.error(f"无法写入状态文件 '{STATE_FILE_PATH}': {e}")
        sys.exit(1)

def find_new_episodes(last_completed_episode):
    """获取数据并找到所有比记录新的剧集"""
    logging.info("正在从数据源获取最新剧集列表...")
    try:
        response = requests.get(DATA_URL)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"获取数据失败: {e}")
        return []
    except json.JSONDecodeError:
        logging.error("解析 JSON 数据失败。")
        return []

    tv_shows = data.get("res", [])[0][4]
    new_episodes = []

    for episode_num_str, value in tv_shows.items():
        title = value[1]
        if "（本集未被日本官网计入总集数）" in title:
            continue

        try:
            current_episode_num = float(episode_num_str)
            if current_episode_num > last_completed_episode:
                new_episodes.append((current_episode_num, value))
        except ValueError:
            logging.warning(f"无法解析集数 '{episode_num_str}'，已跳过。")
            continue
    
    # 按集数从小到大排序
    new_episodes.sort(key=lambda x: x[0])
    return new_episodes

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
                # 检查任务是否已在 pending_tasks 中
                if any(p.get('episode_number') == episode_num for p in pending_tasks):
                    logging.info(f"剧集 {episode_num} 已在待处理列表中，跳过添加。")
                    continue

                downloads = episode_info[7]
                webrip_downloads = downloads.get("WEBRIP", [])
                magnet_found = False
                for item in webrip_downloads:
                    if len(item) > 2 and item[1] == "简繁日MKV":
                        magnet = item[2] + "".join(TRACKERS_TO_ADD)
                        official_episode_num = episode_info[0]
                        chinese_title = episode_info[1]  # 使用中文标题
                        desired_filename = f"{official_episode_num} {chinese_title}.mkv"
                        
                        logging.info(f"处理新剧集 {episode_num}，期望文件名: {desired_filename}")
                        task_id = add_offline_download(token, magnet)
                        
                        if task_id:
                            pending_tasks.append({
                                "task_id": task_id,
                                "episode_number": episode_num,
                                "desired_filename": desired_filename
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
                
                # 创建一个任务ID到任务对象的映射以便快速查找
                alist_tasks_map = {task.get('id'): task for task in alist_tasks}

                for task in pending_tasks:
                    alist_task = alist_tasks_map.get(task["task_id"])
                    
                    if not alist_task:
                        # 对于 V2 添加的任务，我们无法验证，只能假设成功
                        if task["task_id"] == "unknown_v2_task":
                            logging.warning(f"无法验证来自 Alist V2 的任务 (剧集 {task['episode_number']})，假设其已完成。")
                            tasks_to_remove.append(task)
                            state_changed = True
                        else:
                            logging.warning(f"在 Alist 任务列表中未找到任务ID '{task['task_id']}' (剧集 {task['episode_number']})。可能已被手动删除。")
                        continue

                    status = alist_task.get("status")
                    if status == "done":
                        original_filename = alist_task.get("name")
                        desired_filename = task.get("desired_filename")
                        
                        logging.info(f"确认：剧集 {task['episode_number']} (任务ID: {task['task_id']}) 已下载完成。原始文件名: '{original_filename}'")

                        # 执行重命名
                        if original_filename and desired_filename and original_filename != desired_filename:
                            logging.info(f"准备将 '{original_filename}' 重命名为 '{desired_filename}'...")
                            rename_success = rename_file(token, original_filename, desired_filename)
                            if rename_success:
                                # 只有重命名成功后才移除任务
                                tasks_to_remove.append(task)
                                state_changed = True
                            else:
                                logging.error(f"重命名失败，任务 {task['task_id']} 将在下次检查时重试。")
                        else:
                            # 如果不需要重命名，也视为成功
                            tasks_to_remove.append(task)
                            state_changed = True

                    elif status == "error":
                        logging.error(f"剧集 {task['episode_number']} (任务ID: {task['task_id']}) 下载失败。错误信息: {alist_task.get('error')}")
                        tasks_to_remove.append(task)
                        state_changed = True
                    else:
                        logging.info(f"状态：剧集 {task['episode_number']} 仍在下载中 (状态: {status})。")

                if state_changed:
                    # 更新 pending_tasks 列表
                    state["pending_tasks"] = [p for p in pending_tasks if p not in tasks_to_remove]
                    
                    # 获取所有已完成的剧集号（包括新完成的和之前已完成的）
                    completed_episodes = {state["last_completed_episode"]}
                    for removed_task in tasks_to_remove:
                        alist_task = alist_tasks_map.get(removed_task["task_id"])
                        # 只有状态为 'done' 或 V2 的未知任务才算完成
                        if (alist_task and alist_task.get("status") == "done") or removed_task["task_id"] == "unknown_v2_task":
                             completed_episodes.add(removed_task["episode_number"])

                    # 更新 last_completed_episode
                    if completed_episodes:
                        new_last_completed = max(completed_episodes)
                        if new_last_completed > state["last_completed_episode"]:
                            state["last_completed_episode"] = new_last_completed
                            logging.info(f"更新最后完成的集数为: {new_last_completed}")

                    save_state(state)
                    logging.info("状态文件已更新。")

        logging.info(f"\n所有检查完成。等待 {UPDATE_INTERVAL_SECONDS} 秒后进行下一次检查...")
        time.sleep(UPDATE_INTERVAL_SECONDS)

@app.route('/')
def status_page():
    """显示当前状态的 Web 页面"""
    state = load_state()
    last_completed_episode = state.get('last_completed_episode', 'N/A')
    pending_tasks = state.get('pending_tasks', [])
    return render_template('index.html', last_episode=last_completed_episode, pending_tasks=pending_tasks)

if __name__ == "__main__":
    check_env_vars()
    logging.info("脚本启动...")
    
    # 在后台线程中运行更新检查器
    checker_thread = threading.Thread(target=run_update_checker, daemon=True)
    checker_thread.start()
    
    # 启动 Flask web 服务器
    app.run(host='0.0.0.0', port=5000)