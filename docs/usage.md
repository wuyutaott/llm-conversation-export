# 使用说明

把已登录网页版 AI 助手（ChatGPT、Grok…）的聊天记录导出成本地文件（原始 JSON + 可读 Markdown + 图片/附件）。

## 前提

1. 已安装命令行工具 `browser-harness`（见 [dependencies.md](dependencies.md)）。
2. Chrome 里**已登录**要导出的平台（chatgpt.com / grok.com）。脚本靠你浏览器的登录态调接口，不碰你的密码。

## 一条命令搞定

macOS / Linux：

```bash
./run.sh          # 等价于 python3 run.py
```

Windows（CMD / PowerShell）：

```bat
run.cmd
:: 或直接 python run.py
```

> 三个入口都转交给跨平台的 `run.py`，菜单与流程完全一致；方向键（↑↓）或 `j`/`k` 移动，回车确认。

然后按提示选择：

1. **选平台**：菜单自动列出 `adapters/` 下的所有平台（chatgpt、grok…）。
2. **选账号**：列出 `out/{平台}/` 下已有账号（带完成进度），外加「当前登录账号(自动检测)」。
   - 选「自动检测」= 导出当前 Chrome 登录的账号。
   - 选某个已有账号 = 会校验它与当前登录是否一致，不一致直接中止（避免抓错账号）。
3. **选动作**：
   - `导出` = 抓正文 + 图片（没有清单会先自动拉取，有则从上次断点继续）。
   - `仅刷新会话清单` = 只更新 `titles.csv`（会保留已完成标记，只补新增会话）。

## 输出位置

```
out/{平台}/{账号}/
├── titles.csv     会话清单 + 每个会话的「状态/文件」标记（断点续传靠它）
├── json/          每个会话的原始 JSON（完整结构，便于二次处理）
├── markdown/      每个会话的可读 Markdown（含图片链接）
└── images/{会话}/ 该会话的图片/附件
```

- 账号隔离：不同账号、不同平台的数据互不混淆。
- Markdown 里图片用相对路径 `../images/{会话}/xxx`，在 Obsidian/VS Code 里可直接显示。

## 断点续传 / 中断与恢复

- 每抓完一个会话立刻把「完成」写回 `titles.csv`（原子写）。
- 中途关掉、断网、被限流都不要紧——**重新 `./run.sh` 选同一平台+账号，会自动跳过「完成」的，从断点继续**，失败的会重试。
- 连续失败 3 次会**自动熔断停止**（多半是掉登录/被限流/断网），避免把后面的会话全标记失败。提示出现后，检查登录状态再重跑即可。

## 停止正在跑的导出

在运行导出的终端里按 **Ctrl-C** 即可：进度已实时写回 `titles.csv`，重跑会从断点继续。（macOS/Linux 也可在另一个终端 `pkill -f export.py`。）

## 查看进度

直接打开 `out/{平台}/{账号}/titles.csv` 看「状态」列，或统计已完成数：

```bash
# macOS / Linux
grep -c ',完成,' out/chatgpt/你的邮箱/titles.csv
```

```powershell
# Windows PowerShell
(Select-String -Path out\chatgpt\你的邮箱\titles.csv -Pattern ',完成,').Count
```
