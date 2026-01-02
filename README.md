# 山寨币轧空监控机器人 - Zeabur 部署指南

## 📋 文件清单

```
C:\Users\Martin\Downloads\机器人\轧空\
├── squeeze_monitor.py      # 主程序
├── requirements.txt        # Python 依赖
├── Dockerfile              # Docker 配置
├── zeabur.yaml            # Zeabur 配置
└── README.md              # 部署指南（本文件）
```

---

## 🚀 Zeabur 部署步骤

### 1. 注册 Zeabur 账号

1. 访问：https://zeabur.com
2. 使用 GitHub 账号登录
3. 创建新项目

### 2. 创建服务

**方式一：使用 Zeabur CLI（推荐）**

```bash
# 安装 Zeabur CLI
npm install -g @zeabur/cli

# 登录
zeabur login

# 创建项目
zeabur init

# 部署
cd "C:\Users\Martin\Downloads\机器人\轧空"
zeabur deploy
```

**方式二：使用 GitHub 集成**

1. 将代码上传到 GitHub 仓库
2. 在 Zeabur 中导入该仓库
3. Zeabur 会自动识别 `zeabur.yaml` 配置
4. 点击部署

### 3. 配置环境变量

在 Zeabur 控制台中设置以下环境变量：

**必需配置：**
- `PROXY`: 你的代理服务器地址（例如：`http://123.45.67.89:7890`）
  - ⚠️ **重要**：由于币安 API 在国内无法直接访问，必须配置代理
  - 推荐使用稳定的代理服务（如 AWS/Lightsail/DO 等云服务器搭建）

**可选配置：**
- `TELEGRAM_TOKEN`: 你的 Bot Token（代码中已配置，可覆盖）
- `TELEGRAM_CHAT_ID`: 你的 Chat ID（代码中已配置，可覆盖）
- `WECHAT_SCKEY`: Server酱 Key（代码中已配置，可覆盖）

### 4. 查看日志

部署成功后，可以在 Zeabur 控制台查看实时日志：

```bash
zeabur logs squeeze-monitor
```

---

## 🔧 本地测试

在部署到 Zeabur 之前，可以先本地测试：

```bash
cd "C:\Users\Martin\Downloads\机器人\轧空"
python squeeze_monitor.py
```

---

## 📊 监控逻辑

**触发条件**：
- 资金费率 ≤ -0.1%（极端负值）
- OI 短期均值（3次）≥ 长期均值（10次）× 2倍

**止盈止损**：
- TP1: +5%
- TP2: +10%
- 止损: -3%

**扫描频率**：每 10 分钟

**监控范围**：所有 USDT 合约（24h 交易量 > $10M）
- 💡 **完整扫描**，捕捉更多轧空机会
- ⚠️ **建议部署到 Zeabur**，避免与本地程序竞争 API 配额

---

## 📈 数据持久化

程序会在 `/app/data` 目录下创建：
- `squeeze_signals.json` - 存储所有信号记录

**注意**：Zeabur Worker 重启后数据会丢失，如需持久化存储，建议：
1. 定期导出数据
2. 使用外部数据库（如 Redis）
3. 或使用 Zeabur 的 Volume 功能

---

## 💰 成本估算

Zeabur 免费套餐：
- ✅ 512MB RAM
- ✅ 0.1 CPU 核心
- ✅ 每月 1000 小时运行时间

对于这个监控机器人，**完全够用且免费**。

---

## ⚠️ 注意事项

1. **代理配置**：
   - ⚠️ **必须配置代理**才能访问币安 API
   - 推荐使用稳定的云服务器代理（AWS/Lightsail/DO）
   - 代理格式：`http://IP:PORT` 或 `socks5://IP:PORT`

2. **时区设置**：已设置为 `Asia/Shanghai`（UTC+8）

3. **数据持久化**：Worker 重启后内存数据会丢失

4. **API 限制**：币安 API 有频率限制，已启用 `enableRateLimit`

---

## 🆘 故障排除

### 问题 1：程序频繁重启

**原因**：内存不足或程序异常退出

**解决**：
- 查看日志：`zeabur logs squeeze-monitor`
- 检查代码逻辑
- 联系技术支持

### 问题 2：收不到 Telegram 通知

**原因**：Token 或 Chat ID 错误

**解决**：
1. 确认 Token 格式：`数字:字母`
2. 确认 Chat ID：纯数字
3. 向 Bot 发送一条消息，确保 Token 有效
4. 检查网络连接

### 问题 3：数据获取失败

**原因**：代理配置错误或代理不可用

**解决**：
1. 确认代理地址格式正确：`http://IP:PORT` 或 `socks5://IP:PORT`
2. 测试代理是否可用：
   ```bash
   curl -x http://your-proxy:port https://api.binance.com/api/v3/ping
   ```
3. 如果使用 Zeabur 部署，确保代理服务器允许 Zeabur 的 IP 访问
4. 检查代理服务器日志，确认连接是否成功
5. 考虑使用云服务器搭建稳定代理（AWS/Lightsail/DO）

---

## 📞 技术支持

- Zeabur 文档：https://zeabur.com/docs
- Zeabur Discord：https://discord.gg/zeabur
- ccxt 文档：https://docs.ccxt.com

---

## ✅ 部署检查清单

- [ ] 已创建 Zeabur 账号
- [ ] 已配置代理服务器（PROXY 环境变量）
- [ ] 已测试代理可访问币安 API
- [ ] 已更新 Telegram Token
- [ ] 已更新 Telegram Chat ID
- [ ] 已本地测试通过（需要代理）
- [ ] 已上传代码到 Zeabur
- [ ] 已在 Zeabur 中配置 PROXY 环境变量
- [ ] 已查看日志确认运行正常
- [ ] 已收到测试通知

---

部署完成后，你将：
✅ 不需要本地电脑运行
✅ 24/7 自动监控
✅ 自动收到轧空信号通知
✅ 自动统计胜率

开始享受云端监控的便利吧！🚀
