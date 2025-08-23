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
    processed_files = set()  # Track files processed in this session to avoid duplicates
    folder_cache = {}  # Cache folder IDs to avoid repeated API calls

    async def get_or_create_folder(parent_id, folder_name):
        folder_name = sanitize_folder_name(folder_name)
        # Ensure parent_id is None when referring to root
        if parent_id == "root" or parent_id == "" or parent_id is None:
            parent_id = None
        
        # Check cache first
        cache_key = f"{parent_id}:{folder_name}"
        if cache_key in folder_cache:
            return folder_cache[cache_key]
        
        # Add retry logic for API calls
        max_retries = 2  # Reduced retries for speed
        for attempt in range(max_retries):
            try:
                files = await client.file_list(parent_id=parent_id)
                for f in files.get("files", []):
                    if f["name"] == folder_name and f["kind"] == "drive#folder":
                        folder_cache[cache_key] = f["id"]  # Cache the result
                        return f["id"]
                # Folder not found, create it
                new_folder = await client.create_folder(name=folder_name, parent_id=parent_id)
                folder_id = new_folder["file"]["id"]
                folder_cache[cache_key] = folder_id  # Cache the result
                return folder_id
            except Exception as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5)  # Shorter wait time
                else:
                    print(f"Failed to access or create folder '{folder_name}' after {max_retries} attempts: {e}")
                    raise

    def sanitize_folder_name(name):
        return re.sub(r'[\\/:*?"<>|]', '_', name).strip()

    # Create a global session with optimized settings
    timeout = aiohttp.ClientTimeout(total=10, connect=5)  # Much faster timeouts
    connector = aiohttp.TCPConnector(limit=50, limit_per_host=10)  # Connection pooling
    session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    
    async def fetch_torrent(bt_url):
        try:
            async with session.get(bt_url) as response:
                response.raise_for_status()
                return await response.read()
        except asyncio.TimeoutError:
            print(f"Timeout fetching: {bt_url}")
            raise
        except Exception as e:
            print(f"Error fetching {bt_url}: {e}")
            raise

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
    rss_seen = set()  # Track seen RSS feeds to avoid duplicates
    csv_files = [f for f in os.listdir(data_dir) if f.endswith('.csv')]
    
    # If no seasonal files exist but the old data.csv exists, use it as fallback
    if not csv_files and os.path.exists("data.csv"):
        print("No seasonal data files found. Using legacy data.csv as fallback.")
        with open("data.csv", newline='', encoding='utf-8') as file:
            reader = csv.reader(file)
            for row in reader:
                if len(row) >= 2:
                    title, rss_url = row[0], row[1]
                    if (title, rss_url) not in rss_seen:
                        rss_data.append((title, rss_url))
                        rss_seen.add((title, rss_url))
    else:
        # Read from all seasonal CSV files
        for csv_file in csv_files:
            season_path = os.path.join(data_dir, csv_file)
            print(f"Reading data from {season_path}")
            with open(season_path, newline='', encoding='utf-8') as file:
                reader = csv.reader(file)
                for row in reader:
                    if len(row) >= 2:
                        title, rss_url = row[0], row[1]
                        if (title, rss_url) not in rss_seen:
                            rss_data.append((title, rss_url))
                            rss_seen.add((title, rss_url))
                        else:
                            continue  # Skip verbose duplicate logging
        
        if not rss_data:
            print("No anime data found in seasonal files.")
        else:
            print(f"总共找到 {len(rss_data)} 个唯一的RSS源")

    async def process_rss(title, rss_url):
        try:
            feed = feedparser.parse(rss_url)
            if not feed.entries:
                return
                
            anime_root_id = await get_or_create_folder("root", "Anime")
            target_folder_id = await get_or_create_folder(anime_root_id, title)
            
            # Create a semaphore to limit concurrent downloads for this RSS feed
            download_semaphore = asyncio.Semaphore(5)  # Increased concurrency
            
            print(f"处理 {title}: {len(feed.entries)} 个条目")
        except Exception as e:
            print(f"RSS解析失败 {title}: {e}")
            return

        async def process_entry(entry):
            async with download_semaphore:  # Acquire semaphore before processing
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
                
                # Create a unique identifier for this file
                file_identifier = f"{title}:{file_name}"
                if file_identifier in processed_files:
                    return  # Silent skip for speed
                
                processed_files.add(file_identifier)
                
            except Exception as e:
                print(f"解析种子失败: {e}")
                return

            # Quick file existence check - optimized for speed
            try:
                files_in_folder = await client.file_list(parent_id=target_folder_id)
                existing_files = {f["name"] for f in files_in_folder.get("files", [])}  # Use set for O(1) lookup
                
                if file_name in existing_files:
                    return  # Silent skip for speed
                    
            except Exception as e:
                print(f"检查文件存在失败: {e}")
                return

            async def retry_offline_download(bt_url, parent_id, retries=2):  # Reduced retries
                for attempt in range(retries):
                    try:
                        task = await client.offline_download(bt_url, parent_id=parent_id)
                        print(f"✓ 成功添加: [{title}] {file_name}")
                        added_files.append(f"[{title}] {file_name}")
                        return True
                    except Exception as e:
                        error_msg = str(e).lower()
                        
                        # Fast error categorization
                        if any(keyword in error_msg for keyword in ["already exists", "duplicate", "existed", "重复"]):
                            return False  # Silent skip
                        elif any(keyword in error_msg for keyword in ["rate limit", "too many", "频率", "限制"]):
                            if attempt + 1 < retries:
                                await asyncio.sleep(2)  # Shorter delay
                            continue
                        elif any(keyword in error_msg for keyword in ["network", "timeout", "connection"]):
                            if attempt + 1 < retries:
                                await asyncio.sleep(1)  # Shorter delay
                            continue
                        elif any(keyword in error_msg for keyword in ["auth", "login", "token"]):
                            try:
                                await client.login()
                                await client.refresh_access_token()
                                continue
                            except:
                                return False
                        else:
                            if attempt + 1 >= retries:
                                print(f"✗ 下载失败: {file_name} - {e}")
                                return False

            success = await retry_offline_download(bt_url, target_folder_id)

        await asyncio.gather(*(process_entry(entry) for entry in feed.entries))

    if rss_data:
        print(f"开始处理 {len(rss_data)} 个RSS源...")
        start_time = datetime.now()
        await asyncio.gather(*(process_rss(title, rss_url) for title, rss_url in rss_data))
        end_time = datetime.now()
        print(f"处理完成，耗时: {end_time - start_time}")
    else:
        print("No anime data found to process.")
    
    # Clean up session
    await session.close()
    
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
