---
clade_plan_version: "0.1"
name: "Parallel Reviewers Demo"
tasks:
  - id: code-review
    agent: code-reviewer
    read_only: true
    cwd: ..
    prompt: |
      ## 作業依頼
      コードレビューレポートの作成（E2E 自動実行）

      ## 上流レポートのパス
      - 最新の requirements-report / architecture-report / plan-report / test-report を Glob で取得して Read すること

      ## レビュー対象
      現在の git 管理下の src/ と tests/ 配下のファイル群（最新コミット基準）

      ## 特記事項
      自動実行（非対話モード）のため Q&A は不要。デフォルト設定で進めてください。

      ## 出力指示
      - 出力先: `.claude/reports/code-review-report-*.md`（write-report.js 経由）
      - 最終メッセージにレポートファイルパスを必ず含めること
      - レポート生成後は終了すること
    timeout_sec: 1200
  - id: security-review
    agent: security-reviewer
    read_only: true
    cwd: ..
    prompt: |
      ## 作業依頼
      セキュリティレビューレポートの作成（E2E 自動実行）

      ## 上流レポートのパス
      - 最新の requirements-report / architecture-report / plan-report / test-report を Glob で取得して Read すること

      ## レビュー対象
      現在の git 管理下の src/ と tests/ 配下のファイル群（最新コミット基準）

      ## レビュー観点
      OWASP Top 10 / コマンドインジェクション / 機密漏洩 / 入力値バリデーション / 依存ライブラリの脆弱性 等、セキュリティ全般

      ## 特記事項
      自動実行（非対話モード）のため Q&A は不要。デフォルト設定で進めてください。

      ## 出力指示
      - 出力先: `.claude/reports/security-review-report-*.md`（write-report.js 経由）
      - 最終メッセージにレポートファイルパスを必ず含めること
      - レポート生成後は終了すること
    timeout_sec: 1200
---

# Parallel Reviewers Demo

> **Note:** This manifest uses `--agent` option to directly invoke the Clade framework's `code-reviewer` and `security-reviewer` agents. It requires the Clade framework to be present in the working directory (or above) and the `claude` CLI to have access to those agent definitions. If you do not have Clade set up, start with `examples/manifest-hello.md` instead, which uses only general-purpose prompts.

このマニフェストは、Clade の `code-reviewer` と `security-reviewer` の 2 エージェントを
並列実行するための最小構成サンプルです。

## 用途

`clade-parallel` の E2E 動作確認として使用します。
両エージェントが同時に起動され、それぞれのレビューレポートが `.claude/reports/` に
ほぼ同時刻のタイムスタンプで出力されることで、並列実行が成立していることを確認できます。

## プロンプト設計

各タスクのプロンプトは **`--agent` オプション経由で直接起動する 1 層構造** を採用しています。
`claude -p --agent <name>` で指定したエージェントに実際のレビュー指示を直接渡すため、
wrapper プロンプト（Agent ツール呼び出し）は不要です。

- `agent: code-reviewer` → `claude --agent code-reviewer -p "<実際の指示>"` として起動
- `agent: security-reviewer` → `claude --agent security-reviewer -p "<実際の指示>"` として起動

## 実行方法

```bash
# リポジトリルートで実行
clade-parallel run examples/manifest.md

# 出力を抑制してサマリーのみ表示
clade-parallel run examples/manifest.md --quiet
```

## 注意点

- `timeout_sec: 1200`（20 分）に設定しています。Claude Code のエージェント実行には
  数分かかる場合があります。環境に合わせて調整してください。
- 実行には `claude` CLI が `PATH` に含まれている必要があります。
  カスタムパスの場合は `--claude-exe` オプションで指定してください。
- `read_only: true` のため、エージェントはファイルを書き込まず、レポート出力のみ行います。
