import asyncio
import feedparser
import csv
import re
import aiohttp
from pikpakapi import PikPakApi
from info import EMAIL, PW
from torrentool.api import Torrent

async def main():
    client = PikPakApi(username=EMAIL, password=PW)
    await client.login()
    await client.refresh_access_token()

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

            try:
                task = await client.offline_download(
                    bt_url,
                    parent_id=target_folder_id
                )
                print(f"已添加到 /Anime/{title}: {file_name}")
            except Exception as e:
                print(f"失败: {e}")

        await asyncio.gather(*(process_entry(entry) for entry in feed.entries))

    await asyncio.gather(*(process_rss(title, rss_url) for title, rss_url in rss_data))

if __name__ == "__main__":
    asyncio.run(main())
