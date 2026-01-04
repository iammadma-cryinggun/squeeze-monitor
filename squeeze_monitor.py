# -*- coding: utf-8 -*-
"""
山寨币轧空监控机器人 - Coinglass新格式版
修复数据格式变化问题
"""

import ccxt
import time
import json
import requests
from datetime import datetime, timedelta
from collections import deque, defaultdict
import os

print("=" * 60)
print("🔥 山寨币轧空监控机器人 - 新格式适配版")
print("📊 适配Coinglass新版API格式")
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
    FUNDING_THRESHOLD = -0.0015  # 调整为-0.15%
    OI_SURGE_RATIO = 2.0
    TAKER_BUY_RATIO = 1.2
    VOLUME_THRESHOLD = 3000000   # 降低到$3M
    
    SCAN_INTERVAL = 180
    MAX_SYMBOLS = 30

# ==================== Coinglass客户端（新格式）====================
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
    
    def get_funding_rates_new_format(self):
        """获取资金费率 - 适配新格式"""
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
                            
                            # 新格式：token_margin_list包含各交易所数据
                            exchange_list = item.get("token_margin_list", [])
                            
                            for exchange_data in exchange_list:
                                exchange_name = exchange_data.get("exchange", "").lower()
                                
                                # 只关注币安
                                if "binance" in exchange_name:
                                    rate = exchange_data.get("funding_rate", 0)
                                    
                                    # 确保是数值
                                    if isinstance(rate, str):
                                        try:
                                            rate = float(rate)
                                        except:
                                            rate = 0
                                    
                                    # 负费率筛选
                                    if rate < Config.FUNDING_THRESHOLD:
                                        full_symbol = f"{symbol}USDT"
                                        
                                        symbols.append({
                                            "symbol": full_symbol,
                                            "funding_rate": rate,
                                            "next_funding": exchange_data.get("next_funding_time", ""),
                                            "exchange": exchange_name
                                        })
                                        
                                        # 找到币安数据后就可以跳出
                                        break
                                        
                        except Exception as e:
                            print(f"[Coinglass] 解析{symbol}失败: {e}")
                            continue
                    
                    print(f"[Coinglass] 发现 {len(symbols)} 个负费率币种")
                    return symbols
                else:
                    print(f"[Coinglass] API错误: {data.get('msg')}")
            
        except Exception as e:
            print(f"[Coinglass] 获取费率失败: {e}")
        
        return []
    
    def get_taker_ratio_new_format(self, symbol: str):
        """获取买卖比率 - 新格式适配"""
        try:
            # 移除USDT后缀
            clean_symbol = symbol.replace("USDT", "")
            
            url = f"{self.base_url}/futures/taker-buy-sell-volume/exchange-list"
            params = {"symbol": clean_symbol}
            
            response = self.session.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if str(data.get("code")) in ["0", "200"] and "data" in data:
                    data_list = data["data"]
                    
                    if isinstance(data_list, list):
                        for item in data_list:
                            exchange = item.get("exchangeName", "").lower()
                            if "binance" in exchange:
                                buy = float(item.get("buyVol", 0))
                                sell = float(item.get("sellVol", 0))
                                
                                if sell > 0:
                                    ratio = buy / sell
                                    return ratio
            
            return 1.0
            
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
            # 有些币种可能没有永续合约
            oi_data = self.exchange.fetch_open_interest(symbol)
            current_oi = oi_data.get("openInterestAmount", 0)
            
            history = self.oi_history[symbol]
            
            # 计算变化
            change_pct = 0
            if len(history) > 0 and history[-1] > 0:
                change_pct = (current_oi - history[-1]) / history[-1] * 100
            
            history.append(current_oi)
            
            # 计算比率
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
            # 静默失败，可能该币种没有合约
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
    
    def test_connection(self):
        """测试连接"""
        try:
            ticker = self.exchange.fetch_ticker('BTCUSDT')
            return True, ticker['last']
        except Exception as e:
            return False, str(e)

