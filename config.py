# ==================== 配置参数（原config.py内容）====================
class Config:
    """策略配置参数"""
    
    # Coinglass API (初级会员)
    COINGLASS_API_KEY = "04c3a7ffe78d4249968a1886f8e7af1a"
    COINGLASS_BASE_URL = "https://open-api-v4.coinglass.com/api"
    
    # Telegram通知配置
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8216072079:AAFqJjOE81siaDQsHbFIBKBKfWh7SnTRuzI")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "838429342")
    
    # 策略核心参数
    FUNDING_RATE_THRESHOLD = -0.001  # -0.1%
    OI_SURGE_RATIO = 2.0
    OI_SHORT_WINDOW = 3
    OI_LONG_WINDOW = 10
    SCAN_INTERVAL = 300  # 5分钟
    
    # 多空比参数
    GLOBAL_LS_PERIOD = "1h"
    GLOBAL_SHORT_THRESHOLD = 0.65
    TOP_LS_PERIOD = "15m"
    TOP_TREND_WINDOW = 3
    
    # 主动买卖比参数
    TAKER_RATIO_PERIOD = "1h"
    TAKER_BUY_THRESHOLD = 1.2
    
    # 过滤参数
    MIN_VOLUME_USD = 5000000
    MAX_SYMBOLS_TO_ANALYZE = 30
    DATA_DIR = "data"
    OI_HISTORY_FILE = "oi_history.json"
    SIGNALS_LOG_FILE = "signals_log.json"
    
    # 币安配置
    BINANCE_CONFIG = {
        'enableRateLimit': True,
        'options': {'defaultType': 'future'},
        'timeout': 15000,
    }
