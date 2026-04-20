# /status コマンド

現在のセッション状態・適用中のルール・インスティンクト蓄積状況を表示する。

## 実行手順
1. 現在のエージェントと適用ルールを確認する
2. セッションファイルの存在を確認:
   Glob ツールで `.claude/memory/sessions/*.tmp` を検索する
3. 観察データの蓄積状況を確認:
   - Read ツールで `.claude/instincts/raw/observations.jsonl` を読み込み行数をカウントする
   - Glob ツールで `.claude/instincts/clusters/*.json` を検索する
   - Glob ツールで `.claude/skills/project/*.md` を検索する
4. 以下の形式で表示する:

---
## 現在のステータス

### セッション
- 本日: {YYYYMMDD}
- 最新セッションファイル: {ファイル名 or なし}
- 残タスク数: {件数}

### 適用中の設定
- エージェント: {名前 or 未選択}
- 適用ルール: {ファイル一覧}
- 有効スキル: {スキル一覧}

### 継続学習の状態
- 観察データ: {行数}件蓄積済み
- プロジェクト固有スキル: {件数}個
- プロジェクト固有ルール（個別）: {件数}個
- インスティンクトクラスタ: {件数}個

### 利用可能なコマンド
- `/agent-interviewer` — 要件ヒアリング担当
- `/agent-architect` — 設計担当
- `/agent-planner` — 計画立案・タスク割り振り担当
- `/agent-developer` — 実装担当
- `/agent-tester` — テスト担当
- `/agent-code-reviewer` `/agent-security-reviewer` — レビュー担当
- `/init-session` — セッション復元
- `/end-session` — セッション保存
- `/cluster-promote` — インスティンクト昇格
- `/promote` — グローバル展開
---
