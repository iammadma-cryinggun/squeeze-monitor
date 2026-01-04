# -*- coding: utf-8 -*-
"""
山寨币轧空监控机器人
策略逻辑：捕捉庄家通过逼空散户退出的机会

核心信号：
1. 极端负费率（<-0.1%）-> 空头过多，庄家控盘
2. OI异常激增（短期/长期 > 2倍）-> 庄家建多头
3. 价格突破阻力位 -> 触发空头清算

作者：AI Assistant
日期：2026-01-03
"""

import ccxt
import time
import pandas as pd
import requests
from collections import deque
from datetime import datetime
import json

# ==================== 配置区 ====================
import os

# 🌐 云端环境检测（自动禁用代理）
IS_CLOUD = os.environ.get('ZEABUR_DEPLOYMENT', '').lower() == 'true' or \
           os.environ.get('VERCEL', '') != '' or \
           os.environ.get('DYNO', '') != ''

# 代理配置（本地需要代理，云端自动禁用）
if IS_CLOUD:
    # 云端环境：禁用代理
    PROXY = None
    print("[INFO] 检测到云端环境，已自动禁用代理")
else:
    # 本地环境：使用代理
    PROXY = os.environ.get('PROXY', 'http://127.0.0.1:15236')
    print(f"[INFO] 本地环境，使用代理: {PROXY}")

# 禁用SSL警告（解决代理SSL握手问题）
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 构建币安配置
BINANCE_CONFIG = {
    'enableRateLimit': True,
    'options': {'defaultType': 'future'},
    'timeout': 30000,  # 30秒超时
    'verify': False,  # 禁用SSL验证（解决代理SSL握手问题）
    'enableRateLimit': True
}

# 只在非云端环境添加代理配置
if PROXY:
    BINANCE_CONFIG['proxies'] = {
        'http': PROXY,
        'https': PROXY,
    }

# Telegram 配置
TELEGRAM_TOKEN = "8216072079:AAFqJjOE81siaDQsHbFIBKBKfWh7SnTRuzI"
TELEGRAM_CHAT_ID = "838429342"
WECHAT_SCKEY = "SCT307134TCw1AtdGtadVA7CZhRklB0ptp"

# 策略参数
FUNDING_THRESHOLD = -0.001  # 资金费率低于 -0.1%
OI_SURGE_RATIO = 2.0        # OI 短期均值是长期均值的 2 倍
SHORT_WINDOW = 3            # 短期窗口（最近3次，约15分钟）
LONG_WINDOW = 10            # 长期窗口（最近10次，约50分钟）
SCAN_INTERVAL = 600         # 扫描间隔（10分钟，平衡覆盖率和API压力）

# 过滤条件
MIN_VOLUME_24H = 10_000_000   # 24h最小交易量 $10M
MIN_PRICE = 0.001              # 最小价格（过滤极小币种）
MAX_SYMBOLS_TO_SCAN = 9999     # 扫描所有符合条件的币种

# 数据存储
oi_history = {}
last_alert_time = {}  # 避免重复警报
SIGNALS_FILE = "squeeze_signals.json"  # 信号记录文件（用于统计胜率）

# 胜率统计
signals_db = []  # 存储所有信号

