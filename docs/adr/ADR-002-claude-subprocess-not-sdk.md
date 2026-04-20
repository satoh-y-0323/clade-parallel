# ADR-002: Use `claude -p` subprocess instead of Claude Code SDK

- **Date:** 2026-04-20
- **Status:** Accepted

## Context

clade-parallel は Clade エージェントを起動する方法として以下の候補を検討した:

- `claude -p "<プロンプト>"` を `subprocess` で呼び出す
- Claude Code SDK / ライブラリを直接 import して呼び出す

要件として「Clade 本体を import しない（疎結合）」という制約が存在する。

## Decision

`claude -p` を `subprocess.run` で呼び出す方式を採用する。

## Rationale

| 観点 | 理由 |
|---|---|
| 疎結合 | Clade の Python パッケージを import しないため、Clade 側の内部変更が clade-parallel に波及しない |
| 依存最小化 | SDK 依存を追加せずに済む。ランタイム依存は PyYAML のみに抑えられる |
| 標準的な使い方 | `claude -p` は Claude Code CLI の公式インターフェースであり、安定している |
| 完了検知の単純さ | exit code（0 = 成功、非 0 = 失敗）のみを信頼すればよく、stdout 解析や API レスポンスのパースが不要 |

## Alternatives Considered

**Claude Code SDK / ライブラリ呼び出し**: API が豊富で進捗取得が容易だが、Clade を直接 import することになり疎結合の要件に反する。Clade のバージョンアップのたびに互換性確認が必要になる。

## Consequences

- エージェントの実行進捗（途中出力）を clade-parallel から取得することはできない。stdout は全量を終了後に受け取るのみ
- `claude` コマンドが PATH 上に存在しない環境では `RunnerError` となる（`--claude-exe` オプションで実行ファイルパスを指定可能）
- エージェント側のレポート出力（`.claude/reports/` 配下）はエージェント自身の責務であり、clade-parallel は関与しない
