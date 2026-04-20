# MCP サーバ スキル

## 概要

このプロジェクトには Anthropic / modelcontextprotocol 公式が提供する MCP サーバが設定されています。
すべて `npx -y` によるオンデマンド実行のため、グローバルインストール不要です。

設定ファイル:
- `.claude/settings.json` — サーバ定義（commit 対象・シークレットなし）
- `.claude/settings.local.json` — 認証情報（gitignore 済み・ローカル専用）

---

## 提供されるツール一覧

### filesystem（認証不要）

プロジェクトルート配下のファイルを安全に操作します。
アクセス許可ディレクトリ: プロジェクトルート（`.`）

| ツール名 | 説明 | 使用場面 |
|---|---|---|
| read_text_file | テキストファイルを読み込む | ソースコード確認・ログ参照 |
| read_media_file | 画像・音声ファイルを base64 で読み込む | 画像確認 |
| read_multiple_files | 複数ファイルを一括読み込み | 関連ファイルの一括確認 |
| write_file | ファイルを作成・上書き | 新規ファイル作成 |
| edit_file | パターンマッチで部分編集（dry-run 対応） | ピンポイント修正 |
| create_directory | ディレクトリを作成 | フォルダ構造の構築 |
| list_directory | ディレクトリ一覧を表示 | 構成確認 |
| list_directory_with_sizes | サイズ付きで一覧表示 | ディスク使用量確認 |
| move_file | ファイル・ディレクトリを移動・リネーム | リファクタリング |
| search_files | glob パターンでファイル検索 | 関連ファイル探索 |
| directory_tree | ディレクトリ構造を JSON で取得 | プロジェクト全体把握 |
| get_file_info | タイムスタンプ・パーミッション等のメタデータ取得 | ファイル属性確認 |
| list_allowed_directories | アクセス許可ディレクトリを確認 | 権限確認 |

---

### memory（認証不要）

ナレッジグラフ型の永続メモリシステムです。
エンティティ（概念・人・モノ）と関係性を記録し、セッションをまたいで参照できます。
データは `memory.jsonl` に保存されます。

| ツール名 | 説明 | 使用場面 |
|---|---|---|
| create_entities | エンティティを追加する | 新しい概念・要件を記録 |
| create_relations | エンティティ間の関係を作成する | 依存関係・関連性を記録 |
| add_observations | 既存エンティティに事実を追記する | 調査結果・メモを蓄積 |
| delete_entities | エンティティと関係を削除する | 不要な記録を整理 |
| delete_observations | 特定の事実を削除する | 古い情報を更新 |
| delete_relations | 関係を削除する | 関連性の見直し |
| read_graph | ナレッジグラフ全体を取得する | 全記録の俯瞰 |
| search_nodes | 名前・型・内容でエンティティを検索する | 特定情報の参照 |
| open_nodes | 名前指定でエンティティを取得する | 詳細確認 |

---

### sequential-thinking（認証不要）

段階的な思考プロセスを通じて問題解決を支援します。
複雑な要件分析・設計判断・デバッグに有効です。

| ツール名 | 説明 | 使用場面 |
|---|---|---|
| sequential_thinking | ステップごとに思考を展開する | 複雑な問題の分解・分析 |

思考ステップは修正・分岐が可能で、途中で方針を変えることができます。

---

## このプロジェクトでの使い方

- **filesystem** は Claude Code 組み込みのファイルツールが主体ですが、`directory_tree` や `get_file_info` など組み込みにない操作で活用できます。
- **memory** はプロジェクト固有の用語・設計判断・よくある質問を記録しておくことでセッション間の文脈引き継ぎに使えます。
- **sequential-thinking** は要件が複雑な場合や設計の選択肢を整理したい場面で積極的に使ってください。
- **GitHub 操作**（Issue/PR 作成・Actions 確認等）は `gh` CLI を Bash ツール経由で実行します。未認証の場合は `gh auth login` を実行してください。

---

## 使用例

```
# ナレッジグラフにプロジェクトの制約を記録してほしい
memory の create_entities を使って「このプロジェクトでは .NET 8 を使う」という制約を記録してください。

# 段階的に要件を分析してほしい
sequential-thinking を使って、この機能追加リクエストを段階的に分析してください。

# GitHub の Issue を確認したい
gh issue list コマンドで open な Issue を一覧表示してください。

# PR を作成したい
gh pr create コマンドで PR を作成してください。
```
