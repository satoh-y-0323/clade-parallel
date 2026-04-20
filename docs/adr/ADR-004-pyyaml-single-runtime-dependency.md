# ADR-004: Adopt PyYAML as the single third-party runtime dependency

- **Date:** 2026-04-20
- **Status:** Accepted

## Context

clade-parallel のマニフェストは YAML フロントマター付き Markdown 形式である。これは Clade の planner が生成するフォーマットと契約として合わせたものである。

YAML フロントマターをパースするために以下の選択肢を検討した:

- `PyYAML`（サードパーティライブラリ）
- 自作の最小 YAML パーサ
- フォーマットを TOML に切り替えて `tomllib`（Python 3.11+ 標準ライブラリ）を使う

要件として「サードパーティ依存を最小限にする」という方針が存在する。

## Decision

`PyYAML >= 6.0` を唯一のランタイム依存として採用する。

## Rationale

| 観点 | 理由 |
|---|---|
| YAML は契約フォーマット | マニフェストの YAML フロントマットは Clade planner との契約。フォーマットを変えることはできない |
| `yaml.safe_load` の安全性 | `PyYAML` の `safe_load` は任意コード実行の脆弱性を持たず、安全に使用できる |
| デファクトスタンダード | Python エコシステムにおいて YAML パースの事実上の標準ライブラリであり、ほぼすべての環境に間接依存として存在する |
| 自作パーサのリスク | YAML 仕様は複雑であり、自作パーサはバグ温床・保守負債になる。「依存ゼロ」の代償が大きすぎる |

## Alternatives Considered

**自作最小 YAML パーサ**: 追加依存ゼロだが、YAML の仕様（型推論・エスケープ・インデント等）を正確に実装するのは困難。メンテナンスコストと潜在的バグのリスクが高い。

**TOML + tomllib**: Python 3.11+ では標準ライブラリで利用可能だが、Clade planner が生成するのは YAML フロントマターであり、フォーマットを変更すると契約違反になる。また Python 3.10 との互換性も失われる。

## Consequences

- `pyproject.toml` に `install_requires = ["PyYAML>=6.0"]` を明記する
- `yaml.safe_load` のみを使用し、`yaml.load` は使用しない（任意コード実行の防止）
- 将来的により高速な YAML パーサ（`ruamel.yaml` 等）への切り替えを検討する場合も、`safe_load` インターフェースは互換を保てる
- dev 依存に `types-PyYAML` を追加して mypy での型チェックを有効にする
