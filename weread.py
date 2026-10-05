

import argparse
import logging
import os
import sys
from urllib.parse import urlparse
import time
from notion_client import Client
import requests
from http.cookies import SimpleCookie
from datetime import datetime


class SyncError(Exception):
    """An actionable error that contains no credentials or response bodies."""


class WereadSession(requests.Session):
    def request(self, method, url, **kwargs):
        if urlparse(url).scheme != "https" or urlparse(url).hostname not in {
            "weread.qq.com", "i.weread.qq.com"
        }:
            raise SyncError("拒绝向非微信读书地址发送请求。")
        kwargs.setdefault("timeout", (10, 30))
        kwargs["allow_redirects"] = False
        try:
            response = super().request(method, url, **kwargs)
        except requests.RequestException:
            raise SyncError("微信读书网络请求失败，请稍后重试。") from None
        if response.status_code in (401, 403):
            raise SyncError("微信读书认证失败，请重新登录并更新 WEREAD_COOKIE。")
        if not 200 <= response.status_code < 300:
            raise SyncError(f"微信读书请求失败（HTTP {response.status_code}）。")
        return response


def weread_json(response):
    try:
        data = response.json()
    except ValueError:
        raise SyncError("微信读书返回非 JSON 数据，请检查 Cookie 是否过期。") from None
    if not isinstance(data, dict):
        raise SyncError("微信读书返回的数据格式异常。")
    if data.get("errcode", 0) != 0:
        raise SyncError("微信读书接口返回错误，请检查 Cookie 和访问权限。")
    return data

WEREAD_URL = "https://weread.qq.com/"
WEREAD_NOTEBOOKS_URL = "https://i.weread.qq.com/user/notebooks"
WEREAD_BOOKMARKLIST_URL = "https://i.weread.qq.com/book/bookmarklist"
WEREAD_CHAPTER_INFO = "https://i.weread.qq.com/book/chapterInfos"
WEREAD_READ_INFO_URL = "https://i.weread.qq.com/book/readinfo"
WEREAD_REVIEW_LIST_URL = "https://i.weread.qq.com/review/list"
WEREAD_BOOK_INFO = "https://i.weread.qq.com/book/info"


def parse_cookie_string(cookie_string):
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_string)
    except Exception:
        raise SyncError("WEREAD_COOKIE 格式无效。") from None
    cookiejar = requests.cookies.RequestsCookieJar()
    for key, morsel in cookie.items():
        cookiejar.set(key, morsel.value, domain=".weread.qq.com", path="/", secure=True)
    if not cookiejar:
        raise SyncError("WEREAD_COOKIE 为空或格式无效。")
    return cookiejar


def get_bookmark_list(bookId):
    """获取我的划线"""
    params = dict(bookId=bookId)
    r = session.get(WEREAD_BOOKMARKLIST_URL, params=params)
    if r.ok:
        updated = weread_json(r).get("updated", [])
        updated = sorted(updated, key=lambda x: (
            x.get("chapterUid", 1), int((x.get("range") or "0").split("-")[0])))
        return updated
    return None


def get_read_info(bookId):
    params = dict(bookId=bookId, readingDetail=1,
                  readingBookIndex=1, finishedDate=1)
    r = session.get(WEREAD_READ_INFO_URL, params=params)
    if r.ok:
        return weread_json(r)
    return None


def get_bookinfo(bookId):
    """获取书的详情"""
    params = dict(bookId=bookId)
    r = session.get(WEREAD_BOOK_INFO, params=params)
    isbn = ""
    if r.ok:
        data = weread_json(r)
        isbn = data["isbn"]
    return isbn


def get_review_list(bookId):
    """获取笔记"""
    params = dict(bookId=bookId, listType=11, mine=1, syncKey=0)
    r = session.get(WEREAD_REVIEW_LIST_URL, params=params)
    reviews = weread_json(r).get("reviews", [])
    summary = list(filter(lambda x: x.get("review").get("type") == 4, reviews))
    reviews = list(filter(lambda x: x.get("review").get("type") == 1, reviews))
    reviews = list(map(lambda x: x.get("review"), reviews))
    reviews = list(map(lambda x: {**x, "markText": x.pop("content")}, reviews))
    return summary, reviews


def get_table_of_contents():
    """获取目录"""
    return {
        "type": "table_of_contents",
        "table_of_contents": {
            "color": "default"
        }
    }


def get_heading(level, content):
    if level == 1:
        heading = "heading_1"
    elif level == 2:
        heading = "heading_2"
    else:
        heading = "heading_3"
    return {
        "type": heading,
        heading: {
            "rich_text": [{
                "type": "text",
                "text": {
                    "content": content,
                }
            }],
            "color": "default",
            "is_toggleable": False
        }
    }


