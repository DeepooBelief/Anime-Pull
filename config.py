"""
Fast mode configuration for anime downloader
"""

# Performance settings
FAST_MODE = True

if FAST_MODE:
    # Aggressive timeouts for speed
    HTTP_TIMEOUT = 5
    CONNECT_TIMEOUT = 3
    MAX_RETRIES = 1
    MAX_CONCURRENT_RSS = 10
    MAX_CONCURRENT_DOWNLOADS = 8
    FOLDER_CACHE_SIZE = 100
    
    # Reduced logging
    VERBOSE_LOGGING = False
    PROGRESS_UPDATES = True
else:
    # Conservative settings for reliability
    HTTP_TIMEOUT = 30
    CONNECT_TIMEOUT = 10
    MAX_RETRIES = 3
    MAX_CONCURRENT_RSS = 5
    MAX_CONCURRENT_DOWNLOADS = 3
    FOLDER_CACHE_SIZE = 50
    
    # Full logging
    VERBOSE_LOGGING = True
    PROGRESS_UPDATES = True
