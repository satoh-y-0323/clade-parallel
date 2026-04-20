# Claude Code Project Configuration

## Startup Protocol
セッション開始時に実行すること:
1. **`/init-session` を実行する**: セットアップ警告チェック・前回セッション読み込み・残タスク提示をすべて処理する
2. **実行環境を確認する（下記「実行環境チェック」を参照）**
3. エージェントを選択する: `/agent-developer` / `/agent-architect` / `/agent-code-reviewer` / `/agent-security-reviewer`

## 実行環境チェック

> **注記:** CLI での実行を推奨します。VS Code 拡張には現在バグがあり、並列バックグラウンドエージェントが正常動作しません。

### VS Code 拡張から起動された場合

システムプロンプトに `VSCode Extension Context` が含まれている場合は VS Code 拡張から起動されていると判断し、以下をユーザーに伝えて確認を取る:

```
⚠️ VS Code 拡張から起動されています。

VS Code 拡張には現在バグがあり、エージェントを並列でバックグラウンド実行すると
確認ダイアログが表示できず、処理が正常に完了しません。

CLI からの実行を推奨しますが、逐次実行（並列なし）で続けることもできます。

逐次実行で続けますか？
  [yes] 逐次実行モードで作業を続行する（並列バックグラウンド実行は行いません）
  [no]  CLI への切り替え案内を表示して終了する
```

- **yes の場合**: 以降のセッション中、エージェントの並列・バックグラウンド実行を一切行わない。逐次実行のみ使用する。
- **no の場合**: 以下を案内してセッションを終了する:
  1. VS Code の統合ターミナルを開く（`Ctrl+\`` / `Cmd+\``）
  2. `claude` コマンドを実行して CLI を起動する
  3. `/terminal-setup` を実行して `Shift+Enter` による複数行入力を設定する
  4. `/init-session` を実行して前回のセッション状態を復元する

### CLI から起動された場合

通常通りの挙動。環境チェックのメッセージは表示しない。

## 自動実行される hooks
| イベント | スクリプト | 目的 |
|---|---|---|
| PreToolUse | `.claude/hooks/pre-tool.js` | 危険コマンドガード |
| PostToolUse | `.claude/hooks/post-tool.js` | Bash コマンド結果を bash-log.jsonl に記録 |
| Stop | `.claude/hooks/stop.js` | セッションファイル雛形を作成 |
| PreCompact | `.claude/hooks/pre-compact.js` | 圧縮前のセッション状態保存 |

## 設定ファイル

### `settings.json`
リポジトリにコミットされるプロジェクト共通設定。権限・hooks・sandbox・MCPサーバを定義する。

### `settings.local.json`（並列開発に必須）
リポジトリにコミットしないユーザー固有設定（`.gitignore` に追加すること）。`isolation: "worktree"` エージェントが正常動作するために必要。

> **重要:** `isolation: "worktree"` エージェントは `settings.json` を読まず、`settings.local.json` のみを参照する。このファイルがないと、並列エージェントはファイル書き込みや git コマンドの権限を持てず、処理に失敗する。

セットアップスクリプト（`setup.sh` / `setup.ps1`）実行時に `.claude/settings.local.json.example` から自動配置される。手動で作成する場合は以下の内容で:

```json
{
  "permissions": {
    "allow": [
      "Read(**)",
      "Write(**)",
      "Edit(**)",
      "Glob(**)",
      "Grep(**)",
      "Bash(git:*)",
      "Bash(node*)"
    ]
  }
}
```

## Language
ユーザーとの応答は必ず日本語で行うこと。コード・コマンド・ファイルパスは除く。

## User Rules
<!-- /cluster-promote によって自動追記される -->
@rules/local.md

<!-- CLADE:START -->
## Global Rules (Clade 管理)
@rules/core.md

## Available Agents
エージェントはカスタムコマンドで選択する:
- `/agent-interviewer`        → 要件ヒアリング担当（requirements-report 出力・ソース編集不可）
- `/agent-architect`          → 設計・アーキテクチャ担当（architecture-report 出力）
- `/agent-planner`            → 計画立案・タスク割り振り担当（plan-report 出力・ソース編集不可）
- `/agent-developer`          → 実装・デバッグ担当（テスト作成はtesterが行う）
- `/agent-tester`             → テスト仕様設計・実行・結果報告担当（ソース編集不可）
- `/agent-code-reviewer`      → コード品質・保守性・パフォーマンスのレビュー担当（ソース編集不可）
- `/agent-security-reviewer`  → セキュリティ脆弱性診断担当（ソース編集不可）

### ユーティリティエージェント（標準ワークフロー外・単独完結）
- `/agent-project-setup`      → コーディング規約の設定・coding-conventions.md 生成担当
- `/agent-mcp-setup`          → MCPサーバの調査・接続設定・スキルファイル生成担当
- `/agent-workflow-builder`   → 業務ヒアリングからエージェント群を自動生成するメタエージェント
- `/agent-doc-writer`         → Mermaid図・README・操作手順書・API仕様書などのドキュメント生成担当
<!-- CLADE:END -->

## User Agents
<!-- /agent-workflow-builder によって自動追記される -->

## Project Context
プロジェクト固有スキルが存在する場合、各エージェントが起動時に Glob で読み込む:
- `.claude/skills/project/` 配下のファイル

## Notes
- セッション終了時は必ず `/end-session` を実行すること
- パターン昇格は `/cluster-promote` で行う
- グローバル展開は `/promote` で行う
