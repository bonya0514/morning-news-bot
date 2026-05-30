"""
Morning News Bot
- Tavily でニュース収集
- Groq でまとめ生成
- Discord Webhook に投稿
- ガンプラ再販予定を Discord イベントに登録
"""

import os
import json
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

today     = datetime.date.today()
today_str = today.strftime("%Y年%m月%d日")

# ── Groq クライアント ─────────────────────────────────
client = Groq(api_key=GROQ_API_KEY)


def search_news(query: str, max_results: int = 5) -> str:
    """Tavilyでニュースを検索してテキストにまとめる"""
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "topic": "news",
                "max_results": max_results,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return "検索結果なし"
        lines = []
        for r in results:
            lines.append(f"・{r.get('title', '')}（{r.get('url', '')}）\n  {r.get('content', '')[:200]}")
        return "\n".join(lines)
    except Exception as e:
        print(f"検索エラー: {type(e).__name__}: {e}")
        return "検索失敗"


def ask_groq(prompt: str) -> str:
    """Groq にプロンプトを投げてテキストを返す"""
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return response.choices[0].message.content.strip()


def post_discord(webhook_url: str, content: str) -> None:
    """Discord Webhook に投稿（2000文字制限を考慮して分割）"""
    chunks = [content[i:i+1900] for i in range(0, len(content), 1900)]
    for chunk in chunks:
        resp = requests.post(
            webhook_url,
            json={"content": chunk},
            timeout=10,
        )
        resp.raise_for_status()


# ── ニュースまとめ生成 ────────────────────────────────
def build_news_message() -> str:
    categories = config["news_categories"]
    sections = []
    for cat in categories:
        print(f"  検索中: {cat['name']}")
        raw = search_news(cat["query"] + f" {today.year}")
        prompt = f"""以下の検索結果をもとに、「{cat['name']}」の最新ニュースを日本語で3件にまとめてください。

【検索結果】
{raw}

## 出力フォーマット（このフォーマットのみ出力）
{cat['emoji']} {cat['name']}
① タイトル
　概要を1〜2文で。

② タイトル
　概要を1〜2文で。

③ タイトル
　概要を1〜2文で。

- 検索結果にある具体的な情報のみ使う
- 余計な前置き・後書き不要
"""
        sections.append(ask_groq(prompt))
    return "\n\n".join(sections)


def build_gunpla_message() -> str:
    categories = config["gunpla_categories"]
    sections = []
    for cat in categories:
        print(f"  検索中: {cat['name']}")
        raw = search_news(cat["query"] + f" {today.year}")
        prompt = f"""以下の検索結果をもとに、「{cat['name']}」情報を日本語でまとめてください。

【検索結果】
{raw}

## 出力フォーマット（このフォーマットのみ出力）
{cat['emoji']} {cat['name']}
① 商品名 — 発売日・価格など
　一言コメント

- 検索結果にある具体的な情報のみ使う
- 情報がない場合は「現時点で情報なし」
- 余計な前置き・後書き不要
"""
        sections.append(ask_groq(prompt))
    return "\n\n".join(sections)


def build_gunpla_events_json() -> str:
    raw = search_news(f"ガンプラ 再販 新作 発売日 {today.year}")
    prompt = f"""以下の検索結果から、ガンプラの再販・新作発売予定をJSON形式で返してください。

【検索結果】
{raw}

## 出力フォーマット（JSONのみ・余計な文字不要）
[
  {{
    "name": "商品名",
    "date": "YYYY-MM-DD",
    "description": "概要1〜2文"
  }}
]

- 発売日・再販日が明確なものだけ含める
- 日付不明は除外
- 最大10件
- JSONのみ返すこと
"""
    return ask_groq(prompt)


# ── Discord イベント作成 ──────────────────────────────
def create_discord_event(name: str, date_str: str, description: str) -> bool:
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

        payload = {
            "name": f"🔧 {name}",
            "description": description,
            "scheduled_start_time": start_dt.isoformat(),
            "scheduled_end_time": end_dt.isoformat(),
            "privacy_level": 2,
            "entity_type": 3,
            "entity_metadata": {"location": "バンダイホビーサイト"},
        }

        resp = requests.post(
            f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/scheduled-events",
            headers={
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
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


def register_gunpla_events() -> int:
    raw = build_gunpla_events_json()
    clean = raw.replace("```json", "").replace("```", "").strip()
    try:
        items = json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"  ❌ JSONパース失敗: {e}")
        return 0

    count = 0
    for item in items:
        name        = item.get("name", "")
        date_str    = item.get("date", "")
        description = item.get("description", "")
        if name and date_str:
            if create_discord_event(name, date_str, description):
                count += 1
    return count


# ── メイン ────────────────────────────────────────────
def run_news():
    print("📰 ニュースまとめ生成中...")
    news_body = build_news_message()
    news_message = f"# 📰 朝のニュースまとめ｜{today_str}\n\n{news_body}"
    post_discord(DISCORD_NEWS_URL, news_message)
    print("✅ ニュース投稿完了")


def run_gunpla():
    print("🔧 ガンプラ情報生成中...")
    gunpla_body = build_gunpla_message()
    gunpla_message = f"# 🔧 ガンプラ最新情報｜{today_str}\n\n{gunpla_body}"
    post_discord(DISCORD_GUNPLA_URL, gunpla_message)
    print("✅ ガンプラ投稿完了")

    print("📅 ガンプラ再販イベント登録中...")
    count = register_gunpla_events()
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
