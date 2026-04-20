# ADR-005: Version gate via `clade_plan_version` using `SUPPORTED_PLAN_VERSIONS` frozenset

- **Date:** 2026-04-20
- **Status:** Accepted

## Context

clade-parallel はマニフェストのスキーマバージョンを `clade_plan_version` フィールドで管理する。
将来的にスキーマが進化した際（v0.2, v0.3 等）に、古いバージョンのツールが新しいスキーマを誤って処理しないよう、バージョンゲートが必要である。

バージョン互換性の管理方法として以下を検討した:

1. `SUPPORTED_PLAN_VERSIONS` という `frozenset` で受理バージョンを管理する
2. バージョン番号を直接コード中にハードコードする（`if version == "0.1":`）
3. バージョンチェックを行わず、未知フィールドは無視するのみとする

## Decision

`SUPPORTED_PLAN_VERSIONS: frozenset[str] = frozenset({"0.1"})` として `manifest.py` に定義し、パース時にこのセットとの照合でゲートする。

未知のトップレベルキーや Task の未知キーは**無視する**（前方互換）。ただし `clade_plan_version` が `SUPPORTED_PLAN_VERSIONS` にない場合は `ManifestError` で拒否する。

## Rationale

| 観点 | 理由 |
|---|---|
| 拡張の容易さ | v0.2 対応時は `frozenset({"0.1", "0.2"})` に変更するだけでよい。差分が一目でわかる |
| 明示的な契約 | `SUPPORTED_PLAN_VERSIONS` を公開エクスポートに含めることで、ツールがどのバージョンに対応しているかをコード上で表明できる |
| 前方互換の分離 | バージョンゲートは「このスキーマバージョンを処理できるか」の判断に使う。「フィールドを知っているか」とは切り離す（未知フィールドは無視） |
| 誤処理の防止 | バージョンチェックなしに未知スキーマを処理すると、新機能フィールドが無視されて意図しない動作になりうる（例: `writes:` 宣言が無視されて衝突チェックをスキップ） |

## Alternatives Considered

**バージョンをハードコード（`if version == "0.1":`）**: 動作は同じだが、対応バージョンが増えた際に複数箇所を修正する必要がある。`SUPPORTED_PLAN_VERSIONS` 一箇所の変更で済む方が保守しやすい。

**バージョンチェックなし**: 未知フィールドを無視するだけでは、将来スキーマが非互換に変化した際にサイレントな誤動作を引き起こすリスクがある。

## Consequences

- `SUPPORTED_PLAN_VERSIONS` は `clade_parallel/__init__.py` からも再エクスポートし、外部ツールがバージョン互換性を確認できるようにする
- v0.2 でスキーマを拡張する際は、`SUPPORTED_PLAN_VERSIONS` への追加と同時に新フィールドのパース処理を実装する
- `clade_plan_version` フィールドが存在しないマニフェストは `ManifestError` で拒否する（省略不可の必須フィールド）
- 未知のキー（`writes`, `depends_on` 等）はパーサが静かに無視する。これにより v0.2 のマニフェストを v0.1 ツールで読み込んだ場合、バージョンゲートで弾かれる（未知キーが無視される前に `clade_plan_version: "0.2"` が弾かれるため、誤処理は発生しない）
