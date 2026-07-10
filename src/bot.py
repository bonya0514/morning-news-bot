"""
Morning News Bot
- Tavily で特定サイトからニュース収集
- Groq でまとめ生成
- posted_urls.json で重複チェック
- ガンプラ情報は再販カレンダーページを直接取得
- Discord Webhook に投稿
- ガンプラ再販予定を Discord イベントに登録（重複チェックあり）
"""

import os
import json
import subprocess
import datetime
import requests
import yaml
from groq import Groq

# ── 設定読み込み ──────────────────────────────────────
with open("config.yml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

GROQ_API_KEY         = os.environ["GROQ_API_KEY"]
TAVILY_API_KEY       = os.environ["TAVILY_API_KEY"]
DISCORD_NEWS_URL     = os.environ["DISCORD_WEBHOOK_NEWS"]
DISCORD_GUNPLA_URL   = os.environ["DISCORD_WEBHOOK_GUNPLA"]
DISCORD_BOT_TOKEN    = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_GUILD_ID     = os.environ["DISCORD_GUILD_ID"]

# JST で今日の日付を取得
JST = datetime.timezone(datetime.timedelta(hours=9))
today     = datetime.datetime.now(JST).date()
today_str = today.strftime("%Y年%m月%d日")

# ── Groq クライアント ─────────────────────────────────
client = Groq(api_key=GROQ_API_KEY)

# ── posted_urls.json 管理 ─────────────────────────────
POSTED_URLS_FILE = "posted_urls.json"
URL_EXPIRE_DAYS  = 7

def load_posted_urls() -> dict:
    if os.path.exists(POSTED_URLS_FILE):
        with open(POSTED_URLS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"urls": []}

def save_posted_urls(data: dict) -> None:
    with open(POSTED_URLS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        subprocess.run(["git", "config", "user.email", "bot@morning-news-bot"], check=True)
        subprocess.run(["git", "config", "user.name", "Morning News Bot"], check=True)
        subprocess.run(["git", "add", POSTED_URLS_FILE], check=True)
        subprocess.run(["git", "commit", "-m", f"Update posted_urls [{today_str}]"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("✅ posted_urls.json をコミット")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠️ コミット失敗: {e}")

def cleanup_old_urls(data: dict) -> dict:
    cutoff = today - datetime.timedelta(days=URL_EXPIRE_DAYS)
    data["urls"] = [
        u for u in data["urls"]
        if datetime.date.fromisoformat(u["posted_at"]) > cutoff
    ]
    return data

def is_posted(url: str, data: dict) -> bool:
    return any(u["url"] == url for u in data["urls"])

def add_posted_url(url: str, data: dict) -> dict:
    data["urls"].append({"url": url, "posted_at": today.isoformat()})
    return data


def search_from_sites(query: str, sites: list, max_results: int = 5, days: int = 7) -> list:
    all_results = []
    for site in sites:
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "advanced",
                    "topic": "news",
                    "max_results": max_results,
                    "include_domains": [site],
                    "days": days,
                },
                timeout=15,
            )
            resp.raise_for_status()
            for r in resp.json().get("results", []):
                title   = r.get('title', '')
                content = r.get('content', '')
                url     = r.get('url', '')
                if not title or not content:
                    continue
                if url.rstrip('/') in [f"https://{site}", f"http://{site}"]:
                    continue
                skip_keywords = ["リニューアル", "サイトマップ", "プライバシーポリシー", "について |", "ランキング30", "の記事一覧", "ニュースリリース", "archive"]
                if any(kw in title for kw in skip_keywords):
                    continue
                skip_url_keywords = ["/archive/", "/newsrelease/", "/ranking/"]
                if any(kw in url for kw in skip_url_keywords):
                    continue
                all_results.append({"title": title, "url": url, "content": content})
        except Exception as e:
            print(f"  検索エラー ({site}): {type(e).__name__}: {e}")
    return all_results


def ask_groq(prompt: str) -> str:
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return response.choices[0].message.content.strip()


def post_discord(webhook_url: str, content: str) -> None:
    chunks = [content[i:i+1900] for i in range(0, len(content), 1900)]
    for chunk in chunks:
        resp = requests.post(webhook_url, json={"content": chunk}, timeout=10)
        resp.raise_for_status()


# ── ニュースまとめ生成 ────────────────────────────────
def build_news_message(posted_data: dict) -> tuple:
    categories = config["news_categories"]
    sections = []

    for cat in categories:
        print(f"  検索中: {cat['name']}")
        results = search_from_sites(
            cat["query"] + f" {today.year}年{today.month}月",
            cat["sites"],
            max_results=5,
            days=7,
        )
        new_results = [r for r in results if not is_posted(r["url"], posted_data)]
        if not new_results:
            print(f"  {cat['name']}: 新規記事なし、既存記事から選択")
            new_results = results

        raw = "\n".join(
            f"・{r['title']}（{r['url']}）\n  {r['content'][:200]}"
            for r in new_results[:6]
        ) or "検索結果なし"

        prompt = (
            f"以下の検索結果をもとに、「{cat['name']}」の最新ニュースを日本語で3件にまとめてください。\n\n"
            f"【検索結果】\n{raw}\n\n"
            f"## 出力フォーマット（このフォーマットのみ出力）\n"
            f"{cat['emoji']} {cat['name']}\n"
            f"① 日本語タイトル\n　概要を日本語で1〜2文。\n　🔗 記事のURL\n\n"
            f"② 日本語タイトル\n　概要を日本語で1〜2文。\n　🔗 記事のURL\n\n"
            f"③ 日本語タイトル\n　概要を日本語で1〜2文。\n　🔗 記事のURL\n\n"
            f"## 注意事項\n"
            f"- タイトルと概要は必ず自然な日本語で書くこと\n"
            f"- 検索結果にある具体的な情報のみ使う\n"
            f"- URLは検索結果に含まれているものをそのまま使うこと\n"
            f"- サイトの紹介・説明文は絶対に使わないこと\n"
            f"- 3件見つからない場合は見つかった件数だけ書く\n"
            f"- 余計な前置き・後書き不要"
        )
        sections.append(ask_groq(prompt))
        for r in new_results[:3]:
            posted_data = add_posted_url(r["url"], posted_data)

    return "\n\n".join(sections), posted_data


# ── ガンプラ情報生成 ──────────────────────────────────
def find_calendar_urls() -> list:
    """再販カレンダーページのURLをTavily検索で特定"""
    cat = config["gunpla_categories"][0]
    query = f"ガンプラ 再販 カレンダー {today.year}年{today.month}月"
    urls = []
    for site in cat["sites"]:
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 3,
                    "include_domains": [site],
                },
                timeout=15,
            )
            resp.raise_for_status()
            for r in resp.json().get("results", []):
                url = r.get("url", "")
                title = r.get("title", "")
                # 今年のカレンダー・再販情報ページっぽいものだけ
                if url and (str(today.year) in url or str(today.year) in title):
                    urls.append(url)
        except Exception as e:
            print(f"  カレンダーURL検索エラー ({site}): {type(e).__name__}: {e}")
    return urls[:4]


