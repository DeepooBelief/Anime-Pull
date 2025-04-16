import asyncio
import feedparser
import csv
import re
import aiohttp
import os
from datetime import datetime
from pikpakapi import PikPakApi
from info import EMAIL, PW, PUSHPLUS_TOKEN
from torrentool.api import Torrent
import requests


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
        # Ensure parent_id is None when referring to root
        if parent_id == "root" or parent_id == "" or parent_id is None:
            parent_id = None
        
        # Add retry logic for API calls
        max_retries = 3
        for attempt in range(max_retries):
            try:
                files = await client.file_list(parent_id=parent_id)
                for f in files.get("files", []):
                    if f["name"] == folder_name and f["kind"] == "drive#folder":
                        return f["id"]
                # Folder not found, create it
                new_folder = await client.create_folder(name=folder_name, parent_id=parent_id)
                return new_folder["file"]["id"]
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"Folder operation failed (attempt {attempt+1}/{max_retries}): {e}")
                    await asyncio.sleep(1)  # Wait a bit before retrying
                else:
                    print(f"Failed to access or create folder '{folder_name}' after {max_retries} attempts: {e}")
                    raise

    def sanitize_folder_name(name):
        return re.sub(r'[\\/:*?"<>|]', '_', name).strip()

    async def fetch_torrent(bt_url):
        async with aiohttp.ClientSession() as session:
            async with session.get(bt_url) as response:
                response.raise_for_status()
                return await response.read()

    # Create data directory if it doesn't exist
    data_dir = "data"
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
        # If old data.csv exists, migrate it to the current season
        if os.path.exists("data.csv"):
            current_year = datetime.now().year
            current_quarter = (datetime.now().month - 1) // 3 + 1
            current_season = f"{current_year}-Q{current_quarter}"
            season_file = os.path.join(data_dir, f"{current_season}.csv")
            
            # Copy data from old file to new seasonal file
            with open("data.csv", "r", newline='', encoding='utf-8') as old_file:
                with open(season_file, "w", newline='', encoding='utf-8') as new_file:
                    new_file.write(old_file.read())
            print(f"Migrated legacy data.csv to {season_file}")

    # Collect all RSS data from seasonal CSV files
    rss_data = []
    csv_files = [f for f in os.listdir(data_dir) if f.endswith('.csv')]
    
    # If no seasonal files exist but the old data.csv exists, use it as fallback
    if not csv_files and os.path.exists("data.csv"):
        print("No seasonal data files found. Using legacy data.csv as fallback.")
        with open("data.csv", newline='', encoding='utf-8') as file:
            reader = csv.reader(file)
            rss_data = [(row[0], row[1]) for row in reader if len(row) >= 2]
    else:
        # Read from all seasonal CSV files
        for csv_file in csv_files:
            season_path = os.path.join(data_dir, csv_file)
            print(f"Reading data from {season_path}")
            with open(season_path, newline='', encoding='utf-8') as file:
                reader = csv.reader(file)
                season_data = [(row[0], row[1]) for row in reader if len(row) >= 2]
                rss_data.extend(season_data)
        
        if not rss_data:
            print("No anime data found in seasonal files.")

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
                existing_files = [f["name"] for f in files_in_folder.get("files", [])]
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
                        print(f"已添加到 /Anime/{title}: {file_name}")
                        # Add to the list of added files for notification
                        added_files.append(f"[{title}] {file_name}")
                        return
                    except Exception as e:
                        print(f"失败 (尝试 {attempt + 1}/{retries}): {e}")
                        if attempt + 1 < retries:
                            await asyncio.sleep(1)  # Add delay between retries
                        else:
                            print(f"最终失败: {e}")

            await retry_offline_download(bt_url, target_folder_id)

        await asyncio.gather(*(process_entry(entry) for entry in feed.entries))

    if rss_data:
        await asyncio.gather(*(process_rss(title, rss_url) for title, rss_url in rss_data))
    else:
        print("No anime data found to process.")
    
    # Send notification for all added files
    if added_files:
        notification_title = f"新添加了 {len(added_files)} 个动漫文件"
        notification_content = "\n".join(added_files)
        await notify(notification_title, notification_content)


def get_current_season():
    """Get the current year and quarter in YYYY-QN format"""
    now = datetime.now()
    year = now.year
    quarter = (now.month - 1) // 3 + 1
    return f"{year}-Q{quarter}"


def add_anime_to_season(title, rss_url, season=None):
    """Add a new anime to the specified season file (or current season if not specified)"""
    if season is None:
        season = get_current_season()
    
    # Create data directory if it doesn't exist
    data_dir = "data"
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    
    season_file = os.path.join(data_dir, f"{season}.csv")
    
    # Check if anime already exists in the season file
    existing_entries = []
    if os.path.exists(season_file):
        with open(season_file, 'r', newline='', encoding='utf-8') as file:
            reader = csv.reader(file)
            existing_entries = list(reader)
            
        for entry in existing_entries:
            if len(entry) >= 2 and entry[0] == title:
                print(f"Anime '{title}' already exists in {season} season file.")
                return False
    
    # Add the new anime
    with open(season_file, 'a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow([title, rss_url])
    
    print(f"Added '{title}' to {season} season file.")
    return True


if __name__ == "__main__":
    asyncio.run(main())
