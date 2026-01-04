# -*- coding: utf-8 -*-
"""
山寨币轧空监控机器人 - 修复版
修复了Coinglass API身份验证和参数问题
"""

import ccxt
import time
import json
import requests
from datetime import datetime, timedelta
from collections import deque, defaultdict
import os
import pandas as pd

print("=" * 60)
print("🔥 山寨币轧空监控机器人 - 修复版")
print("📊 Coinglass + 币安混合策略")
print(f"🕐 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# ==================== 配置 ====================
class Config:
    COINGLASS_API_KEY = "04c3a7ffe78d4249968a1886f8e7af1a"
    COINGLASS_BASE_URL = "https://open-api-v4.coinglass.com/api"
    
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
    
    BINANCE_CONFIG = {
        'enableRateLimit': True,
        'options': {'defaultType': 'future'},
        'timeout': 15000,
    }
    
    # 策略参数
    FUNDING_THRESHOLD = -0.0018
    OI_SURGE_RATIO = 2.0
    TAKER_BUY_RATIO = 1.2
    VOLUME_THRESHOLD = 5000000
    
    SCAN_INTERVAL = 180  # 3分钟
    MAX_SYMBOLS = 30

# ==================== Coinglass客户端 ====================
class CoinglassClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = Config.COINGLASS_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "accept": "application/json",
            "CG-API-KEY": api_key,
            "User-Agent": "Mozilla/5.0"
        })
    
    def test_api(self):
        """测试API连接"""
        try:
            url = f"{self.base_url}/futures/supported-coins"
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                return data.get("code") == "0"
        except:
            pass
        return False
    
    def get_funding_rates(self):
        """获取资金费率"""
        try:
            url = f"{self.base_url}/futures/funding-rate/exchange-list"
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("code") == "0" and "data" in data:
                    symbols = []
                    
                    for item in data["data"]:
                        try:
                            # 解析费率
                            rate_str = str(item.get("rate", "0"))
                            if "%" in rate_str:
                                rate = float(rate_str.replace("%", "")) / 100
                            else:
                                rate = float(rate_str)
                            
                            # 只关注币安的负费率
                            if (rate < 0 and 
                                item.get("exchangeName", "").lower() == "binance" and
                                "USDT" in item.get("symbol", "")):
                                
                                symbols.append({
                                    "symbol": item["symbol"],
                                    "funding_rate": rate,
                                    "next_funding": item.get("nextFundingTime", "")
                                })
                        except:
                            continue
                    
                    return symbols
        except Exception as e:
            print(f"[Coinglass] 获取费率失败: {e}")
        
        return []
    
    def get_taker_ratio(self, symbol: str):
        """获取买卖比率 - 修复版"""
        try:
            # 移除USDT后缀
            clean_symbol = symbol.replace("USDT", "")
            
            url = f"{self.base_url}/futures/taker-buy-sell-volume/exchange-list"
            
            # 尝试不同的参数组合
            params_combinations = [
                {"symbol": clean_symbol, "range": "h4"},
                {"symbol": clean_symbol, "range": "h1"},
                {"symbol": clean_symbol}  # 不指定range
            ]
            
            for params in params_combinations:
                try:
                    response = self.session.get(url, params=params, timeout=10)
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        if data.get("code") == "0" and "data" in data:
                            data_list = data["data"]
                            
                            if isinstance(data_list, list):
                                for item in data_list:
                                    # 查找币安数据
                                    exchange = item.get("exchangeName", "").lower()
                                    if "binance" in exchange:
                                        buy = float(item.get("buyVol", 0))
                                        sell = float(item.get("sellVol", 0))
                                        
                                        if sell > 0:
                                            ratio = buy / sell
                                            return ratio
                
                except:
                    continue
            
            return 1.0  # 默认值
            
        except Exception as e:
            print(f"[Coinglass] 买卖比失败 {symbol}: {e}")
            return 1.0