def extract_pages(urls: list) -> str:
    """Tavily extract APIでページ本文を取得"""
    if not urls:
        return ""
    try:
        resp = requests.post(
            "https://api.tavily.com/extract",
            json={
                "api_key": TAVILY_API_KEY,
                "urls": urls,
            },
            timeout=30,
        )
        resp.raise_for_status()
        chunks = []
        for r in resp.json().get("results", []):
            content = r.get("raw_content", "")[:8000]
            print(f"  取得: {r.get('url', '')} ({len(r.get('raw_content', ''))}文字)")
            if content:
                chunks.append(f"【{r.get('url', '')}】\n{content}")
        combined = "\n\n".join(chunks)
        print(f"  合計テキスト: {len(combined)}文字")
        if combined:
            print(f"  先頭500文字サンプル:\n{combined[:500]}")
        return combined
    except Exception as e:
        print(f"  ページ取得エラー: {type(e).__name__}: {e}")
        return ""


def get_gunpla_raw() -> str:
    """ガンプラ情報の検索結果テキストを取得（カレンダー直取得→検索フォールバック）"""
    urls = find_calendar_urls()
    print(f"  カレンダーページ: {urls}")
    raw = extract_pages(urls)
    if raw:
        return raw
    # フォールバック: 従来の検索方式
    cat = config["gunpla_categories"][0]
    results = search_from_sites(
        cat["query"] + f" {today.year}年{today.month}月",
        cat["sites"],
        max_results=3,
        days=30,
    )
    return "\n".join(
        f"・{r['title']}（{r['url']}）\n  {r['content'][:200]}"
        for r in results
    ) or "検索結果なし"


def build_gunpla_message(raw: str) -> str:
    prompt = (
        f"以下のページ内容をもとに、ガンプラの新作・再販情報を日本語でまとめてください。\n"
        f"今日は{today_str}です。今月・来月の情報を優先してください。\n\n"
        f"【ページ内容】\n{raw}\n\n"
        f"## 出力フォーマット（このフォーマットのみ出力）\n"
        f"🆕 ガンプラ新作\n"
        f"① 商品名 — 発売日・価格\n　一言コメント\n"
        f"② 商品名 — 発売日・価格\n　一言コメント\n"
        f"③ 商品名 — 発売日・価格\n　一言コメント\n\n"
        f"🔄 ガンプラ再販\n"
        f"① 商品名 — 再販時期\n　一言コメント\n"
        f"② 商品名 — 再販時期\n　一言コメント\n"
        f"③ 商品名 — 再販時期\n　一言コメント\n\n"
        f"- ページ内容にある具体的な情報のみ使う\n"
        f"- 過去の日付（今日より前）の情報は使わないこと\n"
        f"- 情報がない場合は「現時点で情報なし」\n"
        f"- 余計な前置き・後書き不要"
    )
    return ask_groq(prompt)


