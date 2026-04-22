# ADR-011: `Task.writes` フィールドの意味をユーザー宣言パスに変更する

- **Date:** 2026-04-22
- **Status:** Accepted
- **Supersedes (partially):** ADR-006 の「writes は Path.resolve() 済み絶対 POSIX パスで保持する」という記述

## Context

v0.2 で ADR-006 により導入された `writes:` フィールドの実装では、`_normalize_write_path` が `Path.resolve()` を用いてシンボリックリンクを展開したパスを `Task.writes` に保存していた。

この実装では、ユーザーが `writes: ["./output.txt"]` と宣言した場合でも、`./output.txt` が `/home/user/.private/data/output.txt` へのシンボリックリンクであれば、衝突エラーメッセージに `/home/user/.private/data/output.txt` が露出する問題があった。

この問題は v0.2 リリース時の計画レポートで「N6: security Medium として次サイクルへ繰り越し」とされており、本 ADR はその解決のための設計決定を記録する。

## Decision

`Task.writes` が保持するパスの意味を以下のように変更する:

- **変更前（v0.2〜v0.4）**: `Path.resolve()` によりシンボリックリンクを展開した絶対 POSIX パス文字列
- **変更後（v0.5〜）**: cwd を基準に絶対化し `os.path.normpath` で `..` セグメントを解決したが、**シンボリックリンクは展開しない**ユーザー宣言パスの POSIX 文字列

衝突検出（`_check_writes_conflicts`）では、`Path(declared).resolve(strict=False)` を関数内部のローカル変数としてのみ使用し、比較キーとして扱う。resolved path は一切 `Task` dataclass に保存せず、エラーメッセージにも出力しない。

エラーメッセージのフォーマットも変更し、衝突が検出された場合は各タスクの宣言パスを多行列挙する:

```
Write-path conflict(s) detected in manifest:
  - tasks declaring the same write target:
    * task-a: '/declared/path/from/manifest'
    * task-b: '/another/declared/path/same/target'
```

## Alternatives Considered

**(A) 2 フィールド並走 (`writes` = resolved、`writes_raw` = declared)**

`Task` dataclass に `writes`（resolved）と `writes_raw`（declared）の2フィールドを持たせる案。既存コードの多くを維持できるが、同一論理値を2フィールドで表現する SSOT 違反になる。また、`Task.writes` に resolved path が残るため、将来ログ出力等で同フィールドを参照した際に漏洩が再発するリスクが消えない。採用しない。

**(C) Task 不変、`_check_writes_conflicts` に resolved→raw マッピングを渡す**

`Task` dataclass を変更せず、呼び出し元が `resolved → declared` のマッピング辞書を別途 `_check_writes_conflicts` に渡す案。`Task` への変更ゼロという利点があるが、補助マッピングの渡し忘れが将来の保守者に問題を引き起こしうる。かつ `Task.writes` が依然 resolved を保持するため漏洩リスクが残る。採用しない。

## Consequences

- `Task.writes` の値の意味が破壊的に変更される（型・フィールド名は不変）。`Task.writes` を `import` して `resolve()` 済みと仮定しているコードは更新が必要。
- 本リポジトリ内では `runner.py` ほか `task.writes` を参照する箇所がないため、実質的な破壊的影響はリポジトリ内に存在しない。
- セキュリティ面: `Task` dataclass から resolved path が完全に排除されたため、エラーメッセージ・ログ・repr 経由での symlink target 漏洩が構造的に不可能になった。
- テスト: `task.writes[i]` の値に resolved path を期待していた既存テストを宣言パス前提に更新した（`tests/test_manifest.py`）。

## Relation to ADR-006

ADR-006 の「Decision 1: `Task.writes` は `tuple[str, ...]`（絶対 POSIX パス文字列）として保持する」は引き続き有効（型・構造は同じ）。ただし同セクションの「`Path.resolve().as_posix()` の結果」という記述は本 ADR により「`os.path.normpath` + `as_posix()` の結果（symlink 展開なし）」に supersede される。

ADR-006 の「Decision 2: 正規化は `Path.resolve().as_posix()` のみ行う」も本 ADR により変更される: 正規化は `os.path.normpath` + `as_posix()` で行い、`Path.resolve()` は衝突検出時の比較キー生成にのみ限定的に使用する。
