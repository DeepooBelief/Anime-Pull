"""
Download cache to track processed files across script runs
"""
import json
import os
from datetime import datetime, timedelta

class DownloadCache:
    def __init__(self, cache_file="download_cache.json"):
        self.cache_file = cache_file
        self.cache = self._load_cache()
    
    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                # Clean old entries (older than 7 days)
                cutoff = datetime.now() - timedelta(days=7)
                cache = {k: v for k, v in cache.items() 
                        if datetime.fromisoformat(v['timestamp']) > cutoff}
                return cache
            except:
                return {}
        return {}
    
    def _save_cache(self):
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=2)
    
    def is_processed(self, title, file_name):
        key = f"{title}:{file_name}"
        return key in self.cache
    
    def mark_processed(self, title, file_name, status="downloaded"):
        key = f"{title}:{file_name}"
        self.cache[key] = {
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "title": title,
            "file_name": file_name
        }
        self._save_cache()
    
    def get_stats(self):
        total = len(self.cache)
        downloaded = sum(1 for v in self.cache.values() if v['status'] == 'downloaded')
        skipped = sum(1 for v in self.cache.values() if v['status'] == 'skipped')
        return {"total": total, "downloaded": downloaded, "skipped": skipped}