def load_signals():
    """加载历史信号记录"""
    global signals_db
    try:
        with open(SIGNALS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            signals_db = data.get('signals', [])
            print(f"   [OK] 已加载 {len(signals_db)} 条历史信号")
    except FileNotFoundError:
        signals_db = []
        print(f"   [INFO] 首次运行，将创建新记录")
    except Exception as e:
        print(f"   [WARN] 加载信号记录失败: {e}")
        signals_db = []

def save_signals():
    """保存信号记录"""
    try:
        data = {
            'signals': signals_db,
            'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        with open(SIGNALS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"   [WARN] 保存信号记录失败: {e}")

def record_signal(symbol, funding_rate, oi_ratio, mark_price):
    """记录新信号"""
    signal = {
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'symbol': symbol,
        'funding_rate': funding_rate,
        'oi_ratio': oi_ratio,
        'mark_price': mark_price,
        'status': 'active',  # active / tp1_hit / stopped
        'peak_price': mark_price,
        'peak_profit_pct': 0.0,
        'final_price': 0.0,
        'final_profit_pct': 0.0
    }
    signals_db.append(signal)
    save_signals()

def check_existing_signals(current_prices):
    """检查现有信号的价格表现"""
    global signals_db
    updated = False

    for signal in signals_db:
        if signal['status'] != 'active':
            continue

        symbol = signal['symbol']
        if symbol not in current_prices:
            continue

        current_price = current_prices[symbol]
        entry_price = signal['mark_price']

        # 计算盈亏
        profit_pct = (current_price - entry_price) / entry_price * 100

        # 更新峰值
        if profit_pct > signal['peak_profit_pct']:
            signal['peak_profit_pct'] = profit_pct
            signal['peak_price'] = current_price
            updated = True

        # 检查是否达到目标
        if profit_pct >= 10.0:  # TP2: +10%
            signal['status'] = 'tp2_hit'
            signal['final_price'] = current_price
            signal['final_profit_pct'] = profit_pct
            updated = True

            send_alert(
                f"[SUCCESS] *轧空信号止盈: {symbol}*\n\n"
                f"入场价格: `${entry_price:.4f}`\n"
                f"出场价格: `${current_price:.4f}`\n"
                f"最终盈利: `{profit_pct:+.2f}%`\n"
                f"峰值盈利: `{signal['peak_profit_pct']:+.2f}%`\n\n"
                f"[STATS] 这是第 {len([s for s in signals_db if s['status'] in ['tp1_hit', 'tp2_hit']])} 个成功信号",
                "success"
            )

        elif profit_pct >= 5.0:  # TP1: +5%
            if signal['status'] == 'active':
                signal['status'] = 'tp1_hit'
                updated = True

        elif profit_pct <= -3.0:  # 止损: -3%
            signal['status'] = 'stopped'
            signal['final_price'] = current_price
            signal['final_profit_pct'] = profit_pct
            updated = True

            send_alert(
                f"[STOP] *轧空信号止损: {symbol}*\n\n"
                f"入场价格: `${entry_price:.4f}`\n"
                f"出场价格: `${current_price:.4f}`\n"
                f"最终亏损: `{profit_pct:+.2f}%`\n\n"
                f"[STATS] 失败信号数: {len([s for s in signals_db if s['status'] == 'stopped'])}",
                "warning"
            )

    if updated:
        save_signals()

def show_statistics():
    """显示胜率统计"""
    if len(signals_db) == 0:
        return

    active = [s for s in signals_db if s['status'] == 'active']
    success = [s for s in signals_db if s['status'] in ['tp1_hit', 'tp2_hit']]
    failed = [s for s in signals_db if s['status'] == 'stopped']

    win_rate = len(success) / (len(success) + len(failed)) * 100 if (len(success) + len(failed)) > 0 else 0

    avg_profit = 0.0
    if success:
        avg_profit = sum(s['final_profit_pct'] for s in success) / len(success)

    avg_loss = 0.0
    if failed:
        avg_loss = sum(s['final_profit_pct'] for s in failed) / len(failed)

    print(f"\n{'='*80}")
    print(f"[STATS] 胜率统计报告")
    print(f"{'='*80}")
    print(f"总信号数: {len(signals_db)}")
    print(f"活跃中: {len(active)}")
    print(f"已止盈: {len(success)}")
    print(f"已止损: {len(failed)}")
    print(f"\n胜率: {win_rate:.1f}%")
    print(f"平均盈利: {avg_profit:+.2f}%")
    print(f"平均亏损: {avg_loss:+.2f}%")

    if active:
        print(f"\n[ACTIVE] 活跃信号:")
        for s in active[-5:]:  # 只显示最近5个
            print(f"   {s['symbol']} | {s['time']} | 峰值: {s['peak_profit_pct']:+.2f}%")

    print(f"{'='*80}\n")


def send_telegram_message(message, alert_type="warning"):
    """发送 Telegram 警报"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram发送失败: {e}")

def send_wechat_message(message):
    """发送微信警报（Server酱）"""
    url = f"https://sctapi.ftqq.com/{WECHAT_SCKEY}.send"
    payload = {
        "title": "🚨 山寨币轧空预警",
        "desp": message
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"微信发送失败: {e}")

def send_alert(message, alert_type="warning"):
    """发送所有渠道警报"""
    print(f"\n{'='*80}")
    print(f"[ALERT] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(message)
    print(f"{'='*80}\n")

    send_telegram_message(message, alert_type)
    send_wechat_message(message)

def fetch_market_data(exchange):
    """获取市场数据（不再限制交易量）"""
    try:
        # 获取所有USDT合约的24h统计数据
        ticker = exchange.fetch_tickers(['FUTURES/USDT'])

        # 过滤
        filtered = []
        for symbol, data in ticker.items():
            if not symbol.endswith('/USDT'):
                continue

            # 过滤条件
            if data['quoteVolume'] < MIN_VOLUME_24H:  # 24h交易量过滤
                continue
            if data['last'] < MIN_PRICE:
                continue

            filtered.append({
                'symbol': symbol,
                'volume': data['quoteVolume'],
                'price': data['last'],
                'change': data['percentage']
            })

        return filtered  # 返回所有符合条件的币种

    except Exception as e:
        print(f"获取市场数据失败: {e}")
        return []

def fetch_data():
    """获取币安合约的费率和持仓量数据"""
    exchange = ccxt.binance(BINANCE_CONFIG)

    try:
        # 1. 获取所有交易对的资金费率
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 开始扫描...")

        funding_rates = exchange.fetch_funding_rates()

        # 2. 获取市场数据（所有符合条件的币种）
        all_symbols = fetch_market_data(exchange)
        symbol_list = [s['symbol'] for s in all_symbols]
        current_prices = {s['symbol']: s['price'] for s in all_symbols}

        print(f"   [OK] 监控币种数: {len(symbol_list)}")

        # 3. 检查现有信号的表现
        check_existing_signals(current_prices)

        alert_count = 0
        scan_count = 0

        # 4. 逐个分析新信号
        for symbol, data in funding_rates.items():
            if symbol not in symbol_list:
                continue

            scan_count += 1

            funding_rate = data.get('fundingRate', 0)
            mark_price = data.get('markPrice', 0)

            # 获取持仓量
            try:
                oi_data = exchange.fetch_open_interest(symbol)
                current_oi = oi_data['openInterestAmount']
            except:
                continue

            # 更新历史记录
            if symbol not in oi_history:
                oi_history[symbol] = deque(maxlen=LONG_WINDOW)
            oi_history[symbol].append(current_oi)

            # 5. 执行策略逻辑判断
            if check_strategy(symbol, funding_rate, mark_price):
                alert_count += 1

        print(f"   [OK] 扫描完成: {scan_count} 个币种")
        if alert_count > 0:
            print(f"   [ALERT] 发现 {alert_count} 个新轧空信号")
        else:
            print(f"   [OK] 未发现轧空信号")

        # 每6小时显示一次统计报告
        current_hour = datetime.now().hour
        if current_hour % 6 == 0 and scan_count > 0:
            show_statistics()

    except Exception as e:
        print(f"数据获取异常: {e}")
        import traceback
        traceback.print_exc()

def check_strategy(symbol, funding_rate, mark_price):
    """检查是否满足轧空信号"""
    history = list(oi_history[symbol])

    # 数据不足，跳过
    if len(history) < LONG_WINDOW:
        return False

    # 计算 OI 均值
    short_term_oi = sum(history[-SHORT_WINDOW:]) / SHORT_WINDOW
    long_term_oi = sum(history) / LONG_WINDOW

    # 避免 ZeroDivisionError
    if long_term_oi == 0:
        return False

    oi_ratio = short_term_oi / long_term_oi

    # 条件判断
    cond1 = funding_rate <= FUNDING_THRESHOLD  # 极端负费率
    cond2 = oi_ratio >= OI_SURGE_RATIO         # OI 激增

    if cond1 and cond2:
        # 避免重复警报（1小时内不重复）
        current_time = time.time()
        if symbol in last_alert_time:
            if current_time - last_alert_time[symbol] < 3600:
                return False

        last_alert_time[symbol] = current_time

        # ✅ 记录信号（用于统计胜率）
        record_signal(symbol, funding_rate, oi_ratio, mark_price)

        # 构建警报消息
        msg = (
            f"[SQUEEZE] *山寨币轧空预警: {symbol}*\n\n"
            f"[METRICS] 核心指标:\n"
            f"● 资金费率: `{funding_rate:.4%}` "
            f"{'[极端负值]' if funding_rate < -0.001 else ''}\n"
            f"● OI 激增: `{oi_ratio:.2f}x` "
            f"{'[异常]' if oi_ratio >= 2.0 else ''}\n"
            f"● 当前 OI: `{history[-1]:,.0f}`\n"
            f"● 标记价格: `${mark_price:.4f}`\n\n"
            f"[LOGIC] 策略逻辑:\n"
            f"1. 极端负费率 -> 空头过多，庄家控盘\n"
            f"2. OI 激增 -> 庄家建多头头寸\n"
            f"3. 潜在轧空 -> 突破阻力位触发空头清算\n\n"
            f"[ACTION] 操作建议:\n"
            f"• 结合技术分析确认入场点\n"
            f"• 设置止损 -3%\n"
            f"• 目标盈利 +5% ~ +10%\n"
            f"• 注意快速行情，及时止盈"
        )

        send_alert(msg, "warning")
        return True

    return False

def main():
    """主循环"""
    # 加载历史信号
    load_signals()

    print("="*80)
    print("[START] 山寨币轧空监控机器人已启动")
    print("="*80)
    print(f"[CONFIG] 监控配置:")
    print(f"   - 扫描频率: 每 {SCAN_INTERVAL//60} 分钟")
    print(f"   - 监控范围: 所有USDT合约（24h交易量>${MIN_VOLUME_24H/1_000_000:.0f}M）")
    print(f"   - 费率阈值: {FUNDING_THRESHOLD:.1%}")
    print(f"   - OI 激增倍数: {OI_SURGE_RATIO}x")
    print(f"   - 止盈: TP1 +5%, TP2 +10%")
    print(f"   - 止损: -3%")
    print(f"\n[STATS] 胜率统计:")
    if len(signals_db) > 0:
        show_statistics()
    else:
        print(f"   [INFO] 首次运行，将记录所有信号")
    print("="*80)

    # 启动通知
    send_alert("[START] 山寨币轧空监控机器人已启动\n\n开始扫描市场...", "info")

    while True:
        try:
            fetch_data()
            print(f"[TIME] 下次扫描: {SCAN_INTERVAL//60} 分钟后\n")
            time.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            print("\n\n[WARN] 用户中断，程序停止")
            break
        except Exception as e:
            print(f"\n[ERROR] 程序异常: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(60)  # 异常后等待1分钟再重试

if __name__ == "__main__":
    main()