# ==================== 币安客户端 ====================
class BinanceClient:
    def __init__(self):
        self.exchange = ccxt.binance(Config.BINANCE_CONFIG)
        self.oi_history = defaultdict(lambda: deque(maxlen=20))
    
    def get_oi_data(self, symbol):
        """获取持仓数据"""
        try:
            oi_data = self.exchange.fetch_open_interest(symbol)
            current_oi = oi_data.get("openInterestAmount", 0)
            
            # 更新历史
            history = self.oi_history[symbol]
            
            # 计算变化
            change_pct = 0
            if len(history) > 0 and history[-1] > 0:
                change_pct = (current_oi - history[-1]) / history[-1] * 100
            
            history.append(current_oi)
            
            # 计算短期/长期均值
            if len(history) >= 10:
                short_avg = sum(list(history)[-5:]) / 5
                long_avg = sum(history) / len(history)
                ratio = short_avg / long_avg if long_avg > 0 else 1
            else:
                ratio = 1
            
            return {
                "current": current_oi,
                "change": change_pct,
                "ratio": ratio
            }
            
        except Exception as e:
            print(f"[Binance] OI获取失败 {symbol}: {e}")
            return None
    
    def get_market_data(self, symbol):
        """获取市场数据"""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            
            return {
                "price": ticker["last"],
                "volume": ticker.get("quoteVolume", 0),
                "high": ticker.get("high", 0),
                "low": ticker.get("low", 0),
                "change": ticker.get("percentage", 0)
            }
        except:
            return None

# ==================== 信号分析 ====================
class SignalAnalyzer:
    def __init__(self):
        self.signals = []
        self.cooldown = {}
    
    def analyze(self, symbol, funding_rate, oi_data, market_data, taker_ratio):
        """分析信号"""
        # 基本过滤
        if funding_rate > Config.FUNDING_THRESHOLD:  # 不够负
            return None
        
        if market_data["volume"] < Config.VOLUME_THRESHOLD:
            return None
        
        # 计算评分
        score = 0
        
        # 资金费率 (0-40分)
        if funding_rate < -0.003:
            score += 40
        elif funding_rate < -0.002:
            score += 30
        elif funding_rate < -0.0015:
            score += 20
        
        # OI激增 (0-30分)
        if oi_data["ratio"] > 2.5:
            score += 30
        elif oi_data["ratio"] > 2.0:
            score += 20
        elif oi_data["ratio"] > 1.5:
            score += 10
        
        # 买卖比 (0-20分)
        if taker_ratio > 1.5:
            score += 20
        elif taker_ratio > 1.2:
            score += 15
        elif taker_ratio > 1.0:
            score += 10
        
        # 交易量 (0-10分)
        if market_data["volume"] > 20000000:
            score += 10
        elif market_data["volume"] > 10000000:
            score += 7
        elif market_data["volume"] > 5000000:
            score += 5
        
        # 确定信号等级
        if score >= 70:
            level = "STRONG"
            emoji = "🔥🔥🔥"
        elif score >= 50:
            level = "MEDIUM"
            emoji = "🔥🔥"
        elif score >= 30:
            level = "WEAK"
            emoji = "🔥"
        else:
            return None
        
        signal = {
            "symbol": symbol,
            "score": score,
            "level": level,
            "emoji": emoji,
            "funding": funding_rate,
            "oi_ratio": oi_data["ratio"],
            "oi_change": oi_data["change"],
            "taker_ratio": taker_ratio,
            "price": market_data["price"],
            "volume": market_data["volume"],
            "time": datetime.now().isoformat()
        }
        
        # 冷却检查
        current_time = time.time()
        if symbol in self.cooldown:
            last_time = self.cooldown[symbol]
            if current_time - last_time < 7200:  # 2小时
                if score < 80:  # 除非极强信号
                    return None
        
        self.cooldown[symbol] = current_time
        self.signals.append(signal)
        
        return signal

# ==================== 通知管理 ====================
class Notifier:
    def __init__(self):
        self.token = Config.TELEGRAM_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
    
    def send_telegram(self, signal):
        """发送Telegram通知"""
        if not self.token or not self.chat_id:
            return False
        
        message = self.format_message(signal)
        
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200
            
        except:
            return False
    
    def format_message(self, signal):
        """格式化消息"""
        msg = (
            f"{signal['emoji']} *轧空信号: {signal['symbol']}*\n"
            f"▬▬▬▬▬▬▬▬▬▬▬▬\n"
            f"• 评分: `{signal['score']}/100` ({signal['level']})\n"
            f"• 费率: `{signal['funding']:.4%}`\n"
            f"• OI激增: `{signal['oi_ratio']:.2f}x`\n"
            f"• OI变化: `{signal['oi_change']:+.1f}%`\n"
            f"• 买盘比: `{signal['taker_ratio']:.2f}`\n"
            f"• 价格: `${signal['price']:.8f}`\n"
            f"• 交易量: `${signal['volume']/1_000_000:.1f}M`\n"
            f"\n⏰ {datetime.now().strftime('%H:%M:%S')}\n"
            f"▬▬▬▬▬▬▬▬▬▬▬▬"
        )
        
        return msg

