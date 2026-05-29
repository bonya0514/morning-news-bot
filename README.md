# 🤖 Morning News Bot

毎朝7時にニュースまとめ＆ガンプラ情報をDiscordに自動投稿するBot。

## 構成

```
GitHub Actions（毎朝7時 JST）
→ Gemini API でニュース収集・まとめ生成
→ Discord Webhook × 2 に投稿
  ├── #ニュース用チャンネル
  └── #ガンプラ再販チャンネル
```

## セットアップ手順

### 1. リポジトリ作成
GitHubで新しいリポジトリを作成してこのファイル一式をpush。

### 2. Secrets登録
GitHubリポジトリの `Settings → Secrets and variables → Actions` で以下を登録：

| Secret名 | 内容 |
|---|---|
| `GEMINI_API_KEY` | Google AI StudioのAPIキー |
| `DISCORD_WEBHOOK_NEWS` | ニュース用チャンネルのWebhook URL |
| `DISCORD_WEBHOOK_GUNPLA` | ガンプラ用チャンネルのWebhook URL |
| `DISCORD_BOT_TOKEN` | Discord Developer PortalのBotトークン |
| `DISCORD_GUILD_ID` | DiscordサーバーID（開発者モードで右クリックコピー）|

### 3. 動作確認
`Actions` タブ → `Morning News Bot` → `Run workflow` で手動実行してテスト。

## ニュースカテゴリの変更

`config.yml` を編集するだけでOK。

```yaml
news_categories:
  - name: テクノロジー・AI
    emoji: 🤖
    query: "テクノロジー AI 最新ニュース"  # ← ここを変えるだけ
```

カテゴリの追加・削除・検索ワードの変更が自由にできる。

## 投稿時刻の変更

`.github/workflows/morning-news.yml` の cron を変更：

```yaml
# 毎朝6時（JST）にしたい場合
- cron: "0 21 * * *"  # UTC = JST - 9時間
```
