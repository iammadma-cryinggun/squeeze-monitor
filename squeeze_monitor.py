# -*- coding: utf-8 -*-
"""
山寨币轧空监控机器人 - Coinglass混合策略版
策略：Coinglass费率筛选 + 主动买卖比验证 + 币安OI精确计算
API: 04c3a7ffe78d4249968a1886f8e7af1a (初级会员，4位小数精度)
"""

import ccxt
import time
import json
import requests
import asyncio
import aiohttp
from datetime import datetime, timedelta
from collections import deque, defaultdict
import os
import pandas as pd
from typing import Dict, List, Optional, Tuple
import traceback

print("=" * 70)
print("🔥 山寨币轧空监控机器人 v2.0")
print("📊 策略: Coinglass混合验证 + 多维度信号")
print(f"🕐 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 70)

# ==================== 配置区域 ====================
class Config:
    # Coinglass API配置
    COINGLASS_API_KEY = "04c3a7ffe78d4249968a1886f8e7af1a"
    COINGLASS_BASE_URL = "https://open-api-v4.coinglass.com/api"
    
    # Telegram通知配置
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
    
    # 币安交易所配置
    BINANCE_CONFIG = {
        'enableRateLimit': True,
        'options': {'defaultType': 'future'},
        'timeout': 15000,
        'rateLimit': 1200,
    }
    
    # 策略参数
    FUNDING_THRESHOLD = -0.0018  # 资金费率阈值 -0.18%
    OI_SURGE_RATIO = 2.0         # OI激增倍数
    TAKER_BUY_RATIO = 1.2        # 主动买盘比率
    VOLUME_THRESHOLD = 5000000   # 最小交易量 $5M
    
    # 扫描配置
    SCAN_INTERVAL = 180  # 3分钟
    MAX_SYMBOLS = 50     # 最多监控50个币种
    MAX_RETRIES = 3      # API重试次数
    
    # 信号评分权重
    WEIGHTS = {
        'funding_rate': 0.40,   # 资金费率权重
        'oi_surge': 0.30,       # OI激增权重
        'taker_ratio': 0.20,    # 买卖比权重
        'volume': 0.10,         # 交易量权重
    }

# ==================== Coinglass API客户端 ====================
class CoinglassClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = Config.COINGLASS_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "accept": "application/json",
            "coinglassSecret": api_key,
            "User-Agent": "Mozilla/5.0"
        })
    
    def get_funding_rates(self) -> List[Dict]:
        """获取全市场资金费率数据"""
        try:
            url = f"{self.base_url}/futures/funding-rate/exchange-list"
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                symbols = []
                
                if data.get("code") == "200" and "data" in data:
                    for item in data["data"]:
                        try:
                            # 解析资金费率
                            rate_str = str(item.get("rate", "0")).replace("%", "")
                            rate = float(rate_str) / 100 if rate_str else 0
                            
                            # 只关注负费率且是币安的合约
                            if (rate < 0 and 
                                item.get("exchangeName", "").lower() == "binance" and
                                item.get("symbol", "").endswith("USDT")):
                                
                                symbols.append({
                                    "symbol": item["symbol"],
                                    "funding_rate": rate,
                                    "next_funding": item.get("nextFundingTime", ""),
                                    "exchange": item["exchangeName"]
                                })
                        except:
                            continue
                    
                    print(f"[Coinglass] 获取到 {len(symbols)} 个负费率币种")
                    return symbols
                else:
                    print(f"[Coinglass] API响应异常: {data.get('msg', 'Unknown error')}")
            else:
                print(f"[Coinglass] 请求失败: HTTP {response.status_code}")
                
        except Exception as e:
            print(f"[Coinglass] 获取资金费率失败: {e}")
        
        return []
    
    def get_taker_buy_sell_ratio(self, symbol: str, period: str = "h4") -> Optional[float]:
        """获取主动买卖比率"""
        try:
            url = f"{self.base_url}/futures/taker-buy-sell-volume/exchange-list"
            params = {"symbol": symbol, "range": period}
            
            response = self.session.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("code") == "200" and "data" in data:
                    # 解析买卖比数据，取最新值
                    for exchange_data in data["data"]:
                        if exchange_data.get("exchangeName", "").lower() == "binance":
                            buy_vol = float(exchange_data.get("buyVol", 0))
                            sell_vol = float(exchange_data.get("sellVol", 0))
                            
                            if sell_vol > 0:
                                ratio = buy_vol / sell_vol
                                return ratio
            return None
            
        except Exception as e:
            print(f"[Coinglass] 获取买卖比失败 {symbol}: {e}")
            return None

