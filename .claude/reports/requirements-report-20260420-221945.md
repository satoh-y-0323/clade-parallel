# 要件定義レポート

## ヒアリング日時
2026-04-20

## 作業種別
新規開発

## ユーザーの要望（原文）

> Claude Code 上で動く Clade というフレームワーク用の並列実行ラッパー `clade-parallel` を Python で開発する。v0.1 は read-only 並列（レビュー/監査/調査）をゴールとする。

> Clade には `code-reviewer` と `security-reviewer` が実装されている。両方ともソースを読み取って診断するレビュアーで、並列で動かしても問題ないが、Clade の設計上は並列実行ができないため逐次実行となり、大量コードのレビューで時間がかかる。並列実行が可能になれば、それだけで時間短縮につながる。

> `code-reviewer` と `security-reviewer` を同時実行させて、両方のレポートが生成される。かつ race condition 問題も発生しない。

## 整理した要件

### 機能要件（何をしてほしいか）

- **マニフェスト読み込み**: Clade の planner が生成する YAML フロントマター付き Markdown マニフェストを読み込む
- **並列実行制御**: マニフェストに定義されたエージェント（read-only タスク）を並列実行する
- **v0.1 スコープ: read-only 並列のみ**: ファイル書き込みを伴わないタスク（レビュー・監査・調査）のみを並列実行対象とする
- **レポート収集**: 並列実行した各エージェントが出力したレポートを収集し、結果として返す
- **race condition の防止**: 複数エージェントが同時にファイルや状態を参照する際に競合が発生しないよう制御する
- **スキーマバージョン管理**: マニフェストの `clade_plan_version` フィールドでスキーマバージョンを識別・検証する（v0.x は破壊的変更 OK、v1.0 で凍結予定）

### ユースケース（v0.1 の具体的シナリオ）

1. Clade の planner が YAML フロントマター付き Markdown マニフェストを生成する
2. `clade-parallel` がマニフェストを読み込み、read-only タスクを識別する
3. `code-reviewer` と `security-reviewer` を並列で起動する
4. 両レポート（`code-review-report-*.md` / `security-review-report-*.md`）が生成される
5. `clade-parallel` が完了を検知して終了する

### ロードマップ（参考: 将来バージョンの方向性）

| バージョン | 追加機能 |
|---|---|
| v0.1（今回） | read-only 並列のみ（書込衝突リスクなし） |
| v0.2 以降 | writes 宣言 + 静的衝突チェック |
| 将来 | worktree 隔離 + sequential マージ / `depends_on` / リトライ・部分再開・テレメトリ |

### 非機能要件（パフォーマンス・セキュリティ・使いやすさ等）

- **クロスプラットフォーム対応**: Windows / macOS / Linux すべてで動作すること
- **依存ライブラリ最小化**: 標準ライブラリを優先し、サードパーティ依存を最小限にする
- **疎結合設計**: Clade のコードを import しない。公開データスキーマ（マニフェスト Markdown）を契約として使用する
- **Python 3.10+**: coding-conventions.md の規約に従い Python 3.10 以上を対象とする

### 完了条件・成功基準

- `code-reviewer` と `security-reviewer` が同時実行（並列）される
- 両方のレポートファイル（`code-review-report-*.md`、`security-review-report-*.md`）が `.claude/reports/` に正常生成される
- race condition が発生しない（複数エージェントが同時アクセスしても結果が一貫している）
- Windows / macOS / Linux で動作確認できる（またはテストが通る）

### 優先度・緊急度

高 — 今日中に完成させたい。急ぎの要件。

### 制約条件・やってほしくないこと

- Clade のソースコードを import してはならない（疎結合を守る）
- サードパーティライブラリの依存を増やしすぎない（標準ライブラリ優先）
- v0.1 では write を伴うタスクの並列実行はサポートしない（スコープ外）
- コーディング規約（`coding-conventions.md`）に従うこと:
  - Python 3.10+、PEP 8 / 257 / 484
  - Black / Ruff でフォーマット（行長 88 文字）
  - 型アノテーション必須（public API）
  - pytest でテスト

## 現状分析

新規開発のため既存コードの分析は対象外。

## architectへの引き継ぎ事項

### 深堀りしてほしい点

1. **マニフェストのスキーマ設計**: YAML フロントマター付き Markdown マニフェストの具体的なフィールド定義（タスク一覧・read-only フラグ・エージェント識別子・`clade_plan_version` の書式など）
2. **並列実行の実装方式**: Python の `concurrent.futures`（`ThreadPoolExecutor` / `ProcessPoolExecutor`）か `asyncio` か、あるいは subprocess 並列か。Claude Code CLI のサブプロセス起動方式との相性を検討する
3. **レポート出力の競合回避策**: 複数エージェントが `.claude/reports/` に同時書き込みする場合のファイル名衝突・書き込み競合の防止方法（タイムスタンプの一意性、ロックの要否）
4. **エージェント起動インタフェース**: `clade-parallel` が各エージェント（Claude Code CLI）をどのように起動するか（コマンドライン引数・環境変数・作業ディレクトリの渡し方）
5. **完了検知の方式**: 各エージェントの完了をどのように検知するか（exit code・ファイル存在確認・stdout 解析）
6. **エラーハンドリング**: 一方のエージェントが失敗した場合の挙動（もう一方を継続するか・全体を失敗とするか）
7. **v0.1 の「read-only 判定」方法**: マニフェスト内でタスクが read-only であることをどのフィールドで判定するか

### 確認済みの制約

- Clade の並列エージェント呼び出しは race condition を避けるために現状は逐次実行のみ（`core.md` に明記されている）
- `.claude/reports/` がレポートの出力先として既存プロジェクトで使用されている
- タイムスタンプ形式は `YYYYMMDD-HHmmss`（既存レポートファイル名の命名規則）
- `settings.local.json` に `isolation: "worktree"` の設定が必要（並列エージェントが動作するための権限設定）
- コーディング規約は `src/<package_name>/` レイアウト、テストは `tests/` に pytest で配置
