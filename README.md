# 微信读书笔记同步到 Notion

同步划线、笔记、点评和阅读信息。每天北京时间 08:00 由 GitHub Actions 执行，也可手动触发。

## 配置

1. 登录 https://weread.qq.com/ ，在浏览器开发者工具 Network 中复制已登录请求的 Cookie。
2. 在 https://www.notion.so/my-integrations 创建集成，复制 Token，并在目标数据库的 Connections 中授权集成。
3. 从数据库链接中取数据库 ID（不是链接中的视图 `v=` ID）。
4. 在本仓库 Settings → Secrets and variables → Actions 设置 `WEREAD_COOKIE`、`NOTION_TOKEN`、`NOTION_DATABASE_ID`。
5. 数据库字段需包含：`BookName`（标题）、`BookId`/`Author`/`ReadingTime`（文本）、`Sort`（数字）、`Cover`（文件）、`Status`（选择：在读/读完）、`Date`（日期）。
6. 单数据源数据库会自动识别数据源。如果有多个数据源，额外设置 `NOTION_DATA_SOURCE_ID`，并确认它属于指定数据库。
7. Actions 中手动运行 `weread sync`。先检查认证和数据库，再开始同步。

## 本地运行

安装依赖：`python -m pip install -r requirements.txt`。在运行环境中设置上述变量，再运行：

```sh
python weread.py --check
python weread.py
```

`--check` 只读，不新增或归档笔记。`.env.example` 仅为变量清单，程序不会自动加载 `.env`。不再支持把 Cookie 和 Token 作为命令行位置参数传入。

## 更新行为

按数据库中最大 `Sort` 做增量同步，适用于微信读书排序值随更新增加的情况。原有书页只有在新页及全部笔记块写入成功后才归档；新页写入失败时尝试归档不完整的新页，保留原页。若清理失败，应检查数据库中的重复或不完整页后重新运行。

不要在自动同步的书页中添加独立笔记：更新仍通过替换整页完成，手写内容不会迁移。归档旧页不是永久删除，可在 Notion 中恢复。多页更新不是事务；执行中断时仍需检查重复页。数据库全局 Sort 游标沿用原设计，不支持回补比最大 Sort 更早的历史修改。

## 认证与请求流程

`GitHub Secrets → 运行环境 → main()` 读取配置并检查缺失变量；`parse_cookie_string()` 将 Cookie 放入内存 CookieJar；`WereadSession` 通过 HTTPS 请求微信读书；Notion SDK 用集成 Token 访问已授权数据库和数据源；书架数据读取完成后再写入 Notion。

| 组件 | 职责 |
| --- | --- |
| `.github/workflows/weread.yml` | 定时/手动执行，环境注入凭据，防止同一工作流并发 |
| `parse_cookie_string` / `WereadSession` | Cookie 解析、域名限制、超时、HTTP 认证错误处理 |
| `weread_json` | 检查 JSON 和业务错误，不输出响应原文 |
| Notion `Client` | Bearer Token 认证；数据库授权由 Notion 控制 |
| `main` / `check` | 识别数据源、只读预检、先写新页后归档旧页 |

Cookie 仅允许发送到 `.weread.qq.com` 域，通过 HTTPS，拒绝自动重定向及非预期主机。Token 由 SDK 放入 Notion Authorization 请求头；数据库 ID 和数据源 ID 是资源定位符，不代替授权。凭据不写入文件、不输出日志、不插入 Shell 命令。客户端对象仅在本次进程中持有凭据。

本项目不提供用户名/密码登录、OAuth、JWT 签发或自动刷新令牌。Cookie 失效需重新登录微信读书并更新 Secret；Notion Token 失效需在集成设置中更换并更新 Secret。撤销授权通过微信读书会话管理或 Notion 集成设置完成。网络超时、HTTP 401/403、JSON/业务错误会停止同步，错误信息不包含凭据或响应原文。

采用 Notion API `2025-09-03` 的数据源查询方式，避免依赖旧 `databases.query`。
参考：[Notion 升级说明](https://developers.notion.com/docs/upgrade-guide-2025-09-03)、[Python SDK](https://github.com/ramnes/notion-sdk-py)。

## 验证

`python -m unittest discover -s tests -v` 使用模拟网络与 Notion 客户端验证凭据边界、预检和更新失败行为，不访问真实账号。