def get_quote(content):
    return {
        "type": "quote",
        "quote": {
            "rich_text": [{
                "type": "text",
                "text": {
                    "content": content
                },
            }],
            "color": "default"
        }
    }


def get_callout(content, style, colorStyle, reviewId):
    # 根据不同的划线样式设置不同的emoji 直线type=0 背景颜色是1 波浪线是2
    emoji = "🌟"
    if style == 0:
        emoji = "💡"
    elif style == 1:
        emoji = "⭐"
    # 如果reviewId不是空说明是笔记
    if reviewId != None:
        emoji = "✍️"
    color = "default"
    # 根据划线颜色设置文字的颜色
    if colorStyle == 1:
        color = "red"
    elif colorStyle == 2:
        color = "purple"
    elif colorStyle == 3:
        color = "blue"
    elif colorStyle == 4:
        color = "green"
    elif colorStyle == 5:
        color = "yellow"
    return {
        "type": "callout",
        "callout": {
            "rich_text": [{
                "type": "text",
                "text": {
                    "content": content,
                }
            }],
            "icon": {
                "emoji": emoji
            },
            "color": color
        }
    }


def check(bookId):
    """Collect previous page IDs; archive them only after replacement succeeds."""
    time.sleep(0.3)
    filter = {
        "property": "BookId",
        "rich_text": {
            "equals": bookId
        }
    }
    ids = []
    cursor = None
    while True:
        query = {"data_source_id": data_source_id, "filter": filter}
        if cursor:
            query["start_cursor"] = cursor
        response = client.data_sources.query(**query)
        ids.extend(result["id"] for result in response["results"])
        if not response.get("has_more"):
            return ids
        cursor = response["next_cursor"]


def get_chapter_info(bookId):
    """获取章节信息"""
    body = {
        'bookIds': [bookId],
        'synckeys': [0],
        'teenmode': 0
    }
    r = session.post(WEREAD_CHAPTER_INFO, json=body)
    data = weread_json(r)
    if "data" in data and len(data["data"]) == 1 and "updated" in data["data"][0]:
        update = data["data"][0]["updated"]
        return {item["chapterUid"]: item for item in update}
    return None


def insert_to_notion(bookName, bookId, cover, sort, author):
    """插入到notion"""
    time.sleep(0.3)
    parent = {
        "data_source_id": data_source_id,
        "type": "data_source_id"
    }
    properties = {
        "BookName": {"title": [{"type": "text", "text": {"content": bookName}}]},
        "BookId": {"rich_text": [{"type": "text", "text": {"content": bookId}}]},
        "Author": {"rich_text": [{"type": "text", "text": {"content": author}}]},
        "Sort": {"number": sort},
        "Cover": {"files": [{"type": "external", "name": "Cover", "external": {"url": cover}}]},
    }
    read_info = get_read_info(bookId=bookId)
    if read_info != None:
        markedStatus = read_info.get("markedStatus", 0)
        readingTime = read_info.get("readingTime", 0)
        format_time = ""
        hour = readingTime // 3600
        if hour > 0:
            format_time += f"{hour}时"
        minutes = readingTime % 3600 // 60
        if minutes > 0:
            format_time += f"{minutes}分"
        properties["Status"] = {"select": {
            "name": "读完" if markedStatus == 4 else "在读"}}
        properties["ReadingTime"] = {"rich_text": [
            {"type": "text", "text": {"content": format_time}}]}
        if "finishedDate" in read_info:
            properties["Date"] = {"date": {"start": datetime.utcfromtimestamp(read_info.get(
                "finishedDate")).strftime("%Y-%m-%d %H:%M:%S"), "time_zone": "Asia/Shanghai"}}

    icon = {
        "type": "external",
        "external": {
            "url": cover
        }
    }
    # notion api 限制100个block
    response = client.pages.create(
        parent=parent, icon=icon, properties=properties)
    id = response["id"]
    return id


def add_children(id, children):
    results = []
    for i in range(0, len(children), 100):
        time.sleep(0.3)
        response = client.blocks.children.append(
            block_id=id, children=children[i:i+100])
        results.extend(response.get("results"))
    return results if len(results) == len(children) else None


def add_grandchild(grandchild, results):
    for key, value in grandchild.items():
        time.sleep(0.3)
        id = results[key].get("id")
        client.blocks.children.append(block_id=id, children=[value])


def get_notebooklist():
    """获取笔记本列表"""
    r = session.get(WEREAD_NOTEBOOKS_URL)
    if r.ok:
        data = weread_json(r)
        books = data.get("books")
        if not isinstance(books, list):
            raise SyncError("微信读书书架数据缺失，请检查 Cookie 是否有效。")
        books.sort(key=lambda x: x["sort"])
        return books
    else:
        raise SyncError("微信读书书架请求失败。")
    return None