# ==================== 主监控器 ====================
class SqueezeMonitor:
    def __init__(self):
        self.coinglass = CoinglassClient(Config.COINGLASS_API_KEY)
        self.binance = BinanceClient()
        self.analyzer = SignalAnalyzer()
        self.notifier = Notifier()
        self.scan_count = 0
    
    def run_scan(self):
        """执行一次扫描"""
        print(f"\n📡 第{self.scan_count + 1}次扫描...")
        
        # 1. 获取负费率币种
        negative_symbols = self.coinglass.get_funding_rates()
        print(f"   负费率币种: {len(negative_symbols)}个")
        
        if not negative_symbols:
            print("   未发现负费率币种")
            return
        
        # 2. 限制数量
        scan_symbols = negative_symbols[:Config.MAX_SYMBOLS]
        signals_found = 0
        
        # 3. 分析每个币种
        for i, symbol_info in enumerate(scan_symbols):
            symbol = symbol_info["symbol"]
            
            if i % 5 == 0:
                print(f"   进度: {i+1}/{len(scan_symbols)}")
            
            try:
                # 获取买卖比
                taker_ratio = self.coinglass.get_taker_ratio(symbol)
                
                # 获取币安数据
                oi_data = self.binance.get_oi_data(symbol)
                market_data = self.binance.get_market_data(symbol)
                
                if not oi_data or not market_data:
                    continue
                
                # 分析信号
                signal = self.analyzer.analyze(
                    symbol=symbol,
                    funding_rate=symbol_info["funding_rate"],
                    oi_data=oi_data,
                    market_data=market_data,
                    taker_ratio=taker_ratio
                )
                
                if signal:
                    signals_found += 1
                    print(f"   ✅ 发现信号: {symbol} ({signal['score']}分)")
                    
                    # 发送通知
                    if signal["score"] >= 50:  # 只发送中等以上信号
                        if self.notifier.send_telegram(signal):
                            print(f"      Telegram通知已发送")
                
                # 避免请求过快
                time.sleep(0.5)
                
            except Exception as e:
                print(f"   ❌ {symbol} 分析失败: {e}")
                continue
        
        # 4. 更新统计
        self.scan_count += 1
        print(f"\n📊 扫描完成")
        print(f"   • 分析币种: {len(scan_symbols)}个")
        print(f"   • 发现信号: {signals_found}个")
        
        # 5. 显示统计
        if self.scan_count % 3 == 0:
            self.show_stats()
    
    def show_stats(self):
        """显示统计"""
        total = len(self.analyzer.signals)
        strong = len([s for s in self.analyzer.signals if s["score"] >= 70])
        medium = len([s for s in self.analyzer.signals if s["score"] >= 50])
        
        print(f"\n{'='*50}")
        print(f"📈 运行统计 (扫描: {self.scan_count}次)")
        print(f"   • 总信号: {total}")
        print(f"   • 强信号: {strong}")
        print(f"   • 中信号: {medium}")
        
        if self.analyzer.signals:
            recent = self.analyzer.signals[-3:]
            print(f"   • 最近信号:")
            for s in recent:
                time_str = datetime.fromisoformat(s["time"]).strftime("%H:%M")
                print(f"      {time_str} {s['symbol']}: {s['score']}分")
        
        print(f"{'='*50}")
    
    def run(self):
        """主运行循环"""
        print("\n🔧 初始化测试...")
        
        # 测试API
        if not self.coinglass.test_api():
            print("❌ Coinglass API连接失败")
            return
        
        print("✅ API连接成功")
        
        # 显示配置
        print(f"\n🎯 监控配置")
        print(f"   • 费率阈值: {Config.FUNDING_THRESHOLD:.3%}")
        print(f"   • OI激增比: {Config.OI_SURGE_RATIO}x")
        print(f"   • 买盘比率: {Config.TAKER_BUY_RATIO}+")
        print(f"   • 扫描间隔: {Config.SCAN_INTERVAL//60}分钟")
        print("="*60)
        
        # 主循环
        while True:
            try:
                self.run_scan()
                
                # 等待下次扫描
                wait_minutes = Config.SCAN_INTERVAL // 60
                next_time = datetime.now() + timedelta(minutes=wait_minutes)
                print(f"\n⏳ 下次扫描: {next_time.strftime('%H:%M')}")
                print(f"   等待 {wait_minutes} 分钟...\n")
                
                time.sleep(Config.SCAN_INTERVAL)
                
            except KeyboardInterrupt:
                print("\n🛑 程序停止")
                break
            except Exception as e:
                print(f"\n❌ 扫描错误: {e}")
                time.sleep(60)

# ==================== 主函数 ====================
if __name__ == "__main__":
    monitor = SqueezeMonitor()
    monitor.run()
