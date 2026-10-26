# DeepSeek Gauge · 任务

## 已完成（v1.0.0）

- [x] 调研 DSH 是否自带余额悬浮：有余额查询（设置 → 账号与余额 + 不足弹窗），但**无常驻悬浮**
- [x] 摸清数据源：`platform.deepseek.com/api/v0/users/get_user_summary`，鉴权头 `x-dsh-auth-token`
- [x] 确认 token 位置：`dsh-home/.credentials.yaml` → `deepseek-account-platform/default` → `token`
- [x] 实测接口：返回 `normal_wallets` / `bonus_wallets` / `total_costs`，真实数据拿到（¥5.32）
- [x] 单文件实现 `src/deepseek_gauge.py`：卡片悬浮窗、分层窗口圆角 + 可变透明、拖动记忆位置
- [x] dsh 目录自动探测（配置 > 环境变量 > 常见路径 > 扫 C:~H: 盘），失败时右键手动选
- [x] 余额配色：< ¥2 红 + 暗红描边、< ¥10 黄、其余绿
- [x] 队列 + 主线程轮询刷新（沿用 codex-gauge v1.7.1 的架构，子线程不碰 tkinter）
- [x] 看门狗：定时器链断了自动重连、fetching 卡超 120 秒强制放行
- [x] 数据陈旧告警：超 3 个刷新周期显示 `⚠ 数据 X 小时前未更新`
- [x] 错误分型：未找到凭证 / 授权过期（401）/ 网络不可用 / 返回异常
- [x] 单测 42 条全过；真实接口端到端验证；实机截图确认排版
- [x] 启动器 bat（纯 ASCII）、开机自启工具、桌面快捷方式

## 待办

- [ ] 余额趋势：记录每天余额，算「日均消耗」和「还能用几天」（需要先攒够数据）
- [ ] 多账号：如果有多个 DeepSeek 账号
- [ ] 任务栏细条模式（codex-gauge 有，这个还没做）
