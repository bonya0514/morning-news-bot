"""
Morning News Bot
- Gemini API でニュース収集・まとめ生成
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
DISCORD_NEWS_URL     = os.environ["DISCORD_WEBHOOK_NEWS"]
DISCORD_GUNPLA_URL   = os.environ["DISCORD_WEBHOOK_GUNPLA"]
DISCORD_BOT_TOKEN    = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_GUILD_ID     = os.environ["DISCORD_GUILD_ID"]

today     = datetime.date.today()
today_str = today.strftime("%Y年%m月%d日")

# ── Groq クライアント ─────────────────────────────────
client = Groq(api_key=GROQ_API_KEY)

def ask_gemini(prompt: str) -> str:
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
def build_news_prompt() -> str:
    categories = config["news_categories"]
    cat_lines = "\n".join(
        f'- {c["emoji"]} {c["name"]}（検索ワード: {c["query"]}）'
        for c in categories
    )
    return f"""今日（{today_str}）の以下カテゴリの最新ニュースを日本語で簡潔にまとめてください。

{cat_lines}

## 出力フォーマット（必ずこの形式で）
各カテゴリごとに以下の形式で出力：

【絵文字 カテゴリ名】
① タイトル
　概要を1〜2文で。

② タイトル
　概要を1〜2文で。

③ タイトル
　概要を1〜2文で。

- 各カテゴリ3件程度
- 箇条書きで読みやすく
- 余計な前置き不要、本文のみ出力
"""


def build_gunpla_prompt() -> str:
    categories = config["gunpla_categories"]
    cat_lines = "\n".join(
        f'- {c["emoji"]} {c["name"]}（検索ワード: {c["query"]}）'
        for c in categories
    )
    return f"""今日（{today_str}）時点のガンプラ情報を日本語でまとめてください。

{cat_lines}

## 出力フォーマット（必ずこの形式で）

【🆕 新作情報】
① 商品名 — 発売日・価格
　一言コメント

【🔄 再販・受注情報】
① 商品名 — 再販日・予約締切など
　一言コメント

- 情報がない場合は「現時点で情報なし」と記載
- 余計な前置き不要、本文のみ出力
"""


def build_gunpla_event_prompt() -> str:
    """イベント登録用：再販情報をJSON形式で取得するプロンプト"""
    return f"""今日（{today_str}）以降のガンプラ再販・新作発売予定をJSON形式で返してください。
検索して実際の情報を取得してください。

## 出力フォーマット（JSONのみ・余計な文字不要）
[
  {{
    "name": "商品名",
    "date": "YYYY-MM-DD",
    "description": "再販・新作の概要（1〜2文）"
  }},
  ...
]

- 発売日・再販日が明確なものだけ含める
- 日付不明なものは除外
- 最大10件まで
- JSONのみ返すこと。```json などのマークダウン記法は不要
"""


# ── Discord イベント作成 ──────────────────────────────
def create_discord_event(name: str, date_str: str, description: str) -> bool:
    """
    Discord のサーバーイベントを作成する
    date_str: "YYYY-MM-DD" 形式
    """
    try:
        event_date = datetime.date.fromisoformat(date_str)
        # 過去日付はスキップ
        if event_date < today:
            print(f"  スキップ（過去日付）: {name} ({date_str})")
            return False

        # イベント開始: 当日10:00 JST（UTC+9）
        start_dt = datetime.datetime(
            event_date.year, event_date.month, event_date.day,
            1, 0, 0,  # UTC 01:00 = JST 10:00
            tzinfo=datetime.timezone.utc
        )
        end_dt = start_dt + datetime.timedelta(hours=1)

        payload = {
            "name": f"🔧 {name}",
            "description": description,
            "scheduled_start_time": start_dt.isoformat(),
            "scheduled_end_time": end_dt.isoformat(),
            "privacy_level": 2,       # GUILD_ONLY
            "entity_type": 3,         # EXTERNAL
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
            print(f"  ✅ イベント作成: {name} ({date_str})")
            return True
        elif resp.status_code == 400:
            # 同名イベントが既に存在する場合など
            print(f"  ⚠️ イベント作成スキップ: {name} ({resp.json()})")
            return False
        else:
            resp.raise_for_status()

    except Exception as e:
        print(f"  ❌ イベント作成失敗: {name} / {e}")
        return False


def register_gunpla_events() -> int:
    """再販・新作情報をGeminiで取得してDiscordイベントに登録、登録件数を返す"""
    raw = ask_gemini(build_gunpla_event_prompt())

    # JSONパース（```json フェンスが混入した場合も除去）
    clean = raw.replace("```json", "").replace("```", "").strip()
    try:
        items = json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"  ❌ JSONパース失敗: {e}\n  raw: {raw[:200]}")
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
def main():
    # ── ニュース投稿 ──────────────────────────────────
    print("📰 ニュースまとめ生成中...")
    news_body = ask_gemini(build_news_prompt())
    news_message = f"# 📰 朝のニュースまとめ｜{today_str}\n\n{news_body}"
    post_discord(DISCORD_NEWS_URL, news_message)
    print("✅ ニュース投稿完了")

    # ── ガンプラ投稿 ──────────────────────────────────
    print("🔧 ガンプラ情報生成中...")
    gunpla_body = ask_gemini(build_gunpla_prompt())
    gunpla_message = f"# 🔧 ガンプラ最新情報｜{today_str}\n\n{gunpla_body}"
    post_discord(DISCORD_GUNPLA_URL, gunpla_message)
    print("✅ ガンプラ投稿完了")

    # ── ガンプラ再販イベント登録 ──────────────────────
    print("📅 ガンプラ再販イベント登録中...")
    count = register_gunpla_events()
    print(f"✅ イベント登録完了（{count}件）")


if __name__ == "__main__":
    main()
