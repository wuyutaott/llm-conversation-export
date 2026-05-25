# 架构 / 如何新增一个平台

## 总体设计

通用编排集中在 `core/`，每个平台只是一个轻量 **adapter**。加新平台 = 往 `adapters/` 丢一个文件，根 `run.sh` 菜单会自动发现它。

```
memory-exportor/
├── run.sh           # 交互菜单：选平台 → 选账号 → 选动作
├── export.py        # 被 browser-harness exec 的入口；bind helper 后调 driver
├── core/
│   ├── browser.py   # 浏览器内 fetch/下载/切站点；bind() 接收注入的 helper
│   ├── storage.py   # 路径 out/{平台}/{账号}/ + titles.csv 读写（保留进度标记）
│   ├── util.py      # 时间格式化 / 文件名净化 / 扩展名推断
│   └── driver.py    # 主循环：账号校验、清单生成、断点续传、下载、渲染、进度、熔断
├── adapters/
│   ├── chatgpt.py   # ChatGPTAdapter（token + REST）
│   ├── grok.py      # GrokAdapter（cookie + REST）
│   └── gemini.py    # GeminiAdapter（batchexecute 列表 + SPA DOM 抓正文）
└── out/{平台}/{账号}/{json,markdown,images,titles.csv}
```

## 关键约束：helper 注入

browser-harness 把 `js / page_info / new_tab / wait_for_load` 注入到 `exec` 的**全局命名空间**；`import` 进来的模块默认拿不到。所以 `export.py`（被 exec 的那个）拿到这些名字后调用 `browser.bind(...)` 注入 `core.browser`，其余模块统一通过 `core.browser` 发请求，不直接碰 `js`。

→ 因此**绝不能** `python export.py` 直接跑，必须经 `browser-harness -c "exec(...)"`（`run.sh` 已封装）。

## Adapter 接口

一个 adapter 是实现下列约定的对象，模块末尾导出 `adapter = XxxAdapter()`：

| 成员 | 说明 |
|---|---|
| `name` | 平台名，= 模块名，= `out/{name}/` |
| `domain` / `home_url` | 用于 `ensure_origin` 切到正确站点 |
| `gap` / `img_gap` | 会话之间 / 资源之间的停顿秒数（控速避限流）|
| `prepare() -> str` | 切站点后做认证，返回**当前登录账号标识**（邮箱优先）|
| `list_conversations() -> [{id,title,create_time,update_time}]` | 分页拉全部会话清单 |
| `fetch_conversation(cid) -> dict` | 取单个会话完整内容（须可 JSON 序列化）|
| `collect_assets(conv) -> [{key,name,mime,is_image,...}]` | 列出该会话要下载的图片/文件 |
| `download_asset(desc) -> (bytes, name, mime)` | 下载单个资源（平台自行决定调几次接口）|
| `render_markdown(conv, title, key2rel) -> str` | 渲染 Markdown；`key2rel[key]={rel,is_image,name}` |

driver 负责：站点切换、账号校验（指定账号 vs 当前登录）、缺清单则调 `list_conversations` 生成、按 CSV 断点续传、资源去重/跳过已存在、进度条+耗时、连续失败熔断、原子写 CSV。adapter 只关心**该平台的接口与数据形状**。

## 新增平台步骤

1. 在 `adapters/` 新建 `<平台>.py`，实现上面的接口，末尾 `adapter = ...()`。
2. 认证：cookie 型（如 grok）直接 `browser.fetch`；token 型（如 chatgpt）在 `prepare/_api` 里带 header。
3. 资源下载：单步直链用 `browser.download(url, credentials=...)`；需先换签名 URL 的（如 chatgpt）在 `download_asset` 里先调接口再下。
4. 跑 `./run.sh`，新平台会自动出现在菜单里。

参考现有 adapter：`chatgpt.py`（token + mapping 树 + 两步下载）、`grok.py`（cookie + response 列表 + 直链下载 + DOM 抓邮箱）、`gemini.py`（batchexecute RPC 拉列表 + SPA 内整页重载+pushState 打开会话 + DOM 抓正文，最复杂的范例）。

注：gemini adapter 用到 `browser.navigate`（整页重载，需 `cdp` helper，已在 export.py 绑定）和 `browser.run_js`（DOM 抓取）。新平台若用 REST 则无需这些。