# ==================== 主监控器 ====================
class SqueezeMonitor:
    def __init__(self):
        self.coinglass = CoinglassClient(Config.COINGLASS_API_KEY)
        self.binance = BinanceClient()
        self.signals_history = []
        self.cooldown = {}
        self.scan_count = 0
    
    def run_scan(self):
        """执行扫描"""
        print(f"\n📡 第{self.scan_count + 1}次扫描...")
        start_time = time.time()
        
        # 1. 获取负费率币种
        symbols = self.coinglass.get_funding_rates_new_format()
        
        if not symbols:
            print("   当前市场无负费率币种")
            print("   💡 建议: 可以适当放宽费率阈值")
            return
        
        print(f"   分析 {len(symbols)} 个币种...")
        
        # 2. 分析每个币种
        signals_found = 0
        
        for i, symbol_info in enumerate(symbols):
            symbol = symbol_info["symbol"]
            
            if i % 5 == 0:
                print(f"   进度: {i+1}/{len(symbols)}")
            
            try:
                # 获取买卖比
                taker_ratio = self.coinglass.get_taker_ratio_new_format(symbol)
                
                # 获取币安数据
                oi_data = self.binance.get_oi_data(symbol)
                market_data = self.binance.get_market_data(symbol)
                
                if not oi_data or not market_data:
                    continue
                
                # 交易量过滤
                if market_data["volume"] < Config.VOLUME_THRESHOLD:
                    continue
                
                # 检查OI激增
                if oi_data["ratio"] < Config.OI_SURGE_RATIO:
                    continue
                
                # 检查买卖比
                if taker_ratio < Config.TAKER_BUY_RATIO:
                    continue
                
                # 计算综合评分
                score = self.calculate_score(
                    symbol_info["funding_rate"],
                    oi_data["ratio"],
                    taker_ratio,
                    market_data["volume"]
                )
                
                if score >= 50:  # 中等以上信号
                    signals_found += 1
                    
                    signal = {
                        "symbol": symbol,
                        "score": score,
                        "funding": symbol_info["funding_rate"],
                        "oi_ratio": oi_data["ratio"],
                        "taker_ratio": taker_ratio,
                        "price": market_data["price"],
                        "volume": market_data["volume"],
                        "time": datetime.now().isoformat()
                    }
                    
                    self.signals_history.append(signal)
                    
                    print(f"   ✅ {symbol}: {score}分")
                    
                    # 发送通知（如果配置了Telegram）
                    if score >= 70:
                        self.send_alert(signal)
                
                # 避免请求过快
                time.sleep(0.3)
                
            except Exception as e:
                continue
        
        # 3. 完成扫描
        elapsed = time.time() - start_time
        self.scan_count += 1
        
        print(f"\n📊 扫描完成 ({elapsed:.1f}秒)")
        print(f"   • 发现信号: {signals_found}个")
        
        # 4. 显示统计
        if signals_found > 0:
            self.show_recent_signals()
    
    def calculate_score(self, funding_rate, oi_ratio, taker_ratio, volume):
        """计算综合评分"""
        score = 0
        
        # 资金费率 (0-40)
        if funding_rate < -0.003:
            score += 40
        elif funding_rate < -0.002:
            score += 30
        elif funding_rate < -0.0015:
            score += 20
        elif funding_rate < -0.001:
            score += 10
        
        # OI激增 (0-30)
        if oi_ratio > 2.5:
            score += 30
        elif oi_ratio > 2.0:
            score += 20
        elif oi_ratio > 1.5:
            score += 10
        
        # 买卖比 (0-20)
        if taker_ratio > 1.5:
            score += 20
        elif taker_ratio > 1.2:
            score += 15
        elif taker_ratio > 1.0:
            score += 10
        
        # 交易量 (0-10)
        if volume > 20000000:
            score += 10
        elif volume > 10000000:
            score += 7
        elif volume > 5000000:
            score += 5
        
        return score
    
    def send_alert(self, signal):
        """发送警报"""
        if not Config.TELEGRAM_TOKEN or not Config.TELEGRAM_CHAT_ID:
            return
        
        # 冷却检查
        symbol = signal["symbol"]
        current_time = time.time()
        
        if symbol in self.cooldown:
            if current_time - self.cooldown[symbol] < 7200:  # 2小时
                return
        
        self.cooldown[symbol] = current_time
        
        # 格式化消息
        emoji = "🔥🔥🔥" if signal["score"] >= 70 else "🔥🔥"
        
        message = (
            f"{emoji} *轧空信号: {signal['symbol']}*\n"
            f"▬▬▬▬▬▬▬▬▬\n"
            f"• 评分: `{signal['score']}/100`\n"
            f"• 费率: `{signal['funding']:.4%}`\n"
            f"• OI激增: `{signal['oi_ratio']:.2f}x`\n"
            f"• 买盘比: `{signal['taker_ratio']:.2f}`\n"
            f"• 价格: `${signal['price']:.6f}`\n"
            f"• 交易量: `${signal['volume']/1_000_000:.1f}M`\n"
            f"\n⏰ {datetime.now().strftime('%H:%M:%S')}\n"
            f"▬▬▬▬▬▬▬▬▬"
        )
        
        # 发送Telegram
        try:
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
            payload = {
                "chat_id": Config.TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown"
            }
            
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                print(f"      📨 Telegram通知已发送")
            
        except:
            pass
    
    def show_recent_signals(self):
        """显示最近信号"""
        if not self.signals_history:
            return
        
        recent = self.signals_history[-5:]
        
        print(f"\n📋 最近信号:")
        for signal in recent:
            time_str = datetime.fromisoformat(signal["time"]).strftime("%H:%M")
            print(f"   {time_str} | {signal['symbol']}: {signal['score']}分")
    
    def show_stats(self):
        """显示统计"""
        total = len(self.signals_history)
        strong = len([s for s in self.signals_history if s["score"] >= 70])
        medium = len([s for s in self.signals_history if s["score"] >= 50])
        
        print(f"\n{'='*50}")
        print(f"📈 运行统计 (扫描: {self.scan_count}次)")
        print(f"   • 总信号: {total}")
        print(f"   • 强信号: {strong}")
        print(f"   • 中信号: {medium}")
        
        if self.signals_history:
            avg_score = sum(s["score"] for s in self.signals_history) / total
            print(f"   • 平均评分: {avg_score:.1f}")
        
        print(f"{'='*50}")
    
    def run(self):
        """主运行循环"""
        print("\n🔧 初始化测试...")
        
        # 测试币安连接
        success, btc_price = self.binance.test_connection()
        if success:
            print(f"✅ 币安连接成功 | BTC: ${btc_price:.2f}")
        else:
            print(f"❌ 币安连接失败: {btc_price}")
            return
        
        print("\n🎯 监控配置")
        print(f"   • 费率阈值: {Config.FUNDING_THRESHOLD:.3%}")
        print(f"   • OI激增比: {Config.OI_SURGE_RATIO}x")
        print(f"   • 买盘比率: {Config.TAKER_BUY_RATIO}+")
        print(f"   • 交易量过滤: ${Config.VOLUME_THRESHOLD/1_000_000:.1f}M")
        print("="*60)
        
        # 主循环
        while True:
            try:
                self.run_scan()
                
                # 定期显示统计
                if self.scan_count % 3 == 0 and self.scan_count > 0:
                    self.show_stats()
                
                # 等待下次扫描
                wait_seconds = Config.SCAN_INTERVAL
                next_time = datetime.now() + timedelta(seconds=wait_seconds)
                
                print(f"\n⏳ 下次扫描: {next_time.strftime('%H:%M:%S')}")
                print(f"   等待 {wait_seconds//60} 分钟...\n")
                
                time.sleep(wait_seconds)
                
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
