# ADR-001: Adopt ThreadPoolExecutor + subprocess for parallel execution

- **Date:** 2026-04-20
- **Status:** Accepted

## Context

clade-parallel は複数の Clade エージェント（Claude Code CLI）を並列実行するラッパーである。
並列化の方式として以下の候補を検討した:

- `concurrent.futures.ThreadPoolExecutor` + `subprocess.run`
- `concurrent.futures.ProcessPoolExecutor`
- `asyncio` + `asyncio.create_subprocess_exec`

各タスクは外部プロセス（`claude` CLI）の完了を待つ IO バウンド処理であり、CPU 演算はほぼ発生しない。

## Decision

`ThreadPoolExecutor` + `subprocess.run` を採用する。

## Rationale

| 観点 | 理由 |
|---|---|
| IO バウンドに最適 | 外部プロセス待ちは GIL の影響を受けない。スレッドで十分な並列性が得られる |
| 標準ライブラリのみ | `concurrent.futures` と `subprocess` はどちらも Python 標準ライブラリ。追加依存ゼロ |
| クロスプラットフォーム | Windows / macOS / Linux で同一の挙動を持つ |
| 実装のシンプルさ | `with ThreadPoolExecutor() as pool: futures = [pool.submit(...)]` という直感的な記述で完結する |
| スレッドセーフ | `subprocess.run` はスレッドセーフであり、複数スレッドから同時に呼び出しても問題ない |

## Alternatives Considered

**ProcessPoolExecutor**: GIL を完全に回避できるが、IO バウンドでは不要。Windows では `spawn` 方式となりオーバーヘッドが大きく、pickling 制約も生じる。

**asyncio + create_subprocess_exec**: 高い並列性を持つが、呼び出し側全体を `async` に変更する必要があり、MVP の複雑度を不必要に高める。

## Consequences

- CPU バウンド処理を並列化する将来要件が生じた場合、`ProcessPoolExecutor` への切り替えが必要になる（その場合でも `runner.py` の内部変更のみで対応可能な設計としている）
- タイムアウト発火時に子プロセスが残留する可能性が Windows にあるため、`TimeoutExpired` 後に明示的に `kill()` する後始末が必要
