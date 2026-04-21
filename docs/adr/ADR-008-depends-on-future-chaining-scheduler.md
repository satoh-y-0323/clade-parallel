# ADR-008: `depends_on` の依存スケジューリングに Future 連鎖方式を採用する

- **Date:** 2026-04-21
- **Status:** Accepted

## Context

clade-parallel v0.3 では、タスク間の依存関係を表す `depends_on` フィールドを導入する。
依存するタスクが完了してから依存先を実行する必要があり、依存関係のない独立タスクは並列実行を維持したい。

スケジューリング方式として以下を検討した:

1. **トポロジカルソート直列化**: DAG をトポロジカルソートし、ソート順に逐次実行する
2. **Future 連鎖 (`FIRST_COMPLETED` ループ)**: `concurrent.futures.wait(FIRST_COMPLETED)` でタスク完了を監視し、ready なタスクを逐次 submit する
3. **`asyncio` によるイベントループ**: `asyncio.gather` / コルーチンで依存グラフを駆動する

ADR-001 では `ThreadPoolExecutor + subprocess.run` を採用しており、スケジューリング方式はこの前提と整合する必要がある。

## Decision

`concurrent.futures.wait(return_when=FIRST_COMPLETED)` を用いた Future 連鎖方式（`_DependencyScheduler` クラス）を採用する。

`_DependencyScheduler` は以下を担う:
- `indegree`（依存元数）と `reverse_deps`（逆依存マップ）を `__init__` で構築する
- 実行ループで完了した Future を回収するたびに、依存先タスクの `indegree` を減算し、0 になったものを即 submit する
- 依存元が失敗（`ok=False`）した場合、依存先を `skipped=True` の `TaskResult` として即時確定させ、推移的な skip を伝播させる
- `execute_fn` を引数で注入できる設計にし、テスト時に fake に差し替えられるようにする（DI）

## Rationale

| 観点 | 理由 |
|---|---|
| ADR-001 の前提と整合 | `ThreadPoolExecutor` を維持したまま依存制御が実現できる。asyncio への移行は不要 |
| 最大並列性の維持 | 独立タスクは `indegree=0` としてスタート時に全て submit される。依存があるタスクのみ待機する |
| 実装の明確さ | `FIRST_COMPLETED` ループは状態管理が明示的であり、デッドロックや競合の分析が容易 |
| テスト容易性 | `execute_fn` の DI により、本物の `subprocess` を使わずに単体テストで動作を検証できる |
| 標準ライブラリのみ | `concurrent.futures.wait` は Python 標準ライブラリ。追加依存ゼロ（ADR-001 方針と一貫） |

## Alternatives Considered

**トポロジカルソート直列化**: 実装が最もシンプルだが、独立タスクの並列性が失われる。ADR-001 が並列性を前提として採用した `ThreadPoolExecutor` の利点を損なうため採用しない。

**asyncio + create_subprocess_exec**: ADR-001 で「呼び出し側全体を async に変更する必要があり MVP の複雑度を不必要に高める」として不採用とした理由が引き続き適用される。既存の同期 API（`run_manifest()`）を破壊するコストも大きい。

**`concurrent.futures.as_completed` ループ**: `FIRST_COMPLETED` とほぼ等価だが、submit のバッチ制御がやや難しい。`wait` の方が「どの Future が終わったか」の粒度で扱えるため採用。

## Consequences

- `runner.py` に `_DependencyScheduler` クラスを追加する
- `run_manifest()` は `_DependencyScheduler.run()` を呼び出す形に刷新される
- `execute_fn` 引数で実行関数を DI できるため、ユニットテストで `subprocess` をモックせずにスケジューリングロジックを検証できる
- 依存元失敗時の skip 伝播は `_should_skip()` ヘルパが担い、推移的に伝播する（A 失敗 → B skipped → C skipped）
- `results` のタプル順はマニフェスト記述順に保つ（完了順ではない）
- `RunnerError` は最初の 1 件のみを送出する仕様（既存テスト互換）を維持する
