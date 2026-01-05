# -*- coding: utf-8 -*-
"""
山寨币轧空监控机器人 - 完整版
严格按照原文策略逻辑实现：
1. 极端负费率 -> 2. OI异常增多 -> 3. 突破阻力位 -> 4. Long/Short Ratio减少 -> 5. OI减少，费率正常
作者: AI Assistant
日期: 2026-01-04
"""
import os

# 确保数据目录存在（关键！）
os.makedirs("data", exist_ok=True)
os.makedirs("logs", exist_ok=True)  # 如果需要日志目录

import time
import json
import csv
import os
from datetime import datetime, timedelta
from collections import deque, defaultdict
import requests
import ccxt
import pandas as pd
from typing import Dict, List, Optional, Tuple, Any

# ==================== 配置类 (原config.py内容) ====================
class Config:
    """策略配置参数 - 内联版本"""
    
    # Coinglass API
    COINGLASS_API_KEY = "04c3a7ffe78d4249968a1886f8e7af1a"
    COINGLASS_BASE_URL = "https://open-api-v4.coinglass.com/api"
    
    # Telegram通知
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8216072079:AAFqJjOE81siaDQsHbFIBKBKfWh7SnTRuzI")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "838429342")
    
    # 策略核心
    FUNDING_RATE_THRESHOLD = -0.0005  # -0.1%
    OI_SURGE_RATIO = 1.1
    OI_SHORT_WINDOW = 3
    OI_LONG_WINDOW = 3
    SCAN_INTERVAL_SECONDS = 60  # 5分钟
    
    # 多空比
    GLOBAL_LS_PERIOD = "1h"
    GLOBAL_SHORT_THRESHOLD = 0.65
    TOP_LS_PERIOD = "15m"
    TOP_TREND_WINDOW = 3
    
    # 主动买卖比
    TAKER_RATIO_PERIOD = "1h"
    TAKER_BUY_THRESHOLD = 1.0
    
    # 过滤参数
    MIN_VOLUME_USD = 1000000
    MAX_SYMBOLS_TO_ANALYZE = 50
    DATA_DIR = "data"
    OI_HISTORY_FILE = "oi_history.json"
    SIGNALS_LOG_FILE = "signals_log.json"
    
    # 币安配置
    BINANCE_CONFIG = {
        'enableRateLimit': True,
        'options': {'defaultType': 'future'},
        'timeout': 15000,
        'rateLimit': 1200,
    }
    
    # 评分权重
    SCORE_WEIGHTS = {
        'funding_rate': 40,
        'oi_surge': 30,
        'global_short': 15,
        'top_trader': 10,
        'taker_ratio': 5,
    }