def build_gunpla_events_json(raw: str) -> str:
    prompt = (
        f"以下のページ内容から、ガンプラの再販・新作発売予定をJSON形式で返してください。\n"
        f"今日は{today_str}です。\n\n"
        f"【ページ内容】\n{raw}\n\n"
        f"## 出力フォーマット（JSONのみ・余計な文字不要）\n"
        f'[\n  {{\n    "name": "商品名",\n    "date": "YYYY-MM-DD",\n    "description": "概要1〜2文"\n  }}\n]\n\n'
        f"- 発売日・再販日が明確なものだけ含める\n"
        f"- 今日より前の日付は除外\n"
        f"- 日付不明は除外\n"
        f"- 最大10件\n"
        f"- JSONのみ返すこと"
    )
    return ask_groq(prompt)


# ── Discord 既存イベント取得 ──────────────────────────
def get_existing_events() -> set:
    try:
        resp = requests.get(
            f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/scheduled-events",
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
            timeout=10,
        )
        resp.raise_for_status()
        return {e.get("name", "") for e in resp.json()}
    except Exception as e:
        print(f"  ⚠️ 既存イベント取得失敗: {e}")
        return set()


# ── Discord イベント作成 ──────────────────────────────
def create_discord_event(name: str, date_str: str, description: str, existing: set) -> bool:
    event_name = f"🔧 {name}"
    if event_name in existing:
        print(f"  スキップ（重複）: {name}")
        return False
    try:
        event_date = datetime.date.fromisoformat(date_str)
        if event_date < today:
            print(f"  スキップ（過去日付）: {name}")
            return False

        start_dt = datetime.datetime(
            event_date.year, event_date.month, event_date.day,
            1, 0, 0, tzinfo=datetime.timezone.utc
        )
        end_dt = start_dt + datetime.timedelta(hours=1)

        resp = requests.post(
            f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/scheduled-events",
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"},
            json={
                "name": event_name,
                "description": description,
                "scheduled_start_time": start_dt.isoformat(),
                "scheduled_end_time": end_dt.isoformat(),
                "privacy_level": 2,
                "entity_type": 3,
                "entity_metadata": {"location": "バンダイホビーサイト"},
            },
            timeout=10,
        )
        if resp.status_code == 200:
            print(f"  ✅ イベント作成: {name}")
            return True
        else:
            print(f"  ⚠️ スキップ: {name} ({resp.status_code})")
            return False
    except Exception as e:
        print(f"  ❌ イベント作成失敗: {name} / {e}")
        return False


def register_gunpla_events(raw: str) -> int:
    existing = get_existing_events()
    print(f"  既存イベント数: {len(existing)}")
    events_json = build_gunpla_events_json(raw)
    clean = events_json.replace("```json", "").replace("```", "").strip()
    try:
        items = json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"  ❌ JSONパース失敗: {e}")
        return 0
    count = 0
    for item in items:
        name = item.get("name", "")
        date_str = item.get("date", "")
        description = item.get("description", "")
        if name and date_str:
            if create_discord_event(name, date_str, description, existing):
                count += 1
    return count


# ── メイン ────────────────────────────────────────────
def run_news():
    print("📰 ニュースまとめ生成中...")
    posted_data = load_posted_urls()
    posted_data = cleanup_old_urls(posted_data)
    news_body, posted_data = build_news_message(posted_data)
    news_message = f"# 📰 朝のニュースまとめ｜{today_str}\n\n{news_body}"
    post_discord(DISCORD_NEWS_URL, news_message)
    print("✅ ニュース投稿完了")
    save_posted_urls(posted_data)


def run_gunpla():
    print("🔧 ガンプラ情報生成中...")
    raw = get_gunpla_raw()
    gunpla_body = build_gunpla_message(raw)
    post_discord(DISCORD_GUNPLA_URL, f"# 🔧 ガンプラ最新情報｜{today_str}\n\n{gunpla_body}")
    print("✅ ガンプラ投稿完了")
    print("📅 ガンプラ再販イベント登録中...")
    count = register_gunpla_events(raw)
    print(f"✅ イベント登録完了（{count}件）")


if __name__ == "__main__":
    mode = os.environ.get("RUN_MODE", "news")
    if mode == "gunpla":
        run_gunpla()
    elif mode == "all":
        run_news()
        run_gunpla()
    else:
        run_news()