# ==================== 币安客户端 ====================
class BinanceClient:
    def __init__(self):
        self.exchange = ccxt.binance(Config.BINANCE_CONFIG)
        self.oi_history = defaultdict(lambda: deque(maxlen=20))
        self.price_history = defaultdict(lambda: deque(maxlen=50))
    
    def get_precise_oi(self, symbol: str) -> Optional[float]:
        """获取精确的持仓量数据（无精度损失）"""
        try:
            oi_data = self.exchange.fetch_open_interest(symbol)
            oi = oi_data.get("openInterestAmount", 0)
            
            # 更新历史记录
            if symbol in self.oi_history:
                prev_oi = self.oi_history[symbol][-1] if self.oi_history[symbol] else 0
                if prev_oi > 0:
                    oi_change = (oi - prev_oi) / prev_oi * 100
                else:
                    oi_change = 0
            else:
                oi_change = 0
            
            self.oi_history[symbol].append(oi)
            return {"oi": oi, "change": oi_change}
            
        except Exception as e:
            print(f"[Binance] 获取OI失败 {symbol}: {e}")
            return None
    
    def get_market_data(self, symbol: str) -> Optional[Dict]:
        """获取综合市场数据"""
        try:
            # 获取ticker数据
            ticker = self.exchange.fetch_ticker(symbol)
            
            # 获取K线计算波动率
            ohlcv = self.exchange.fetch_ohlcv(symbol, '5m', limit=20)
            volatility = 0
            
            if len(ohlcv) >= 10:
                closes = [c[4] for c in ohlcv[-10:]]
                returns = [(closes[i] - closes[i-1]) / closes[i-1] 
                          for i in range(1, len(closes))]
                if returns:
                    volatility = pd.Series(returns).std() * 100
            
            # 更新价格历史
            self.price_history[symbol].append(ticker['last'])
            
            return {
                "price": ticker['last'],
                "volume_24h": ticker.get('quoteVolume', 0),
                "high_24h": ticker.get('high', 0),
                "low_24h": ticker.get('low', 0),
                "change_24h": ticker.get('percentage', 0),
                "volatility": volatility,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            print(f"[Binance] 获取市场数据失败 {symbol}: {e}")
            return None

# ==================== 信号分析引擎 ====================
class SignalAnalyzer:
    def __init__(self):
        self.signals_history = []
        self.alert_cooldown = {}
    
    def calculate_squeeze_score(self, data: Dict) -> Dict:
        """计算轧空综合评分"""
        score = 0
        details = {}
        
        # 1. 资金费率评分（0-40分）
        funding_rate = data.get('funding_rate', 0)
        if funding_rate < -0.003:
            score += 40
            details['funding'] = "极度负值(40分)"
        elif funding_rate < -0.002:
            score += 30
            details['funding'] = "高度负值(30分)"
        elif funding_rate < -0.0015:
            score += 20
            details['funding'] = "中度负值(20分)"
        elif funding_rate < -0.001:
            score += 10
            details['funding'] = "轻度负值(10分)"
        
        # 2. OI激增评分（0-30分）
        oi_ratio = data.get('oi_ratio', 1)
        oi_change = data.get('oi_change', 0)
        
        if oi_ratio > 2.5:
            score += 30
            details['oi'] = f"异常激增({oi_ratio:.2f}x, 30分)"
        elif oi_ratio > 2.0:
            score += 20
            details['oi'] = f"显著激增({oi_ratio:.2f}x, 20分)"
        elif oi_ratio > 1.5:
            score += 10
            details['oi'] = f"温和增长({oi_ratio:.2f}x, 10分)"
        
        if oi_change > 30:
            score += 10
            details['oi_change'] = f"快速增长(+{oi_change:.1f}%)"
        
        # 3. 主动买卖比评分（0-20分）
        taker_ratio = data.get('taker_ratio', 1)
        if taker_ratio > 1.5:
            score += 20
            details['taker'] = f"强烈买盘({taker_ratio:.2f}, 20分)"
        elif taker_ratio > 1.2:
            score += 15
            details['taker'] = f"积极买盘({taker_ratio:.2f}, 15分)"
        elif taker_ratio > 1.0:
            score += 10
            details['taker'] = f"买盘占优({taker_ratio:.2f}, 10分)"
        
        # 4. 交易量评分（0-10分）
        volume = data.get('volume_24h', 0)
        if volume > 50000000:  # 50M
            score += 10
            details['volume'] = "高流动性(10分)"
        elif volume > 10000000:  # 10M
            score += 7
            details['volume'] = "良好流动性(7分)"
        elif volume > 5000000:   # 5M
            score += 5
            details['volume'] = "基本流动性(5分)"
        
        # 确定信号等级
        if score >= 70:
            signal_level = "STRONG"
            emoji = "🔥🔥🔥"
        elif score >= 50:
            signal_level = "MEDIUM"
            emoji = "🔥🔥"
        elif score >= 30:
            signal_level = "WEAK"
            emoji = "🔥"
        else:
            signal_level = "NO_SIGNAL"
            emoji = "⚪"
        
        return {
            "total_score": score,
            "signal_level": signal_level,
            "emoji": emoji,
            "details": details,
            "timestamp": datetime.now().isoformat()
        }
    
    def should_alert(self, symbol: str, score: int) -> bool:
        """检查是否应该发送警报"""
        current_time = time.time()
        
        # 冷却期检查
        if symbol in self.alert_cooldown:
            last_alert = self.alert_cooldown[symbol]
            if current_time - last_alert < 7200:  # 2小时冷却
                return False
        
        # 只有强信号才立即警报
        if score >= 70:
            self.alert_cooldown[symbol] = current_time
            return True
        elif score >= 50:
            # 中等信号每4小时只提醒一次
            if symbol not in self.alert_cooldown or current_time - self.alert_cooldown[symbol] > 14400:
                self.alert_cooldown[symbol] = current_time
                return True
        
        return False

# ==================== 通知管理器 ====================
class NotificationManager:
    def __init__(self):
        self.telegram_token = Config.TELEGRAM_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        
    def send_telegram(self, message: str):
        """发送Telegram通知"""
        if not self.telegram_token or not self.chat_id:
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            }
            
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200
            
        except Exception as e:
            print(f"[Telegram] 发送失败: {e}")
            return False
    
    def format_squeeze_alert(self, symbol: str, data: Dict, analysis: Dict) -> str:
        """格式化轧空警报消息"""
        score = analysis["total_score"]
        level = analysis["signal_level"]
        emoji = analysis["emoji"]
        details = analysis["details"]
        
        # 基础信息
        message = f"{emoji} *轧空信号警报 - {symbol}*\n"
        message += f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        message += f"• **综合评分**: `{score}/100` ({level})\n"
        message += f"• **资金费率**: `{data['funding_rate']:.4%}`\n"
        
        # 详细评分
        for key, desc in details.items():
            message += f"• **{key.upper()}**: {desc}\n"
        
        # 市场数据
        message += f"\n📊 *市场数据:*\n"
        message += f"• 价格: `${data['price']:.6f}`\n"
        if 'volume_24h' in data:
            message += f"• 24h交易量: `${data['volume_24h']/1_000_000:.1f}M`\n"
        if 'volatility' in data:
            message += f"• 5m波动率: `{data['volatility']:.2f}%`\n"
        
        # OI数据
        if 'oi_ratio' in data:
            message += f"• OI激增比: `{data['oi_ratio']:.2f}x`\n"
        if 'oi_change' in data:
            message += f"• OI变化: `{data['oi_change']:+.1f}%`\n"
        
        # 买卖比
        if 'taker_ratio' in data:
            message += f"• 主动买盘比: `{data['taker_ratio']:.2f}`\n"
        
        # 操作建议
        message += f"\n⚡ *操作建议:*\n"
        
        if score >= 70:
            message += f"• **信号强度**: 强烈轧空信号\n"
            message += f"• **入场时机**: 突破阻力或放量上涨\n"
            message += f"• **止损**: -2% (严格风控)\n"
            message += f"• **目标**: +8% ~ +20% (分批止盈)\n"
            message += f"• **仓位**: 可适当增加仓位\n"
        elif score >= 50:
            message += f"• **信号强度**: 中等轧空信号\n"
            message += f"• **入场时机**: 等待确认突破\n"
            message += f"• **止损**: -3%\n"
            message += f"• **目标**: +5% ~ +12%\n"
            message += f"• **仓位**: 轻仓试单\n"
        else:
            message += f"• **信号强度**: 弱信号，观察为主\n"
            message += f"• **建议**: 等待更强信号确认\n"
        
        message += f"\n⏰ *时间*: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        message += f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
        
        return message