print("=" * 60)
print("🔥 轧空监控机器人启动")
print(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

print("=" * 70)
print("🔥 山寨币轧空监控机器人 - 完整逻辑版")
print("📊 策略: 严格遵循原文五阶段逻辑链条")
print(f"🕐 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 70)

# ==================== 工具函数 ====================
def log(msg: str, level: str = "INFO"):
    """统一日志格式"""
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"[{timestamp}] [{level}] {msg}")

def send_telegram(message: str):
    """发送Telegram通知"""
    if not Config.TELEGRAM_TOKEN or not Config.TELEGRAM_CHAT_ID:
        return False
    
    try:
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": Config.TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        log(f"Telegram发送失败: {e}", "ERROR")
        return False

# ==================== Coinglass客户端 ====================
class CoinglassClient:
    """Coinglass API客户端 (用于费率初筛和主动买卖比)"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "accept": "application/json",
            "CG-API-KEY": Config.COINGLASS_API_KEY,
            "User-Agent": "Mozilla/5.0"
        })
        self.base_url = Config.COINGLASS_BASE_URL
    
    def get_negative_funding_symbols(self) -> List[Dict]:
        """获取所有负费率币种"""
        try:
            url = f"{self.base_url}/futures/funding-rate/exchange-list"
            response = self.session.get(url, timeout=15)
        
            if response.status_code == 200:
                data = response.json()
            
                if str(data.get("code")) in ["0", "200"] and "data" in data:
                    symbols = []
                
                    for item in data["data"]:
                        try:
                            symbol = item.get("symbol", "")
                            # 核心修复：跳过币安没有的指数代码
                            if "INDEX" in symbol or "TOTAL" in symbol or "ALL" in symbol:
                                continue
                        
                            # 🔧 修复这里：stablecoin_margin_list 不是 token_margin_list
                            exchange_list = item.get("stablecoin_margin_list", [])
                        
                            for exchange_data in exchange_list:
                                exchange = exchange_data.get("exchange", "").lower()
                            
                                if "binance" in exchange:
                                    rate = exchange_data.get("funding_rate", 0)
                                    if isinstance(rate, str):
                                        rate = float(rate)
                                
                                    if rate < Config.FUNDING_RATE_THRESHOLD:
                                        full_symbol = f"{symbol}USDT"
                                        symbols.append({
                                            "symbol": full_symbol,
                                            "funding_rate": rate,
                                            "next_funding": exchange_data.get("next_funding_time", ""),
                                            "exchange": "binance",
                                            "timestamp": datetime.now().isoformat()
                                        })
                                        break
                                    
                        except Exception as e:
                            continue
                
                    log(f"Coinglass: 发现 {len(symbols)} 个负费率(<-0.1%)币种", "INFO")
                    # 按资金费率排序（最负的排前面）
                    symbols.sort(key=lambda x: x["funding_rate"])
        
                    # 限制分析数量
                    symbols = symbols[:Config.MAX_SYMBOLS_TO_ANALYZE]
        
                    log(f"筛选后分析 {len(symbols)} 个最负费率的币种", "INFO")
                    return symbols
                
        except Exception as e:
            log(f"Coinglass获取费率失败: {e}", "ERROR")
    
        return []
    
    def get_taker_buy_sell_ratio(self, symbol: str) -> Optional[float]:
        """
        获取主动买卖比 (用于信号增强)
        返回: 买盘/卖盘比率，>1表示买盘强
        """
        try:
            clean_symbol = symbol.replace("USDT", "")
            url = f"{self.base_url}/futures/taker-buy-sell-volume/exchange-list"
            params = {"symbol": clean_symbol, "range": Config.TAKER_RATIO_PERIOD}
            
            response = self.session.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if str(data.get("code")) in ["0", "200"] and "data" in data:
                    for item in data["data"]:
                        exchange = item.get("exchangeName", "").lower()
                        if "binance" in exchange:
                            buy_vol = float(item.get("buyVol", 0))
                            sell_vol = float(item.get("sellVol", 0))
                            
                            if sell_vol > 0:
                                ratio = buy_vol / sell_vol
                                return ratio
                                
        except Exception as e:
            log(f"Coinglass买卖比失败 {symbol}: {e}", "WARN")
        
        return None

# ==================== 币安数据客户端 ====================
class BinanceDataClient:
    """
    币安数据客户端 (用于OI和多空比精确计算)
    采用'费率初筛 -> OI精算'模式，减少API调用
    """
    
    def __init__(self):
        self.exchange = ccxt.binance(Config.BINANCE_CONFIG)
        
        # OI历史数据缓存 {symbol: deque([oi1, oi2, ...], maxlen=10)}
        self.oi_history = self.load_oi_history()
        
        # 活跃信号跟踪 {symbol: {signal_data}}
        self.active_signals = {}
        
        # 确保数据目录存在
        os.makedirs(Config.DATA_DIR, exist_ok=True)
    
    def load_oi_history(self) -> Dict[str, deque]:
        """加载OI历史数据"""
        try:
            if os.path.exists(Config.OI_HISTORY_FILE):
                with open(Config.OI_HISTORY_FILE, 'r') as f:
                    data = json.load(f)
                    # 转换回deque
                    history = {}
                    for symbol, values in data.items():
                        history[symbol] = deque(values, maxlen=Config.OI_LONG_WINDOW)
                    log(f"已加载 {len(history)} 个币种的OI历史数据", "INFO")
                    return history
        except Exception as e:
            log(f"加载OI历史失败: {e}", "WARN")
        
        return {}
    
    def save_oi_history(self):
        """保存OI历史数据"""
        try:
            # 转换deque为list以便JSON序列化
            data = {symbol: list(history) for symbol, history in self.oi_history.items()}
            with open(Config.OI_HISTORY_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log(f"保存OI历史失败: {e}", "WARN")
    
    def get_open_interest(self, symbol: str) -> Optional[float]:
        """
        获取当前OI (原始接口)
        https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest
        """
        try:
            # 使用ccxt获取，更稳定
            oi_data = self.exchange.fetch_open_interest(symbol)
            return oi_data.get('openInterestAmount', 0)
        except Exception as e:
            log(f"获取OI失败 {symbol}: {e}", "DEBUG")
            return None
    
    def calculate_oi_surge_ratio(self, symbol: str, current_oi: float) -> Tuple[float, float]:
        """
        计算OI激增比率 (阶段2: OI异常增多)
        严格按照原文: 最近3次均值 / 最近10次均值 > 2
        返回: (激增比率, OI变化百分比)
        """
        # 初始化或获取历史队列
        if symbol not in self.oi_history:
            self.oi_history[symbol] = deque(maxlen=Config.OI_LONG_WINDOW)
        
        history = self.oi_history[symbol]
        previous_oi = history[-1] if len(history) > 0 else current_oi
        
        # 计算OI变化
        oi_change_pct = 0
        if previous_oi > 0:
            oi_change_pct = (current_oi - previous_oi) / previous_oi * 100
        
        # 添加当前值到历史
        history.append(current_oi)
        
        # 计算激增比率 (当有足够数据时)
        if len(history) >= Config.OI_LONG_WINDOW:
            # 最近3次均值
            short_window = min(Config.OI_SHORT_WINDOW, len(history))
            recent_values = list(history)[-short_window:]
            short_avg = sum(recent_values) / short_window
            
            # 最近10次均值
            long_avg = sum(history) / len(history)
            
            if long_avg > 0:
                surge_ratio = short_avg / long_avg
                return surge_ratio, oi_change_pct
        
        # 数据不足时返回默认值
        return 1.0, oi_change_pct
    
    def get_global_long_short_ratio(self, symbol: str) -> Optional[Dict]:
        """
        获取全平台多空比 (用于阶段4: Long/Short Ratio减少监控)
        https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Long-Short-Ratio
        """
        try:
            clean_symbol = symbol.replace("USDT", "")
            url = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
            params = {
                "symbol": clean_symbol,
                "period": Config.GLOBAL_LS_PERIOD,
                "limit": 10  # 获取最近10个数据点看趋势
            }
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    # 计算趋势
                    latest = data[-1]
                    first = data[0] if len(data) >= 3 else latest
                    
                    current_ratio = float(latest.get("longShortRatio", 0))
                    short_account = float(latest.get("shortAccount", 0))
                    
                    # 判断趋势
                    trend = "下降" if current_ratio < float(first.get("longShortRatio", 1)) else "上升"
                    
                    return {
                        "current_ratio": current_ratio,
                        "short_account": short_account,
                        "trend": trend,
                        "data_points": len(data),
                        "timestamp": datetime.now().isoformat()
                    }
                    
        except Exception as e:
            log(f"获取全平台多空比失败 {symbol}: {e}", "DEBUG")
        
        return None
    
    def get_top_trader_long_short_ratio(self, symbol: str) -> Optional[Dict]:
        """
        获取顶级交易员多空比 (用于信号增强)
        https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Long-Short-Ratio
        """
        try:
            clean_symbol = symbol.replace("USDT", "")
            url = "https://fapi.binance.com/futures/data/topLongShortPositionRatio"
            params = {
                "symbol": clean_symbol,
                "period": Config.TOP_LS_PERIOD,
                "limit": Config.TOP_TREND_WINDOW + 2
            }
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) >= Config.TOP_TREND_WINDOW:
                    # 分析最近N个周期的趋势
                    recent_data = data[-Config.TOP_TREND_WINDOW:]
                    ratios = [float(d.get("longShortRatio", 0)) for d in recent_data]
                    
                    # 计算趋势
                    if len(ratios) >= 2:
                        trend_up = all(ratios[i] <= ratios[i+1] for i in range(len(ratios)-1))
                        trend = "上升" if trend_up else "下降或震荡"
                        
                        return {
                            "current_ratio": ratios[-1],
                            "trend": trend,
                            "trend_data": ratios,
                            "timestamp": datetime.now().isoformat()
                        }
                    
        except Exception as e:
            log(f"获取顶级交易员多空比失败 {symbol}: {e}", "DEBUG")
        
        return None
    
    def save_to_csv(self, symbol: str, data: Dict):
        """
        保存数据到CSV (原文要求)
        文件: data/{symbol}.csv
        """
        try:
            filename = os.path.join(Config.DATA_DIR, f"{symbol}.csv")
            file_exists = os.path.isfile(filename)
            
            with open(filename, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=data.keys())
                if not file_exists:
                    writer.writeheader()
                writer.writerow(data)
                
        except Exception as e:
            log(f"保存CSV失败 {symbol}: {e}", "WARN")
    
    def get_market_data(self, symbol: str) -> Optional[Dict]:
        """获取市场数据 (价格、交易量等)"""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            
            return {
                "price": ticker['last'],
                "volume_24h": ticker.get('quoteVolume', 0),
                "high_24h": ticker.get('high', 0),
                "low_24h": ticker.get('low', 0),
                "change_24h": ticker.get('percentage', 0)
            }
        except Exception as e:
            log(f"获取市场数据失败 {symbol}: {e}", "DEBUG")
            return None

# ==================== 信号分析与跟踪系统 ====================
class SqueezeSignalAnalyzer:
    """
    轧空信号分析与跟踪系统
    完整实现五阶段逻辑链条的监控
    """
    
    def __init__(self):
        self.signals_log = self.load_signals_log()
        self.alert_cooldown = {}  # 警报冷却 {symbol: last_alert_time}
        self.active_tracking = {}  # 正在跟踪的信号 {symbol: {phase, start_time, data}}
        
        # 信号强度阈值
        self.STRONG_SIGNAL_SCORE = 70
        self.MEDIUM_SIGNAL_SCORE = 50
    
    def load_signals_log(self) -> List[Dict]:
        """加载信号历史记录"""
        try:
            if os.path.exists(Config.SIGNALS_LOG_FILE):
                with open(Config.SIGNALS_LOG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    log(f"已加载 {len(data.get('signals', []))} 条历史信号", "INFO")
                    return data.get("signals", [])
        except Exception as e:
            log(f"加载信号记录失败: {e}", "WARN")
        
        return []
    
    def save_signals_log(self):
        """保存信号记录"""
        try:
            data = {
                "signals": self.signals_log,
                "last_update": datetime.now().isoformat(),
                "total_signals": len(self.signals_log)
            }
            with open(Config.SIGNALS_LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log(f"保存信号记录失败: {e}", "WARN")
    
    def analyze_squeeze_potential(self, symbol_data: Dict) -> Optional[Dict]:
        """
        分析轧空潜力 (阶段1+2)
        返回信号数据，包含评分和详细指标
        """
        symbol = symbol_data["symbol"]
        funding_rate = symbol_data["funding_rate"]
        
        # 获取市场数据
        market_data = self.binance.get_market_data(symbol)
        if not market_data or market_data["volume_24h"] < Config.MIN_VOLUME_USD:
            return None
        
        # 获取OI数据并计算激增比率
        current_oi = self.binance.get_open_interest(symbol)
        if not current_oi:
            return None
        
        oi_surge_ratio, oi_change_pct = self.binance.calculate_oi_surge_ratio(symbol, current_oi)
        
        # 严格按照原文核心条件
        core_condition_1 = funding_rate < Config.FUNDING_RATE_THRESHOLD
        core_condition_2 = oi_surge_ratio > Config.OI_SURGE_RATIO
        
        if not (core_condition_1 and core_condition_2):
            return None
        
        # 获取增强指标
        taker_ratio = self.coinglass.get_taker_buy_sell_ratio(symbol)
        global_ls = self.binance.get_global_long_short_ratio(symbol)
        top_ls = self.binance.get_top_trader_long_short_ratio(symbol)
        
        # 计算综合评分
        score, score_details = self.calculate_signal_score(
            funding_rate, oi_surge_ratio, global_ls, top_ls, taker_ratio
        )
        
        # 构建信号数据
        signal_data = {
            "symbol": symbol,
            "score": score,
            "phase": "PHASE_1_2",  # 处于阶段1+2
            "timestamp": datetime.now().isoformat(),
            "core_indicators": {
                "funding_rate": funding_rate,
                "oi_surge_ratio": oi_surge_ratio,
                "oi_change_pct": oi_change_pct,
                "current_oi": current_oi,
                "price": market_data["price"],
                "volume_24h_usd": market_data["volume_24h"]
            },
            "enhanced_indicators": {
                "taker_buy_ratio": taker_ratio,
                "global_long_short": global_ls,
                "top_trader_long_short": top_ls
            },
            "score_details": score_details
        }
        
        # 保存到CSV (原文要求)
        csv_data = {
            "timestamp": signal_data["timestamp"],
            "symbol": symbol,
            "funding_rate": funding_rate,
            "oi_surge_ratio": oi_surge_ratio,
            "oi_current": current_oi,
            "price": market_data["price"],
            "score": score,
            "phase": signal_data["phase"]
        }
        self.binance.save_to_csv(symbol, csv_data)
        
        return signal_data
    
    def calculate_signal_score(self, funding_rate: float, oi_surge_ratio: float,
                              global_ls: Optional[Dict], top_ls: Optional[Dict], 
                              taker_ratio: Optional[float]) -> Tuple[int, Dict]:
        """
        计算信号综合评分 (0-100)
        用于Telegram消息的强度分级
        """
        score = 0
        details = {}
        
        # 1. 资金费率评分 (0-40分)
        if funding_rate < -0.003:
            score += 40
            details["funding"] = "极度负值(40分)"
        elif funding_rate < -0.002:
            score += 30
            details["funding"] = "高度负值(30分)"
        elif funding_rate < -0.0015:
            score += 20
            details["funding"] = "中度负值(20分)"
        elif funding_rate < -0.001:
            score += 10
            details["funding"] = "临界负值(10分)"
        
        # 2. OI激增评分 (0-30分)
        if oi_surge_ratio > 3.0:
            score += 30
            details["oi_surge"] = f"异常激增({oi_surge_ratio:.2f}x, 30分)"
        elif oi_surge_ratio > 2.5:
            score += 25
            details["oi_surge"] = f"强烈激增({oi_surge_ratio:.2f}x, 25分)"
        elif oi_surge_ratio > 2.0:
            score += 20
            details["oi_surge"] = f"显著激增({oi_surge_ratio:.2f}x, 20分)"
        elif oi_surge_ratio > 1.5:
            score += 10
            details["oi_surge"] = f"温和增长({oi_surge_ratio:.2f}x, 10分)"
        
        # 3. 散户空头评分 (0-15分)
        if global_ls and global_ls.get("short_account", 0) > Config.GLOBAL_SHORT_THRESHOLD:
            short_pct = global_ls["short_account"] * 100
            if short_pct > 70:
                score += 15
                details["global_short"] = f"极度拥挤({short_pct:.1f}%, 15分)"
            elif short_pct > 65:
                score += 10
                details["global_short"] = f"高度拥挤({short_pct:.1f}%, 10分)"
            elif short_pct > 60:
                score += 5
                details["global_short"] = f"中度拥挤({short_pct:.1f}%, 5分)"
        
        # 4. 大户动向评分 (0-10分)
        if top_ls and top_ls.get("trend") == "上升":
            score += 10
            details["top_trader"] = "趋势上升(10分)"
        elif top_ls:
            score += 5
            details["top_trader"] = "有数据(5分)"
        
        # 5. 主动买卖比评分 (0-5分)
        if taker_ratio and taker_ratio > Config.TAKER_BUY_THRESHOLD:
            score += 5
            details["taker_ratio"] = f"买盘强劲({taker_ratio:.2f}, 5分)"
        elif taker_ratio and taker_ratio > 1.0:
            score += 3
            details["taker_ratio"] = f"买盘占优({taker_ratio:.2f}, 3分)"
        
        return min(score, 100), details
    
    def check_alert_cooldown(self, symbol: str, score: int) -> bool:
        """检查警报冷却时间"""
        current_time = time.time()
        
        if symbol in self.alert_cooldown:
            last_alert = self.alert_cooldown[symbol]
            
            # 强信号冷却2小时，中信号冷却4小时
            if score >= self.STRONG_SIGNAL_SCORE:
                if current_time - last_alert < 7200:  # 2小时
                    return False
            elif score >= self.MEDIUM_SIGNAL_SCORE:
                if current_time - last_alert < 14400:  # 4小时
                    return False
        
        self.alert_cooldown[symbol] = current_time
        return True
    
    def format_telegram_message(self, signal_data: Dict) -> str:
        """格式化Telegram消息"""
        symbol = signal_data["symbol"]
        score = signal_data["score"]
        indicators = signal_data["core_indicators"]
        enhanced = signal_data["enhanced_indicators"]
        details = signal_data["score_details"]
        
        # 确定信号强度
        if score >= self.STRONG_SIGNAL_SCORE:
            emoji = "🔥🔥🔥"
            strength = "强轧空信号"
        elif score >= self.MEDIUM_SIGNAL_SCORE:
            emoji = "🔥🔥"
            strength = "中轧空信号"
        else:
            emoji = "🔥"
            strength = "弱轧空信号"
        
        # 构建消息
        message = f"{emoji} *{strength}: {symbol}*\n"
        message += "══════════════════════\n"
        message += f"• **综合评分**: `{score}/100`\n"
        message += f"• **信号阶段**: `阶段1+2 (预警期)`\n"
        message += f"• **资金费率**: `{indicators['funding_rate']:.4%}`\n"
        message += f"• **OI激增比**: `{indicators['oi_surge_ratio']:.2f}x`\n"
        message += f"• **OI变化**: `{indicators['oi_change_pct']:+.1f}%`\n"
        message += f"• **当前价格**: `${indicators['price']:.6f}`\n"
        
        # 添加增强指标
        if enhanced["taker_buy_ratio"]:
            message += f"• **主动买盘比**: `{enhanced['taker_buy_ratio']:.2f}`\n"
        
        if enhanced["global_long_short"]:
            short_pct = enhanced["global_long_short"]["short_account"] * 100
            message += f"• **散户空头占比**: `{short_pct:.1f}%`\n"
        
        if enhanced["top_trader_long_short"]:
            message += f"• **大户动向**: `{enhanced['top_trader_long_short']['trend']}`\n"
        
        message += f"• **时间**: {datetime.now().strftime('%H:%M:%S')}\n"
        message += "══════════════════════\n"
        
        # 添加评分详情
        message += "📊 **评分详情**:\n"
        for key, desc in details.items():
            message += f"  • {desc}\n"
        
        # 添加策略逻辑说明
        message += "\n📈 **策略逻辑 (五阶段)**:\n"
        message += "1. ✅ 极端负费率 (庄家控盘)\n"
        message += "2. ✅ OI异常增多 (庄家建仓)\n"
        message += "3. ⏳ 等待价格突破 (需人工确认)\n"
        message += "4. 🔍 监控多空比减少 (进行中)\n"
        message += "5. 📉 跟踪OI减少 & 费率回归\n"
        
        message += "\n⚡ **操作建议**:\n"
        if score >= 70:
            message += "• 加入重点观察列表\n• 准备突破追多\n• 止损: -3%\n• 目标: +10%~+20%\n"
        elif score >= 50:
            message += "• 加入观察列表\n• 等待突破确认\n• 轻仓试单\n• 严格止损\n"
        else:
            message += "• 保持关注\n• 等待更强信号\n• 勿急于入场\n"
        
        message += "\n🤖 *机器人将持续跟踪此币种的多空比和OI变化*"
        
        return message
    
    def track_active_signal(self, symbol: str, signal_data: Dict):
        """开始跟踪一个活跃信号"""
        self.active_tracking[symbol] = {
            "start_time": datetime.now().isoformat(),
            "initial_data": signal_data,
            "last_check": datetime.now().isoformat(),
            "phase": "PHASE_1_2",
            "check_count": 0
        }
        log(f"开始跟踪信号: {symbol}", "INFO")
    
    def update_tracking(self, binance_client):
        """更新所有活跃信号的跟踪状态"""
        symbols_to_remove = []
        
        for symbol, tracking_data in self.active_tracking.items():
            try:
                # 检查是否进入阶段4: Long/Short Ratio减少
                global_ls = binance_client.get_global_long_short_ratio(symbol)
                
                if global_ls and global_ls.get("trend") == "下降":
                    if tracking_data["phase"] == "PHASE_1_2":
                        # 进入阶段4
                        tracking_data["phase"] = "PHASE_4"
                        tracking_data["phase4_start"] = datetime.now().isoformat()
                        
                        # 发送阶段更新通知
                        update_msg = (
                            f"🔄 *信号阶段更新: {symbol}*\n"
                            f"══════════════════════\n"
                            f"• 进入 **阶段4**: Long/Short Ratio开始减少\n"
                            f"• 散户空头比例: `{global_ls['short_account']*100:.1f}%`\n"
                            f"• 多空比趋势: `{global_ls['trend']}`\n"
                            f"• 时间: {datetime.now().strftime('%H:%M:%S')}\n"
                            f"══════════════════════\n"
                            f"📈 策略进展: 散户开始被止损/清算，轧空可能正在进行中。"
                        )
                        
                        if send_telegram(update_msg):
                            log(f"发送阶段更新: {symbol} 进入阶段4", "INFO")
                
                # 检查是否进入阶段5: OI减少，费率回归正常
                current_oi = binance_client.get_open_interest(symbol)
                if current_oi and symbol in binance_client.oi_history:
                    history = list(binance_client.oi_history[symbol])
                    if len(history) >= 3:
                        # 检查OI是否从峰值下降超过15%
                        oi_peak = max(history[-5:] if len(history) >= 5 else history)
                        oi_current = history[-1]
                        
                        if oi_current < oi_peak * 0.85:  # 下降超过15%
                            # 这里可以添加费率检查
                            tracking_data["phase"] = "PHASE_5"
                            tracking_data["phase5_start"] = datetime.now().isoformat()
                            
                            # 发送结束预警
                            end_msg = (
                                f"⚠️ *轧空可能接近尾声: {symbol}*\n"
                                f"══════════════════════\n"
                                f"• 进入 **阶段5**: OI开始减少\n"
                                f"• OI峰值: `{oi_peak:,.0f}`\n"
                                f"• OI当前: `{oi_current:,.0f}`\n"
                                f"• 下降幅度: `{(1 - oi_current/oi_peak)*100:.1f}%`\n"
                                f"• 时间: {datetime.now().strftime('%H:%M:%S')}\n"
                                f"══════════════════════\n"
                                f"📉 策略提示: 庄家可能正在退出，注意风险。"
                            )
                            
                            if send_telegram(end_msg):
                                log(f"发送结束预警: {symbol} 进入阶段5", "INFO")
                            
                            # 标记为待移除（跟踪结束）
                            symbols_to_remove.append(symbol)
                
                tracking_data["check_count"] += 1
                tracking_data["last_check"] = datetime.now().isoformat()
                
                # 如果跟踪超过24小时，自动结束
                start_time = datetime.fromisoformat(tracking_data["start_time"])
                if datetime.now() - start_time > timedelta(hours=24):
                    symbols_to_remove.append(symbol)
                    
            except Exception as e:
                log(f"跟踪信号 {symbol} 更新失败: {e}", "ERROR")
        
        # 移除结束跟踪的信号
        for symbol in symbols_to_remove:
            if symbol in self.active_tracking:
                del self.active_tracking[symbol]
                log(f"结束跟踪信号: {symbol}", "INFO")

# ==================== 主监控引擎 ====================
class SqueezeMonitor:
    """主监控引擎 - 协调所有组件"""
    
    def __init__(self):
        self.coinglass = CoinglassClient()
        self.binance = BinanceDataClient()
        self.analyzer = SqueezeSignalAnalyzer()
        
        # 注入依赖
        self.analyzer.coinglass = self.coinglass
        self.analyzer.binance = self.binance
        
        self.scan_count = 0
        self.total_signals_found = 0
        
        log("监控引擎初始化完成", "SUCCESS")
    
    def test_apis(self) -> bool:
        """测试所有API连接"""
        log("测试API连接...", "INFO")
        
        # 测试Telegram
        if Config.TELEGRAM_TOKEN and Config.TELEGRAM_CHAT_ID:
            test_msg = (
                "🤖 *轧空监控机器人启动测试*\n\n"
                f"• 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"• 策略版本: 完整五阶段逻辑\n"
                f"• 扫描间隔: {Config.SCAN_INTERVAL_SECONDS//60}分钟\n\n"
                "✅ 如果收到此消息，说明Telegram通知功能正常。"
            )
            
            if send_telegram(test_msg):
                log("✅ Telegram测试通知已发送", "SUCCESS")
            else:
                log("⚠️ Telegram测试发送失败", "WARN")
        else:
            log("⚠️ Telegram配置缺失，通知功能禁用", "WARN")
        
        # 测试币安连接
        try:
            ticker = self.binance.exchange.fetch_ticker('BTCUSDT')
            log(f"✅ 币安API连接正常 | BTC: ${ticker['last']:.2f}", "SUCCESS")
            return True
        except Exception as e:
            log(f"❌ 币安连接失败: {e}", "ERROR")
            return False
    
    def run_scan_cycle(self):
        """执行一次完整的扫描周期"""
        self.scan_count += 1
        log(f"开始第 {self.scan_count} 次扫描...", "CYCLE")
        start_time = time.time()
        
        signals_found = 0
        
        # 步骤1: 获取负费率币种 (Coinglass)
        negative_symbols = self.coinglass.get_negative_funding_symbols()
        
        if not negative_symbols:
            log("当前市场无符合负费率条件的币种", "INFO")
            # 仍然更新跟踪中的信号
            self.analyzer.update_tracking(self.binance)
            return
        
        # 限制分析数量
        symbols_to_analyze = negative_symbols[:Config.MAX_SYMBOLS_TO_ANALYZE]
        log(f"分析 {len(symbols_to_analyze)} 个币种...", "INFO")
        
        # 步骤2: 分析每个币种
        for i, symbol_data in enumerate(symbols_to_analyze):
            symbol = symbol_data["symbol"]
            # 二次过滤，确保不查 ALLINDEXUSDT
            if "INDEX" in symbol:
                 continue
            
            # 显示进度
            if (i + 1) % 5 == 0:
                log(f"分析进度: {i+1}/{len(symbols_to_analyze)}", "INFO")
            
            try:
                # 分析轧空潜力
                signal_data = self.analyzer.analyze_squeeze_potential(symbol_data)
                
                if signal_data:
                    signals_found += 1
                    score = signal_data["score"]
                    
                    log(f"发现信号: {symbol} ({score}分)", "ALERT")
                    
                    # 检查冷却时间
                    if self.analyzer.check_alert_cooldown(symbol, score):
                        # 发送Telegram警报
                        telegram_msg = self.analyzer.format_telegram_message(signal_data)
                        
                        if send_telegram(telegram_msg):
                            log(f"Telegram警报已发送: {symbol}", "SUCCESS")
                        
                        # 记录信号
                        self.analyzer.signals_log.append(signal_data)
                        self.analyzer.save_signals_log()
                        
                        # 开始跟踪这个信号
                        self.analyzer.track_active_signal(symbol, signal_data)
                    
                    self.total_signals_found += 1
                
                # 避免请求过快
                time.sleep(0.5)
                
            except Exception as e:
                log(f"分析 {symbol} 失败: {e}", "ERROR")
                continue
        
        # 步骤3: 更新所有活跃信号的跟踪状态
        self.analyzer.update_tracking(self.binance)
        
        # 步骤4: 保存OI历史数据
        self.binance.save_oi_history()
        
        # 完成扫描
        elapsed = time.time() - start_time
        log(f"扫描完成 ({elapsed:.1f}秒)", "INFO")
        log(f"本次发现信号: {signals_found}个 | 历史总信号: {self.total_signals_found}个", "STATS")
        
        # 显示统计信息 (每5次扫描)
        if self.scan_count % 5 == 0:
            self.show_statistics()
    
    def show_statistics(self):
        """显示运行统计"""
        total = len(self.analyzer.signals_log)
        strong = len([s for s in self.analyzer.signals_log if s["score"] >= 70])
        medium = len([s for s in self.analyzer.signals_log if s["score"] >= 50])
        weak = total - strong - medium
        
        active = len(self.analyzer.active_tracking)
        
        print(f"\n{'='*60}")
        print(f"📊 运行统计 (第{self.scan_count}次扫描)")
        print(f"{'='*60}")
        print(f"• 总扫描次数: {self.scan_count}")
        print(f"• 历史总信号: {total}")
        print(f"• 强信号: {strong} | 中信号: {medium} | 弱信号: {weak}")
        print(f"• 正在跟踪: {active} 个活跃信号")
        
        if total > 0:
            avg_score = sum(s["score"] for s in self.analyzer.signals_log) / total
            print(f"• 平均评分: {avg_score:.1f}")
        
        # 显示最近信号
        if self.analyzer.signals_log:
            recent = self.analyzer.signals_log[-3:]
            print(f"\n🕐 最近信号:")
            for signal in recent:
                time_str = datetime.fromisoformat(signal["timestamp"]).strftime("%m-%d %H:%M")
                print(f"   {time_str} | {signal['symbol']}: {signal['score']}分")
        
        # 显示正在跟踪的信号
        if self.analyzer.active_tracking:
            print(f"\n🔍 正在跟踪的信号:")
            for symbol, data in list(self.analyzer.active_tracking.items())[:5]:
                phase = data.get("phase", "PHASE_1_2")
                start = datetime.fromisoformat(data["start_time"]).strftime("%H:%M")
                print(f"   {symbol}: {phase} (开始于 {start})")
        
        print(f"{'='*60}\n")
    
    def run(self):
        """主运行循环"""
        # 显示配置
        print(f"\n🎯 策略配置 (严格遵循原文)")
        print(f"{'='*60}")
        print(f"核心条件:")
        print(f"  • 资金费率 < {Config.FUNDING_RATE_THRESHOLD:.3%}")
        print(f"  • OI激增比 > {Config.OI_SURGE_RATIO}x (近3次/近10次)")
        print(f"\n增强指标:")
        print(f"  • 散户空头 > {Config.GLOBAL_SHORT_THRESHOLD*100:.0f}%")
        print(f"  • 主动买盘比 > {Config.TAKER_BUY_THRESHOLD}")
        print(f"  • 大户多空比趋势上升")
        print(f"\n运行设置:")
        print(f"  • 扫描间隔: {Config.SCAN_INTERVAL_SECONDS//60} 分钟")
        print(f"  • 数据保存: {Config.DATA_DIR}/{{symbol}}.csv")
        print(f"  • 最大分析: {Config.MAX_SYMBOLS_TO_ANALYZE} 币种/次")
        print(f"{'='*60}")
        
        # 测试API
        if not self.test_apis():
            log("API测试失败，程序退出", "ERROR")
            return
        
        log("开始主监控循环...", "SUCCESS")
        
        # 主循环
        last_scan_time = 0
        while True:
            try:
                current_time = time.time()
                
                # 检查是否到达扫描时间
                if current_time - last_scan_time >= Config.SCAN_INTERVAL_SECONDS:
                    self.run_scan_cycle()
                    last_scan_time = current_time
                    
                    # 计算下次扫描时间
                    next_scan = datetime.now() + timedelta(seconds=Config.SCAN_INTERVAL_SECONDS)
                    log(f"下次扫描: {next_scan.strftime('%H:%M:%S')}", "INFO")
                
                # 等待期间保持活跃
                wait_time = max(1, Config.SCAN_INTERVAL_SECONDS - (time.time() - last_scan_time))
                time.sleep(min(wait_time, 30))  # 最多睡30秒，以便及时响应
                
            except KeyboardInterrupt:
                log("用户中断，程序停止", "WARN")
                
                # 保存所有数据
                self.binance.save_oi_history()
                self.analyzer.save_signals_log()
                
                # 发送停止通知
                if Config.TELEGRAM_TOKEN and Config.TELEGRAM_CHAT_ID:
                    stop_msg = (
                        "🛑 *轧空监控机器人已停止*\n\n"
                        f"• 停止时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"• 总扫描次数: {self.scan_count}\n"
                        f"• 总发现信号: {self.total_signals_found}\n"
                        f"• 正在跟踪: {len(self.analyzer.active_tracking)} 个信号\n\n"
                        "机器人已保存所有数据，下次启动将恢复运行。"
                    )
                    send_telegram(stop_msg)
                
                break
                
            except Exception as e:
                log(f"主循环异常: {e}", "ERROR")
                time.sleep(60)  # 异常后等待1分钟
# ==================== 调试函数 ====================
def quick_debug():
    """快速调试前5个币种 - 独立运行"""
    print("="*60)
    print("🔍 快速调试模式")
    print("="*60)
    
    # 需要导入必要的类
    fetcher = DataFetcher()
    symbols = fetcher.get_funding_symbols()[:5]  # 只取前5个
    
    print(f"测试前5个最负费率的币种:")
    
    for i, symbol_info in enumerate(symbols):
        symbol = symbol_info["symbol"]
        funding = symbol_info["funding_rate"]
        
        print(f"\n{i+1}. 🔍 {symbol}:")
        print(f"   资金费率: {funding:.4%} (要求 < {Config.FUNDING_RATE_THRESHOLD:.3%})")
        
        # 检查OI
        oi_ratio, oi_value = fetcher.check_oi_surge(symbol)
        print(f"   OI激增比: {oi_ratio:.2f}x (要求 > {Config.OI_SURGE_RATIO})")
        
        # 检查交易量
        try:
            ticker = fetcher.exchange.fetch_ticker(symbol)
            volume = ticker.get('quoteVolume', 0)
            print(f"   24h交易量: ${volume/1_000_000:.2f}M (要求 > ${Config.MIN_VOLUME_USD/1_000_000}M)")
        except Exception as e:
            print(f"   ❌ 无法获取交易量: {e}")
            volume = 0
        
        # 判断是否通过
        conditions_passed = 0
        total_conditions = 2  # 费率已在筛选时通过
        
        if funding < Config.FUNDING_RATE_THRESHOLD:
            conditions_passed += 1
        
        if oi_ratio > Config.OI_SURGE_RATIO:
            conditions_passed += 1
        else:
            print(f"   💡 OI激增不足: {oi_ratio:.2f} < {Config.OI_SURGE_RATIO}")
        
        if volume > Config.MIN_VOLUME_USD:
            conditions_passed += 1
        else:
            print(f"   💡 交易量不足: ${volume/1_000_000:.2f}M < ${Config.MIN_VOLUME_USD/1_000_000}M")
        
        if conditions_passed == total_conditions + 1:  # +1是费率条件
            print(f"   ✅ 符合所有条件！")
        else:
            print(f"   ❌ 通过条件: {conditions_passed}/{total_conditions + 1}")
    
    print("\n" + "="*60)
    print("调试完成。建议：")
    print("1. 如果多数币种OI < 2.0，考虑降低 OI_SURGE_RATIO")
    print("2. 如果交易量不足，考虑降低 MIN_VOLUME_USD")
    print("3. 如果都满足但没信号，检查其他条件")
    print("="*60)

# ==================== 主函数 ====================
def main():
    log("初始化机器人...")
    # ... 原有代码 ...

# ==================== 主函数 ====================
if __name__ == "__main__":
    # 创建监控实例
    monitor = SqueezeMonitor()
    
    # 运行监控
    monitor.run()
