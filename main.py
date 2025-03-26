import asyncio
import feedparser
import csv
import re
import aiohttp
import os
from pikpakapi import PikPakApi
from info import EMAIL, PW, PUSHPLUS_TOKEN
from torrentool.api import Torrent
import requests


def is_github_actions():
    """Check if running in GitHub Actions environment"""
    return os.environ.get('GITHUB_ACTIONS') == 'true'


def echo_github_output(message, level="info"):
    """Format message for GitHub Actions output"""
    if is_github_actions():
        prefix = "::{}::".format(level)
        print(f"{prefix}{message}")
    else:
        print(message)


async def notify(title, content):
    """Send notification via Pushplus service"""
    if not PUSHPLUS_TOKEN:
        return
    
    url = f'http://www.pushplus.plus/send?token={PUSHPLUS_TOKEN}&title={title}&content={content}'
    try:
        response = requests.get(url)
        if response.status_code == 200:
            print(f"通知已发送: {title}")
        else:
            print(f"通知发送失败: {response.status_code}")
    except Exception as e:
        print(f"通知发送异常: {e}")


async def main():
    client = PikPakApi(username=EMAIL, password=PW)
    await client.login()
    await client.refresh_access_token()
    
    added_files = []  # Track newly added files for notification

    async def get_or_create_folder(parent_id, folder_name):
        folder_name = sanitize_folder_name(folder_name)
        if parent_id == "root":
            parent_id = None
        files = await client.file_list(parent_id=parent_id)
        for f in files.get("files", []):
            if f["name"] == folder_name and f["kind"] == "drive#folder":
                return f["id"]
        new_folder = await client.create_folder(name=folder_name, parent_id=parent_id)
        return new_folder["file"]["id"]

    def sanitize_folder_name(name):
        return re.sub(r'[\\/:*?"<>|]', '_', name).strip()

    async def fetch_torrent(bt_url):
        async with aiohttp.ClientSession() as session:
            async with session.get(bt_url) as response:
                response.raise_for_status()
                return await response.read()

    csv_file = "data.csv"
    with open(csv_file, newline='', encoding='utf-8') as file:
        reader = csv.reader(file)
        rss_data = [(row[0], row[1]) for row in reader if len(row) >= 2]

    async def process_rss(title, rss_url):
        print(f"\n处理 RSS: {title}")
        feed = feedparser.parse(rss_url)
        anime_root_id = await get_or_create_folder("root", "Anime")
        target_folder_id = await get_or_create_folder(anime_root_id, title)

        async def process_entry(entry):
            bt_url = None
            for enc in entry.get('enclosures', []):
                if enc.get('type') == 'application/x-bittorrent':
                    bt_url = enc.href
                    break
            if not bt_url:
                for link in entry.get('links', []):
                    if link.get('type') == 'application/x-bittorrent':
                        bt_url = link.href
                        break
            if not bt_url:
                return

            try:
                torrent_data = await fetch_torrent(bt_url)
                torrent = Torrent.from_string(torrent_data)
                file_name = torrent.name
            except Exception as e:
                print(f"解析种子失败: {e}")
                return

            try:
                files_in_folder = await client.file_list(parent_id=target_folder_id)
                existing_files = [f["name"] for f in files_in_folder.get("files", []) 
                                if f.get("kind") != "drive#folder"]
                if file_name in existing_files:
                    print(f"文件已存在，跳过：{file_name}")
                    return
            except Exception as e:
                print(f"检查文件存在失败: {e}")
                return

            async def retry_offline_download(bt_url, parent_id, retries=3):
                for attempt in range(retries):
                    try:
                        task = await client.offline_download(bt_url, parent_id=parent_id)
                        success_msg = f"已添加到 /Anime/{title}: {file_name}"
                        echo_github_output(success_msg)
                        # Add to the list of added files for notification
                        added_files.append(f"[{title}] {file_name}")
                        return
                    except Exception as e:
                        error_msg = f"失败 (尝试 {attempt + 1}/{retries}): {e}"
                        if attempt + 1 == retries:
                            error_msg = f"最终失败: {e}"
                            echo_github_output(error_msg, "error")
                        else:
                            echo_github_output(error_msg, "warning")

            await retry_offline_download(bt_url, target_folder_id)

        await asyncio.gather(*(process_entry(entry) for entry in feed.entries))

    await asyncio.gather(*(process_rss(title, rss_url) for title, rss_url in rss_data))
    
    # Send notification for all added files
    if added_files:
        notification_title = f"新添加了 {len(added_files)} 个动漫文件"
        notification_content = "\n".join(added_files)
        await notify(notification_title, notification_content)
        
        # Echo summary to GitHub Actions log
        summary = "\n".join([
            "## 🎬 动漫文件下载摘要",
            f"### 已添加 {len(added_files)} 个新文件:",
            "```",
            "\n".join(added_files),
            "```"
        ])
        echo_github_output(summary)
    else:
        echo_github_output("## 🎬 动漫文件下载摘要\n### 本次运行没有添加新文件")

if __name__ == "__main__":
    asyncio.run(main())