# ==================== 主监控引擎 ====================
class SqueezeMonitor:
    def __init__(self):
        self.coinglass = CoinglassClient(Config.COINGLASS_API_KEY)
        self.binance = BinanceClient()
        self.analyzer = SignalAnalyzer()
        self.notifier = NotificationManager()
        self.monitoring_symbols = set()
        self.scan_count = 0
        
    async def analyze_symbol(self, symbol_info: Dict) -> Optional[Dict]:
        """分析单个币种的轧空潜力"""
        symbol = symbol_info["symbol"]
        
        try:
            print(f"  🔍 分析 {symbol}...")
            
            # 1. 获取买卖比数据
            taker_ratio = self.coinglass.get_taker_buy_sell_ratio(symbol, "h4")
            
            # 2. 获取币安精确OI数据
            oi_data = self.binance.get_precise_oi(symbol)
            if not oi_data:
                return None
            
            # 3. 获取市场数据
            market_data = self.binance.get_market_data(symbol)
            if not market_data:
                return None
            
            # 4. 交易量过滤
            if market_data["volume_24h"] < Config.VOLUME_THRESHOLD:
                return None
            
            # 5. 计算OI历史比率
            oi_history = list(self.binance.oi_history[symbol])
            if len(oi_history) >= 10:
                short_avg = sum(oi_history[-5:]) / 5 if len(oi_history) >= 5 else oi_data["oi"]
                long_avg = sum(oi_history[-10:]) / 10 if len(oi_history) >= 10 else oi_data["oi"]
                oi_ratio = short_avg / long_avg if long_avg > 0 else 1
            else:
                oi_ratio = 1
            
            # 6. 组合数据
            analysis_data = {
                "symbol": symbol,
                "funding_rate": symbol_info["funding_rate"],
                "taker_ratio": taker_ratio or 1.0,
                "oi": oi_data["oi"],
                "oi_change": oi_data["change"],
                "oi_ratio": oi_ratio,
                "price": market_data["price"],
                "volume_24h": market_data["volume_24h"],
                "volatility": market_data["volatility"],
            }
            
            # 7. 计算综合评分
            score_result = self.analyzer.calculate_squeeze_score(analysis_data)
            
            if score_result["signal_level"] != "NO_SIGNAL":
                analysis_data.update(score_result)
                return analysis_data
                
        except Exception as e:
            print(f"  分析{symbol}时出错: {e}")
        
        return None
    
    async def scan_cycle(self):
        """执行一次完整的扫描周期"""
        print(f"\n📡 第{self.scan_count + 1}次扫描开始...")
        start_time = time.time()
        
        # 1. 从Coinglass获取负费率币种
        negative_funding = self.coinglass.get_funding_rates()
        
        if not negative_funding:
            print("⚠️  未获取到负费率币种，跳过本次扫描")
            return
        
        # 2. 筛选前N个币种
        scan_symbols = negative_funding[:Config.MAX_SYMBOLS]
        print(f"📊 筛选出 {len(scan_symbols)} 个候选币种")
        
        # 3. 并行分析所有币种
        tasks = []
        for symbol_info in scan_symbols:
            tasks.append(self.analyze_symbol(symbol_info))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 4. 处理分析结果
        valid_signals = []
        for result in results:
            if isinstance(result, dict):
                valid_signals.append(result)
            elif isinstance(result, Exception):
                continue
        
        # 5. 发送警报
        alert_count = 0
        for signal_data in valid_signals:
            symbol = signal_data["symbol"]
            score = signal_data["total_score"]
            
            if self.analyzer.should_alert(symbol, score):
                # 发送Telegram警报
                alert_msg = self.notifier.format_squeeze_alert(
                    symbol, signal_data, signal_data
                )
                
                if self.notifier.send_telegram(alert_msg):
                    print(f"   ✅ 已发送 {symbol} 警报 (评分: {score})")
                    alert_count += 1
                
                # 添加到历史记录
                self.analyzer.signals_history.append({
                    "time": datetime.now().isoformat(),
                    "symbol": symbol,
                    "score": score,
                    "data": signal_data
                })
        
        # 6. 更新统计
        elapsed = time.time() - start_time
        self.scan_count += 1
        
        print(f"\n📈 扫描完成!")
        print(f"   • 耗时: {elapsed:.1f}秒")
        print(f"   • 分析币种: {len(scan_symbols)}个")
        print(f"   • 有效信号: {len(valid_signals)}个")
        print(f"   • 发送警报: {alert_count}个")
        
        # 7. 显示统计摘要
        if valid_signals:
            print(f"\n🏆 本次扫描发现信号:")
            for signal in valid_signals[:5]:  # 显示前5个
                print(f"   • {signal['symbol']}: {signal['total_score']}分 ({signal['signal_level']})")
        
        return valid_signals
    
    def show_statistics(self):
        """显示运行统计"""
        if not self.analyzer.signals_history:
            return
        
        total_signals = len(self.analyzer.signals_history)
        strong_signals = len([s for s in self.analyzer.signals_history if s["score"] >= 70])
        medium_signals = len([s for s in self.analyzer.signals_history if s["score"] >= 50])
        
        print(f"\n{'='*60}")
        print(f"📊 运行统计 (总扫描: {self.scan_count}次)")
        print(f"{'='*60}")
        print(f"• 总信号数: {total_signals}")
        print(f"• 强信号数: {strong_signals}")
        print(f"• 中信号数: {medium_signals}")
        
        if total_signals > 0:
            avg_score = sum(s["score"] for s in self.analyzer.signals_history) / total_signals
            print(f"• 平均评分: {avg_score:.1f}")
        
        # 显示最近信号
        if self.analyzer.signals_history:
            recent = self.analyzer.signals_history[-3:]
            print(f"\n🕐 最近信号:")
            for signal in recent:
                time_str = datetime.fromisoformat(signal["time"]).strftime("%H:%M")
                print(f"   {time_str} | {signal['symbol']}: {signal['score']}分")
        
        print(f"{'='*60}")
    
    async def run(self):
        """主运行循环"""
        print("\n🎯 监控策略配置:")
        print(f"   • 扫描间隔: {Config.SCAN_INTERVAL//60}分钟")
        print(f"   • 费率阈值: {Config.FUNDING_THRESHOLD:.3%}")
        print(f"   • OI激增比: {Config.OI_SURGE_RATIO}x")
        print(f"   • 买盘比率: {Config.TAKER_BUY_RATIO}+")
        print(f"   • 交易量过滤: ${Config.VOLUME_THRESHOLD/1_000_000:.0f}M")
        print(f"   • 最大监控数: {Config.MAX_SYMBOLS}")
        print("="*70)
        
        # 初始测试
        print("\n🔧 初始化测试...")
        test_symbols = self.coinglass.get_funding_rates()
        if not test_symbols:
            print("❌ Coinglass API测试失败，请检查API Key")
            return
        
        print(f"✅ Coinglass API连接成功")
        print(f"✅ 检测到 {len(test_symbols)} 个负费率币种")
        
        # 主循环
        cycle_count = 0
        while True:
            try:
                cycle_count += 1
                
                # 执行扫描
                await self.scan_cycle()
                
                # 显示统计（每5次扫描）
                if cycle_count % 5 == 0:
                    self.show_statistics()
                
                # 计算等待时间
                wait_time = Config.SCAN_INTERVAL
                next_scan = datetime.now() + timedelta(seconds=wait_time)
                
                print(f"\n⏳ 下次扫描: {next_scan.strftime('%H:%M:%S')}")
                print(f"   (等待 {wait_time//60} 分钟)")
                
                # 等待期间保持活跃
                for i in range(wait_time // 30):
                    await asyncio.sleep(30)
                    if i % 2 == 0:
                        print(f"   💓 保持活跃... ({i//2 + 1}分)")
                
            except KeyboardInterrupt:
                print("\n\n🛑 用户中断，程序停止")
                break
            except Exception as e:
                print(f"\n❌ 扫描周期异常: {e}")
                traceback.print_exc()
                await asyncio.sleep(60)

# ==================== 主函数 ====================
async def main():
    """主函数"""
    # 创建监控器实例
    monitor = SqueezeMonitor()
    
    # 运行监控
    await monitor.run()

if __name__ == "__main__":
    # 运行异步主函数
    asyncio.run(main())
