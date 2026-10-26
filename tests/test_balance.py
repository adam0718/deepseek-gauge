"""DeepSeek 余额读取的单元测试（不启动 GUI，不打网络）。

跑法：python tests/test_balance.py

样本来自 platform.deepseek.com /api/v0/users/get_user_summary 的真实响应
（2026-10-26 实测）。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import deepseek_gauge as g  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print("%-42s %-28s %s" % (name, got, "PASS" if ok else "FAIL (want %s)" % (want,)))


def check_true(name, got):
    check(name, bool(got), True)


# --- 真实响应（字段裁剪到浮窗用到的部分）
PAYLOAD = {
    "code": 0,
    "msg": "",
    "data": {
        "biz_code": 0,
        "biz_msg": "",
        "biz_data": {
            "normal_wallets": [
                {"currency": "CNY", "balance": "5.3303092000000000", "token_estimation": "0"}
            ],
            "bonus_wallets": [{"currency": "CNY", "balance": "0", "token_estimation": "0"}],
            "total_costs": [{"currency": "CNY", "amount": "11.0345683200000000"}],
        },
    },
}

d = g.normalize_balance(PAYLOAD)
check_true("真实响应能解析", d)
check("币种", d["currency"], "CNY")
check("充值余额", round(d["normal"], 2), 5.33)
check("赠金余额", round(d["bonus"], 2), 0.0)
check("可用总额 = 充值 + 赠金", round(d["total"], 2), 5.33)
check("累计消费", round(d["spent"], 2), 11.03)
check_true("带抓取时间戳", d["fetched_at"] > 0)

# --- 赠金有值时总额要加上
with_bonus = {
    "code": 0,
    "data": {"biz_data": {
        "normal_wallets": [{"currency": "CNY", "balance": "3.50"}],
        "bonus_wallets": [{"currency": "CNY", "balance": "1.50"}],
        "total_costs": [{"currency": "CNY", "amount": "2.00"}],
    }},
}
d2 = g.normalize_balance(with_bonus)
check("总额含赠金", round(d2["total"], 2), 5.00)
check("充值单独计", round(d2["normal"], 2), 3.50)
check("赠金单独计", round(d2["bonus"], 2), 1.50)

# --- 美元账户
usd = {
    "code": 0,
    "data": {"biz_data": {
        "normal_wallets": [{"currency": "USD", "balance": "8.25"}],
        "bonus_wallets": [],
        "total_costs": [{"currency": "USD", "amount": "1.00"}],
    }},
}
d3 = g.normalize_balance(usd)
check("美元币种", d3["currency"], "USD")
check("美元余额", round(d3["total"], 2), 8.25)
check("美元符号", g.fmt_money(d3["total"], "USD"), "$8.25")

# --- 余额为 0 也要能显示（不能当成"没数据"）
zero = {
    "code": 0,
    "data": {"biz_data": {
        "normal_wallets": [{"currency": "CNY", "balance": "0"}],
        "bonus_wallets": [{"currency": "CNY", "balance": "0"}],
        "total_costs": [{"currency": "CNY", "amount": "0"}],
    }},
}
d4 = g.normalize_balance(zero)
check_true("零余额仍算有效数据", d4)
check("零余额值", d4["total"], 0.0)

# --- 异常输入一律返回 None，不能瞎猜
check("非字典", g.normalize_balance("boom"), None)
check("错误码", g.normalize_balance({"code": 40003}), None)
check("缺 data", g.normalize_balance({"code": 0}), None)
check("缺 biz_data", g.normalize_balance({"code": 0, "data": {}}), None)
check("钱包为空", g.normalize_balance({"code": 0, "data": {"biz_data": {
    "normal_wallets": [], "bonus_wallets": []}}}), None)

# --- 余额配色：钱越少越红
check("余额 0 危险", g.balance_color(0), g.C_DANGER)
check("余额 1.5 危险", g.balance_color(1.5), g.C_DANGER)
check("余额 2 转为警告", g.balance_color(2.0), g.C_WARN)
check("余额 5.33 警告", g.balance_color(5.33), g.C_WARN)
check("余额 10 转为正常", g.balance_color(10.0), g.C_OK)
check("余额 99 正常", g.balance_color(99), g.C_OK)
check("非法输入不炸", g.balance_color(None), g.C_DIM)

# --- 金额格式
check("人民币两位小数", g.fmt_money(5.3303092, "CNY"), "¥5.33")
check("整数补两位", g.fmt_money(0, "CNY"), "¥0.00")
check("未知币种只给数字", g.fmt_money(3, "XYZ"), "3.00")
check("非法金额", g.fmt_money(None, "CNY"), "¥--")

# --- 时长文案
check("ago 30 秒", g.fmt_ago(30), "刚刚")
check("ago 90 秒", g.fmt_ago(90), "1 分钟前")
check("ago 2 小时", g.fmt_ago(7200), "2 小时前")
check("ago 19 小时", g.fmt_ago(19 * 3600), "19 小时前")

# --- 凭证解析：用真实结构造一个临时文件
CRED_SAMPLE = """\ufeffversion: 1
records:
  client-connection/browser-session:
    kind: grant
    payload:
      version: 1
      secret: abcdefghijklmnop
  deepseek-account-platform/device:
    kind: grant
    payload:
      id: 11111111-2222-3333-4444-555555555555
  deepseek-account-platform/default:
    kind: grant
    payload:
      version: 1
      token: TOKEN_VALUE_0123456789
      issuer: https://platform.deepseek.com
refs: []
"""

tmp = Path(tempfile.mkdtemp(prefix="dsg-test-"))
(tmp / ".credentials.yaml").write_text(CRED_SAMPLE, encoding="utf-8")

check("从凭证文件读出 token", g.read_token(tmp), "TOKEN_VALUE_0123456789")
check("找不到目录时返回 None", g.read_token(tmp / "nope"), None)
check("空目录返回 None", g.read_token(None), None)

# 文件里没有目标段落时不能误抓别的字段
(tmp / "empty").mkdir()
(tmp / "empty" / ".credentials.yaml").write_text(
    "version: 1\nrecords:\n  other/thing:\n    payload:\n      token: WRONG\n",
    encoding="utf-8",
)
check("不误抓其他段落的 token", g.read_token(tmp / "empty"), None)

# --- 自动探测 dsh 目录
found = g.find_dsh_home(str(tmp))
check("指定目录时能定位", found, tmp)
# 显式路径失效时会继续自动探测（宁可找到真的，也不要因为配置过期就罢工），
# 但绝不能把那个失效路径原样返回
check("指定错误目录时不会原样返回",
      g.find_dsh_home(str(tmp / "nope")) != (tmp / "nope"), True)

# --- 没登录信息时给的是人话，不是堆栈
try:
    g.fetch_balance(tmp / "nope")
    check("无凭证时抛错", "没抛", "抛 BalanceError")
except g.BalanceError as e:
    check("无凭证时的提示", str(e), "未找到 DSH 登录信息")

# --- 清理
import shutil  # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)

print()
print("balance: %d/%d passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
