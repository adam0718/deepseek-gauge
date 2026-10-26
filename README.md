# DeepSeek Gauge

一个常驻桌面的极简悬浮窗，实时显示 **DeepSeek 平台账户余额**。

```
● DEEPSEEK 余额              [CNY]
¥5.32
充值 ¥5.32 · 赠金 ¥0.00
累计消费 ¥11.04 · 更新 00:40
```

## 它解决什么问题

DeepSeek 的余额在 `platform.deepseek.com/usage` 或 DSH 的「设置 → 账号与余额」里，**都要主动点进去看**。真在写代码的时候，谁也不会为了确认"钱还够不够"切窗口。

这个浮窗把那个数字常驻在桌面角落：瞟一眼就知道还能用多久，不用打断思路。

## 怎么用

| 操作 | 效果 |
| --- | --- |
| 双击 `启动 DeepSeek 余额浮窗.bat` | 启动（也可以直接用桌面图标） |
| 鼠标移入 | 完全显示 |
| 鼠标移出 | 半透明淡出，不挡视线 |
| 左键拖动 | 移动位置（自动记忆） |
| 双击窗口 | 立即刷新 |
| 右键 | 菜单：刷新 / 重置位置 / 选择 DSH 目录 / 淡出透明度 / 退出 |

## 数据从哪来

**不需要你填 API Key。** 它复用 DeepSeek Harness（dsh）已经登录好的账号：

1. 从 dsh 的 `dsh-home/.credentials.yaml` 里读出平台授权 token（只读，不复制、不落盘、不写回）
2. 用这个 token 调 DeepSeek 平台官方接口 `platform.deepseek.com/api/v0/users/get_user_summary`
3. 拿到 `normal_wallets`（充值余额）、`bonus_wallets`（赠金余额）、`total_costs`（累计消费）

**所以 dsh 没开着也能用**——token 是服务端的，浮窗自己直连平台。

### 关于安全

| 担心的事 | 实际情况 |
| --- | --- |
| 会把我的 token 上传吗 | **不会**。token 只在内存里用于这一次请求头，不写进任何文件 |
| 会上传我的余额数据吗 | **不会**。没有服务器、没有账号体系、没有遥测 |
| 联网行为 | 只有一种：向 `platform.deepseek.com` 查你自己的余额 |
| 浮窗自己写的文件 | `%APPDATA%\DeepSeekGauge\` 下的两个 json：界面设置、余额数字缓存（**不含 token**） |

## 颜色含义

余额越低越扎眼：

| 余额 | 颜色 |
| --- | --- |
| < ¥2 | 红 + 窗口描边变暗红 |
| < ¥10 | 黄 |
| ≥ ¥10 | 绿 |

阈值按「人民币日常用量」估的。觉得不合适可以改 `src/deepseek_gauge.py` 顶部的 `LOW_BALANCE_DANGER` / `LOW_BALANCE_WARN`。

## 配置

`%APPDATA%\DeepSeekGauge\config.json`，右键菜单改的都会存这里：

```json
{
  "idle_alpha": 0.55,        // 鼠标不在时的透明度
  "hover_alpha": 1.0,        // 鼠标悬停时
  "refresh_seconds": 60,     // 刷新间隔（最小 15）
  "pos": null,               // 窗口位置，null = 默认右上角
  "rounded": true,           // 圆角卡片；若渲染异常可改 false
  "dsh_home": null           // dsh 目录，null = 自动探测
}
```

**找不到 dsh 目录时**：右键 →「选择 DSH 目录…」，选到那个含 `.credentials.yaml` 的目录（通常叫 `dsh-home`）即可。程序会自动扫 `C:`~`H:` 的 `\DeepSeek\dsh-home`、`~/DeepSeek/dsh-home`、`~/.dsh` 等常见位置。

## 常见问题

**显示「未找到 DSH 登录信息」？**
说明没定位到 dsh 目录，或者你还没在 dsh 里登录。先确认 dsh 能正常用，然后右键选一次目录。

**显示「授权已过期，请在 DSH 重新登录」？**
dsh 的登录态过期了。打开 DSH 重新登录一次，浮窗下一轮刷新会自动恢复。

**底部出现「⚠ 数据 X 小时前未更新」？**
说明好几分钟没拿到新数据（断网、代理切换、电脑休眠刚唤醒）。此时上面的金额是**旧数字**，别当实时看。程序每秒自检，网络恢复后会自动补刷。

**为什么不做成"悄悄用旧数据"？**
那样最危险——数字看着正常，其实是几小时前的，你以为还有钱，实际已经不够跑任务了。宁可丑一点，也要把「这不是实时值」说清楚。

**窗口不见了？**
再双击一次启动器：如果已经有一个在跑，它会把那个窗口提到最前，不会再开第二个。

## 文件结构

```
deepseek-gauge/
├─ 启动 DeepSeek 余额浮窗.bat  启动器（双击即可）
├─ src/deepseek_gauge.py       全部源码，单文件
├─ tools/autostart.py          开机自启管理（install / status / uninstall）
├─ tests/                      单测：test_balance（42 条）
├─ PROJECT.md                  项目背景
├─ TASKS.md                    待办
├─ DECISIONS.md                技术决策记录
└─ CHANGELOG.md                版本变更
```

## 已知限制

- 仅支持 Windows（依赖 Win32 分层窗口做圆角 + 可变透明）
- 依赖 dsh 的凭证文件格式；dsh 大版本改了存储结构需要跟进
- 余额接口是平台网页内部接口（`/api/v0/`），不是公开 API，字段变了需要适配
- 单一实例：重复启动只会唤起已有窗口
