# ADR-007: v0.3 で `read_only: false` のブロックを解除し、worktree 隔離と組み合わせて許容する

- **Date:** 2026-04-21
- **Status:** Accepted

## Context

v0.1 では、`read_only: false` のタスクはマニフェストのパース時点で即座に `ManifestError` を送出していた（`_parse_task()` 内の明示的な拒否）。
理由は、ファイル書き込みを伴うタスクが同一の作業ディレクトリで並列実行されると、ファイル衝突・データ破損が起きるためである。
v0.3 では worktree 隔離（ADR-009）が実装され、書き込みタスクを安全に実行できる基盤が整った。

ADR-003 は「worktree 隔離は v0.3 以降に延期する」と決定しており、本 ADR はその延期が解消されたことを記録する。
ADR-005 は `SUPPORTED_PLAN_VERSIONS` によるバージョンゲートを管理しており、`read_only: false` を受理するバージョン範囲はそちらで管理する。

## Decision

`_parse_task()` から `read_only: false` に対する即時 `ManifestError` 送出を削除する。

`read_only` フィールドの型バリデーション（`None` / `int` / `"yes"` / `list` 等の不正型は依然 `ManifestError`）は維持する。
`read_only: false` は全バージョン（`0.1`, `0.2`, `0.3` 等）のマニフェストで受理する。

## Rationale

| 観点 | 理由 |
|---|---|
| worktree 隔離が実現した | ADR-009 で定義した worktree 隔離により、書き込みタスクは `.clade-worktrees/<id>-<uuid8>/` の独立ディレクトリで実行される。共有 cwd の衝突は発生しない |
| ブロック理由がなくなった | v0.1 の拒否は「安全な実行基盤が存在しないから」であって、`read_only: false` の意味論そのものを否定していたわけではない |
| 後方互換の維持 | `read_only` の型バリデーションは残すため、誤った型（`read_only: "false"` 文字列など）はこれまでどおり拒否される |
| バージョン横断サポート | ADR-006 の `writes:` と同様に、`read_only: false` を受理するバージョン範囲を限定しない。書き込みタスクが有意義かどうかはユーザーが判断する |

## Alternatives Considered

**v0.3 以降のマニフェストのみで許容する（`clade_plan_version: "0.3"` チェックを追加する）**: スキーマバージョンの細粒度管理は複雑度を高める。実行安全性は worktree 隔離で担保されており、バージョン制限は不要と判断した。

**`read_only: false` の拒否を維持し、代替フィールドを設ける**: API 拡張のコストが高い。既存フィールドの意味論を自然な方向に拡張する（ブロック解除）ほうが互換性が高い。

## ADR-003/005 との関係

- **ADR-003**（defer worktree isolation to v0.3）: v0.3 で worktree 隔離を実現するという計画が本 ADR で完遂された。ADR-003 の「延期」が解消されたことを示す。ADR-003 のステータスは「Superseded (by ADR-007/009)」に更新する。
- **ADR-005**（バージョンゲート）: `SUPPORTED_PLAN_VERSIONS` の管理方針に変更はない。`read_only: false` の許容はバージョンゲートの範囲外（タスクフィールドの意味論の問題）であり、ADR-005 は引き続き有効。

## Consequences

- `manifest.py` の `_parse_task()` から `read_only is False` チェックを削除する
- `read_only` の型バリデーション（`isinstance(read_only, bool)` のチェック）は継続する
- 既存テスト `test_read_only_falseのタスクがあるとManifestErrorが送出される` を削除し、型バリデーション系テストに置換する
- `runner.py` は `task.read_only` が `False` の場合に worktree 隔離（ADR-009）経路を選択する
