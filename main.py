import asyncio
import feedparser
import csv
import re
import requests  # 新增导入
from pikpakapi import PikPakApi
from info import EMAIL, PW
from torrentool.api import Torrent  # 确保导入正确的Torrent解析类

async def main():
    # 初始化客户端并登录
    client = PikPakApi(username=EMAIL, password=PW)
    await client.login()

    async def get_or_create_folder(parent_id, folder_name):
        """在指定父文件夹下获取或创建子文件夹"""
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
        """清理非法字符"""
        return re.sub(r'[\\/:*?"<>|]', '_', name).strip()

    # 读取 CSV
    csv_file = "data.csv"
    with open(csv_file, newline='', encoding='utf-8') as file:
        reader = csv.reader(file)
        rss_data = [(row[0], row[1]) for row in reader if len(row) >= 2]

    # 处理每个 RSS
    for title, rss_url in rss_data:
        print(f"\n处理 RSS: {title}")
        feed = feedparser.parse(rss_url)
        
        # 创建文件夹结构
        anime_root_id = await get_or_create_folder("root", "Anime")
        target_folder_id = await get_or_create_folder(anime_root_id, title)
        
        # 处理条目
        for entry in feed.entries:
            # 获取种子链接
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
                continue
            
            # 下载并解析种子文件名
            try:
                response = await asyncio.to_thread(requests.get, bt_url, timeout=10)
                response.raise_for_status()
                torrent = Torrent.from_string(response.content)
                file_name = torrent.name
            except Exception as e:
                print(f"解析种子失败: {e}")
                continue
            
            # 检查目标文件夹是否存在同名文件
            try:
                files_in_folder = await client.file_list(parent_id=target_folder_id)
                existing_files = [f["name"] for f in files_in_folder.get("files", []) 
                                if f.get("kind") != "drive#folder"]
                if file_name in existing_files:
                    print(f"文件已存在，跳过：{file_name}")
                    continue
            except Exception as e:
                print(f"检查文件存在失败: {e}")
                continue
            
            # 添加离线任务
            try:
                task = await client.offline_download(
                    bt_url,
                    parent_id=target_folder_id
                )
                print(f"已添加到 /Anime/{title}: {file_name}")
            except Exception as e:
                print(f"失败: {e}")

if __name__ == "__main__":
    asyncio.run(main())