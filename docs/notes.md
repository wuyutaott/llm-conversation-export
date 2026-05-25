# 经验与坑（实测记录）

开发过程中踩过的、对维护和加新平台有用的非显然知识。

## 通用

- **必须经 browser-harness 跑**：`js` 等 helper 是注入到 exec 全局的，`import` 的模块拿不到。
  入口 `export.py` 用 `browser.bind(...)` 注入后，其余模块走 `core.browser`。直接 `python` 跑必 NameError。
- **站点要对**：页面内相对路径 `fetch("/...")` 发到的是**当前标签页所在站点**。
  之前在 grok 标签页跑 chatgpt 脚本，请求发到了 grok.com，报"拿不到 accessToken"。
  解决：每次开跑先 `ensure_origin(domain, home_url)`，不在目标站点就 `new_tab` 打开。
- **限流(429)不是并发问题**：脚本严格串行（一次一个请求）。429 是服务端按"单位时间请求数/账号/IP"限的。
  缓解手段：① token 缓存复用，少打一半请求；② 会话间 `gap` 停顿；③ 429 指数退避重试。
- **连续失败熔断**：连续失败 3 次就停（多半掉登录/被限流/断网），避免把后面的会话全标记失败。
  偶发单次失败不触发（成功即重置计数）。失败的会话状态留作非"完成"，重跑自动重试。
- **断点续传靠 titles.csv 的「状态」列**：每抓完一个原子写回。刷新清单时按会话ID保留已有标记，不丢进度。

## ChatGPT

- 认证：`GET /api/auth/session` 拿 `accessToken`（有效期数小时），后续请求带 `Authorization: Bearer`。
  **缓存复用**，仅 401 时刷新——别每个请求都调 session（会翻倍请求量、更易限流）。
- 账号标识：session 里的 `user.email`。
- 会话列表：`/backend-api/conversations?offset=&limit=28&order=updated`。
  **`total` 字段不可靠**（曾返回 29，实际 500+）。要翻到"本页无新增"为止，别信 total。
- 会话详情：`/backend-api/conversation/{id}`，正文在 `mapping` 树里，按 `parent→children` 还原顺序。
- 图片：消息 parts 里的 `image_asset_pointer` + `metadata.attachments`。
  下载两步：`/backend-api/files/{id}/download` 取签名 `download_url`，再带 Bearer fetch 该 URL。

## Grok

- 认证：**cookie**（httpOnly，页面内 fetch 自动带），无需 token。
- 账号标识：优先**邮箱**——但 Grok 没有邮箱接口，邮箱只在侧边栏账号区的 DOM 文本里，
  用正则从 `document.documentElement.innerHTML` 抓（等渲染，重试几次），取不到回退到 `x-userid` cookie。
- 会话列表：`GET /rest/app-chat/conversations?pageToken=`（每页 60，`nextPageToken` 翻页）。
- 会话详情**两步**：
  1. `GET .../{cid}/response-node` → 消息树（`responseId`/`sender`/`parentResponseId`）。
  2. `POST .../{cid}/load-responses` body `{"responseIds":[...]}` → 正文（`message`/`sender`/`createTime`/资源字段）。
     responseId 多时分批（每批 ≤100）。
- `sender` 在 load-responses 里是**小写**（`human`/`assistant`），response-node 里却是大写 `ASSISTANT`——
  渲染统一 `.lower()` 再映射。
- 图片/文件：`fileAttachmentsMetadata`（含 `fileUri`、`fileMimeType`、`fileName`）+ `generatedImageUrls`。
  下载直链：`https://assets.grok.com/{fileUri}`，需 `credentials:"include"`。非图片附件存为对应扩展名（识别不出用 `.bin`）。

## Gemini（gemini.google.com）

最难的一个：没有干净 REST，用 Google 的 `batchexecute` RPC + SPA DOM 抓取。

- 认证：Google cookie + 页面 `window.WIZ_global_data` 里的 `SNlM0e`(at) / `FdrFJe`(f.sid) / `cfb2h`(bl)。
- 账号：无邮箱接口，从页面 DOM 正则提取登录邮箱（同 Grok 思路）。
- 会话列表：RPC `MaZiqc`。**首页 payload 是 `[60]`（单元素数组）**，返回 `[null, cursor, [[id,title,...,[ts,ns],...],...]]`；
  下一页 `[60, cursor]`，直到无 cursor。注意 `[60,null]`（显式 null）会返回空——必须用单元素。
- 会话正文：正文 RPC（hNvQHb）脱离 SPA 复现返回空、直链 `/app/{id}` 会被重定向回 `/app`。
  最终改为 **SPA 内 DOM 抓取**：
  1. `browser.navigate` 整页重载回 `/app`（**关键**：同路由换参数 `pushState` 不会触发 Angular 重渲染，
     会抓到上一个会话的残留 DOM；必须整页重载回 /app 再进目标会话）。
  2. `history.pushState(null,'','/app/{id}')` + `dispatchEvent(PopStateEvent('popstate'))` 打开会话。
  3. 轮询 `user-query`/`model-response` 数量稳定后，抓 `.query-text` 与 `.markdown` 的 innerText。
  4. 用户消息开头的 "你说"/"You said" 是 UI 标签，需正则去掉。
- 代价：每个会话要整页重载（~7.5s/个），比 ChatGPT/Grok 慢；且依赖 Gemini 前端 DOM 结构，前端改版会失效。
- 暂未导出会话中的图片（Gemini 图片在 DOM 里是 blob，后续可加）。
- batchexecute 响应格式：`)]}'` 前缀 + 多段「长度行 + JSON 数组行」，目标数据在 `["wrb.fr", rpcid, "<内层JSON字符串>", ...]` 的第 3 项（需二次 json 解析）。
- `core.browser.fetch` 的 body：dict/list 会 `JSON.stringify`，**字符串体原样发送**（Gemini 的 `f.req=...` 表单体靠这个）。

## 文件命名

- 会话文件名：`{三位序号}_{净化标题}`，图片在 `images/{该文件名}/` 子目录。
- Markdown 用相对路径 `../images/{文件名}/xxx` 引用——只要 markdown 和 images 同在 `{账号}/` 下，
  整体搬动（如重构换目录）相对关系不变，引用不会失效。