def get_sort():
    """获取database中的最新时间"""
    filter = {
        "property": "Sort",
        "number": {
            "is_not_empty": True
        }
    }
    sorts = [
        {
            "property": "Sort",
            "direction": "descending",
        }
    ]
    response = client.data_sources.query(
        data_source_id=data_source_id, filter=filter, sorts=sorts, page_size=1)
    if (len(response.get("results")) == 1):
        return response.get("results")[0].get("properties").get("Sort").get("number")
    return 0


def get_children(chapter, summary, bookmark_list):
    children = []
    grandchild = {}
    if chapter != None:
        # 添加目录
        children.append(get_table_of_contents())
        d = {}
        for data in bookmark_list:
            chapterUid = data.get("chapterUid", 1)
            if (chapterUid not in d):
                d[chapterUid] = []
            d[chapterUid].append(data)
        for key, value in d .items():
            if key in chapter:
                # 添加章节
                children.append(get_heading(
                    chapter.get(key).get("level"), chapter.get(key).get("title")))
            for i in value:
                callout = get_callout(
                    i.get("markText"), i.get("style"), i.get("colorStyle"), i.get("reviewId"))
                children.append(callout)
                if i.get("abstract") != None and i.get("abstract") != "":
                    quote = get_quote(i.get("abstract"))
                    grandchild[len(children)-1] = quote

    else:
        # 如果没有章节信息
        for data in bookmark_list:
            children.append(get_callout(data.get("markText"),
                            data.get("style"), data.get("colorStyle"), data.get("reviewId")))
    if summary != None and len(summary) > 0:
        children.append(get_heading(1, "点评"))
        for i in summary:
            children.append(get_callout(i.get("review").get("content"), i.get(
                "style"), i.get("colorStyle"), i.get("review").get("reviewId")))
    return children, grandchild


def main():
    global session, client, database_id, data_source_id
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="仅检查认证和数据库，不写入")
    options = parser.parse_args()
    names = ("WEREAD_COOKIE", "NOTION_TOKEN", "NOTION_DATABASE_ID")
    values = {name: os.environ.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise SyncError("缺少环境变量：" + ", ".join(missing))
    weread_cookie = values["WEREAD_COOKIE"]
    database_id = values["NOTION_DATABASE_ID"]
    notion_token = values["NOTION_TOKEN"]
    session = WereadSession()
    session.cookies = parse_cookie_string(weread_cookie)
    client = Client(
        auth=notion_token,
        log_level=logging.ERROR, notion_version="2025-09-03", timeout_ms=30000
    )
    session.get(WEREAD_URL)
    database = client.databases.retrieve(database_id=database_id)
    sources = database.get("data_sources", [])
    data_source_id = os.environ.get("NOTION_DATA_SOURCE_ID", "").strip()
    if data_source_id:
        if data_source_id.replace("-", "") not in {s["id"].replace("-", "") for s in sources}:
            raise SyncError("NOTION_DATA_SOURCE_ID 不属于指定数据库。")
    elif len(sources) == 1:
        data_source_id = sources[0]["id"]
    else:
        raise SyncError("数据库包含零个或多个数据源，请设置 NOTION_DATA_SOURCE_ID。")
    latest_sort = get_sort()
    books = get_notebooklist()
    if options.check:
        print(f"认证及数据库检查通过，微信读书书架有 {len(books)} 本书；未写入数据。")
        return
    if (books != None):
        for book in books:
            sort = book["sort"]
            if sort <= latest_sort:
                continue
            book = book.get("book")
            title = book.get("title")
            cover = book.get("cover")
            bookId = book.get("bookId")
            author = book.get("author")
            previous_ids = check(bookId)
            chapter = get_chapter_info(bookId)
            bookmark_list = get_bookmark_list(bookId)
            summary, reviews = get_review_list(bookId)
            bookmark_list.extend(reviews)
            bookmark_list = sorted(bookmark_list, key=lambda x: (
                x.get("chapterUid", 1), 0 if x.get("range", "") == "" else int(x.get("range").split("-")[0])))
            children, grandchild = get_children(
                chapter, summary, bookmark_list)
            id = insert_to_notion(title, bookId, cover, sort, author)
            try:
                results = add_children(id, children)
                if results is None:
                    raise SyncError("Notion 返回的块数量不完整。")
                if grandchild:
                    add_grandchild(grandchild, results)
            except Exception:
                # Keep old pages and remove the incomplete replacement from the cursor.
                client.pages.update(page_id=id, archived=True)
                raise
            for previous_id in previous_ids:
                client.pages.update(page_id=previous_id, archived=True)


if __name__ == "__main__":
    try:
        main()
    except SyncError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print("同步失败：请检查 Notion 集成授权、数据库字段及网络；凭据与响应内容未输出。", file=sys.stderr)
        sys.exit(1)

