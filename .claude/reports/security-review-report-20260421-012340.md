# セキュリティ診断レポート

## 診断日時
2026-04-21

## 担当タスク
T20: セキュリティ診断再実施（修正サイクル後）

## 参照したレポート
- 要件定義: requirements-report-20260420-221945.md
- アーキテクチャ: architecture-report-20260420-223210.md
- 計画（更新版）: plan-report-20260421-003014.md
- 前回 security-review-report（T11）: security-review-report-20260421-002428.md
- 最新 test-report（T18）: test-report-20260421-011220.md
- 最新 code-review-report（T19）: code-review-report-20260421-011655.md

## 診断対象

| ファイル | 備考 |
|---|---|
| `src/clade_parallel/__init__.py` | CladeParallelError 公開追加 |
| `src/clade_parallel/_exceptions.py` | 新規作成（R4） |
| `src/clade_parallel/manifest.py` | F3（env ブロックリスト）・R2（未使用引数削除）適用 |
| `src/clade_parallel/runner.py` | F1（Popen+kill）・F2（多重 RunnerError 修正）適用 |
| `src/clade_parallel/cli.py` | R3（sys.exit → return 統一）適用 |
| `pyproject.toml` | 依存関係（変更なし） |

コミット範囲: `5d98f37..HEAD`（T12 承認後の修正サイクル）

---

## 診断結果サマリ

| 深刻度 | 件数 |
|---|---|
| Critical | 0 件 |
| High | 0 件 |
| Medium | 0 件（新規）※ v0.2 送り 2 件を維持記録 |
| Low | 1 件（新規） |
| Info | 1 件（維持） |
| 問題なし | 7 項目 |

---

## T11 High 指摘の解消状況（最重要確認）

| 指摘 ID | 内容 | 解消状況 | 検証詳細 |
|---|---|---|---|
| T11 High #A | タイムアウト後の子プロセス残存による DoS | **解消済み** | `subprocess.run` が `src/` から完全除去されていることを `grep -rn "subprocess.run" src/` で確認（出力なし）。`runner.py:L119-140` で `Popen` + `communicate(timeout=)` + `TimeoutExpired` 捕捉 + `proc.kill()` + `proc.communicate()` の構造に切り替え済み。kill 後の `communicate()` でパイプバッファのデッドロックを防ぐ設計も適切。 |
| T11 High #B | `task.env` による危険な環境変数の無制限上書き | **解消済み** | `manifest.py:L25-34` で `_BLOCKED_ENV_KEYS: frozenset[str]` を定義。対象キーは `LD_PRELOAD` / `LD_LIBRARY_PATH` / `LD_AUDIT` / `DYLD_INSERT_LIBRARIES` / `DYLD_LIBRARY_PATH` / `PYTHONPATH` の 6 件（T11 推奨セットに完全一致）。`_parse_task:L177-181` の 1 箇所に集約されており、バイパス経路なし。 |

---

## 検出された脆弱性

### 新規指摘

#### [Low] `except Exception:` パスで子プロセスが後始末されない可能性

**種別:** 非制御リソース消費（DoS）
**該当箇所:** `runner.py:L141-151`
**OWASP 分類:** A05:2021 – Security Misconfiguration（リソース管理の不備）

**影響範囲:**
`Popen.communicate(timeout=task.timeout_sec)` が `TimeoutExpired` 以外の例外（例: `OSError`、`BrokenPipeError`）を送出した場合、現在の `except Exception:` ブロックは `proc.kill()` および `proc.communicate()` を実行せずに `TaskResult` を返す。子プロセスは起動済みのまま残存する可能性がある。

F1 修正（T14）で `TimeoutExpired` パスの後始末は確実に実装されたが、その他の通信エラーパスは未対応のまま。`BrokenPipeError` や `MemoryError` などが発生した場合、子プロセスがゾンビとして蓄積しうる。

**攻撃の現実性:**
このパスに到達するには `Popen.communicate()` 自体が稀なシステムレベルエラーを起こす必要があり、通常のオペレーションでは発生しにくい。意図的な悪用経路（例えばマニフェスト操作で強制的にこのパスを通る）は現時点では確認できない。このため **High ではなく Low** と評価する。ただし長期的な CI/CD 実行環境では蓄積リスクがある。

**code-review-report（T19）との一致確認:**
T19 の「推奨」指摘（`except Exception:` での子プロセス後始末）と同一の観点。コードレビュー観点（品質・保守性）のみならず、セキュリティ観点（リソース枯渇）からも指摘する。

**修正方針（v0.2 推奨）:**
```python
except Exception:
    # Best-effort cleanup: ensure the child process is reaped.
    try:
        proc.kill()
        proc.communicate()
    except Exception:
        pass
    duration_sec = time.perf_counter() - start
    return TaskResult(
        task_id=task.id,
        agent=task.agent,
        returncode=None,
        stdout="",
        stderr=traceback.format_exc(),
        timed_out=False,
        duration_sec=duration_sec,
    )
```

---

## 詳細検証: T11 High #A（Popen 切替）の完全性確認

### (1) `subprocess.run` の完全除去
`grep -rn "subprocess.run" src/` の出力がゼロ行であることを実際に確認した。`subprocess` モジュール自体は `import subprocess` として残存しているが、これは `subprocess.Popen`・`subprocess.PIPE`・`subprocess.TimeoutExpired` の使用に必要なものであり問題なし。

### (2) `TimeoutExpired` 捕捉後の `proc.kill()` 呼び出し
`runner.py:L135-140` を確認した。

```python
except subprocess.TimeoutExpired:
    proc.kill()
    # Flush remaining buffers after kill to avoid pipe deadlock.
    stdout, stderr = proc.communicate()
    timed_out = True
    returncode = None
```

`proc.kill()` が必ず呼ばれること、kill 後に `proc.communicate()` でバッファをフラッシュすること、両方が実装されている。

### (3) kill 後の `communicate()` によるバッファ flush
パイプデッドロック回避のためのコメント（`# Flush remaining buffers after kill to avoid pipe deadlock.`）も記述されており、設計意図が明確である。`communicate()` には引数なし（タイムアウトなし）として呼ばれており、kill 後のプロセス終了を待機する正しい実装。

### (4) Windows / macOS / Linux での `proc.kill()` の挙動
- **Unix（Linux / macOS）**: `proc.kill()` は `SIGKILL` を送信する。プロセスは即時終了する。
- **Windows**: `proc.kill()` は `TerminateProcess()` を呼び出す。Unix の `SIGKILL` 相当であり、プロセスは強制終了する。
Python の公式ドキュメントでも `Popen.kill()` は Windows/Unix 両対応であることが明記されており、クロスプラットフォーム設計として適切。

### (5) リソースリーク設計の妥当性
`ThreadPoolExecutor` は `with` ブロック内で使用されており（`runner.py:L202`）、スレッドの後始末は `__exit__` で保証される。Popen オブジェクト自体は `_execute_task` の各パスで `communicate()` を経て終了待ちが行われるため、ファイルディスクリプタのリークもない（`text=True` かつ `stdout=PIPE`/`stderr=PIPE` で `communicate()` がパイプを閉じる）。

---

## 詳細検証: T11 High #B（env ブロックリスト）の完全性確認

### (1) ブロックリストのキー網羅確認
`manifest.py:L25-34` を確認した。

```python
_BLOCKED_ENV_KEYS: frozenset[str] = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PYTHONPATH",
    }
)
```

T11 推奨セット 6 件が完全に実装されている。`python -c "from clade_parallel.manifest import _BLOCKED_ENV_KEYS; print(sorted(_BLOCKED_ENV_KEYS))"` で実行時確認済み。

### (2) バイパス経路の有無
ブロックリスト判定は `_parse_task:L177-181` の 1 箇所に集約されており、`load_manifest` → `_parse_task` の呼び出しパスが唯一の env 処理経路である。`task.env` を直接構築するバイパス経路は存在しない。

### (3) case-sensitive 完全一致の妥当性
Linux の環境変数はケースセンシティブであり、`LD_PRELOAD` と `ld_preload` は別のキーとして扱われる。したがって `ld_preload` を使った迂回攻撃は Linux では成立しない。macOS でも環境変数は通常ケースセンシティブであり、`DYLD_INSERT_LIBRARIES` の小文字迂回は機能しない。

ただし理論的には、前例のない大文字小文字の変種（`Ld_Preload` など）が将来の OS/ランタイムで問題になる可能性はゼロではない。v0.1 としては case-sensitive 完全一致で十分であり、将来は `.upper()` 比較への強化を v0.2 ロードマップに追記しておく。

### (4) エラーメッセージの情報漏洩確認
```python
raise ManifestError(
    f"Task '{task_id}': env key '{key}' is not allowed"
    " for security reasons."
)
```

エラーメッセージには「どのキーがブロックされているか」は明示されているが（`{key}` を含む）、「どのキーが許可されているか」の情報（許可リストの内容）は含まれていない。これは適切な設計である。拒否されたキー名を攻撃者に伝えることは「何がブロック対象か」を教えることになるが、ブロックリストはセキュリティ上オープンな情報（公開リポジトリのソースコードにも記載）であるため、情報漏洩上の問題はない。

### (5) 通常キーの許可確認
`FOO`、`MY_VAR` 等のブロックリスト外のキーは引き続き `dict(raw_env)` に取り込まれ、`os.environ` にマージされる。テストレポート（T18）にて parametrize 6 ケース（ブロックキー全種）と正常キー許可ケースの合計が pass していることを code-review-report（T19）で確認済み。

---

## 詳細検証: 新規脆弱性の混入確認

### (1) Popen 切替による新しい攻撃面

#### コマンドインジェクション耐性（`shell=False` の維持）
`subprocess.Popen` の `shell` パラメータのデフォルト値は `False`（Python 公式ドキュメントおよび実行時検証で確認）。現在の実装では `shell=False` を明示的に指定していないが、デフォルト値が `False` であるため、`shell=False` を明示指定していた旧 `subprocess.run` と同等のコマンドインジェクション耐性が維持されている。`cmd` はリスト形式（`[claude_exe, _CLAUDE_PROMPT_FLAG, task.prompt]`）であり、`task.prompt` が任意文字列を含んでもシェル展開は発生しない。**コマンドインジェクション攻撃は不可能。**

#### バッファ枯渇（大量出力への耐性）
`stdout=subprocess.PIPE` + `stderr=subprocess.PIPE` で `communicate()` を使用する場合、Python の `communicate()` はプロセス終了まで全出力をメモリに読み込む。タスクが大量の出力（数 GB）を生成した場合は OOM になりうる。

ただし v0.1 のスコープは「claude エージェントの実行」であり、claude エージェントの出力は通常テキストベースで有限である。意図的に大量出力を生成する悪意あるプロセスが `claude_exe` として渡される場合は `--claude-exe` の悪用（T11 Low #1 と同じ前提）であり、攻撃条件がすでに高い。このため **新規 High/Critical として昇格しない**。v0.2 以降で `communicate()` の出力バイト上限（例: `limit` 付き asyncio subprocess）の検討を推奨する。

#### タイムアウト値の妥当性
`communicate(timeout=task.timeout_sec)` の `task.timeout_sec` は `_parse_task:L172` で `int(raw.get("timeout_sec", 900))` として取得される。既定値 900 秒（15 分）は T11 で確認済みの設計値であり変更なし。負の値やゼロが渡された場合の挙動（`communicate(timeout=0)` は即タイムアウト、`communicate(timeout=-1)` は Python 内部では扱いが不定）については `_parse_task` でのバリデーションがないが、これは T11 以前からの既存事項で v0.2 対応。

#### `text=True` によるエンコーディング問題
`text=True` を指定すると Python はデフォルトロケールのエンコーディングでデコードする。不正なバイト列が stdout/stderr に含まれる場合、`UnicodeDecodeError` が発生する可能性がある。この例外は `except Exception:` ブロックで捕捉されて `stderr=traceback.format_exc()` として記録されるため、クラッシュには至らない（ただし後始末の問題は上記 Low 指摘のとおり）。攻撃経路としては、`claude_exe` が不正なバイト列を出力する必要があり現実的でない。

旧実装では `_decode_optional_bytes` ヘルパーで `errors="replace"` を使用していたが、`text=True` に切り替えた後はエンコーディングエラー時の挙動が異なる。セキュリティ的な影響は限定的だが、エンコーディングエラー耐性の観点ではわずかに退行している。推奨: `subprocess.Popen(..., text=True, encoding="utf-8", errors="replace")` への変更を v0.2 で検討。これは新規脆弱性ではなく、品質上の改善事項として記録する。

### (2) ブロックリスト実装の網羅性

計画（plan-report）に「v0.1 では T11 推奨セットで OK、漏れがあれば v0.2 拡張推奨」と明記されている。以下は v0.2 への拡張候補として記録する：

| キー | リスク概要 | 追加優先度 |
|---|---|---|
| `LD_BIND_NOW` | 動的リンカの遅延バインディング無効化（単体では低リスク） | 低 |
| `LD_DYNAMIC_WEAK` | シンボル解決の弱参照動作変更（組み合わせ攻撃の要素） | 低 |
| `HOSTALIASES` | ホスト名解決のローカルオーバーライド（DNS 汚染の一形態） | 中 |
| `NODE_OPTIONS` | Node.js の実行オプション（claude が Node で動作する場合） | 中 |
| `PATH` | 実行バイナリの検索パス汚染 | 高（v0.2） |
| `HOME` / `XDG_CONFIG_HOME` | 設定ファイル読み込み先の変更 | 中（v0.2） |

`PATH` は T11 High #B の再現手順でも悪用例として挙げられており、v0.2 でのブロック追加を強く推奨する。ただし本サイクル（T20）の評価対象は plan-report 合意済みセットの実装品質であるため、v0.1 の High 指摘としては扱わない。

### (3) `CladeParallelError` ベースクラス導入の副作用

#### 既存の `except Exception:` パスへの影響
`ManifestError` と `RunnerError` が `Exception` のサブクラスである点は変わらない（継承チェーン: `ManifestError → CladeParallelError → Exception`）。したがって既存の `except Exception:` で捕捉できる範囲に変更はない。`_execute_task` の `except Exception:` ブロック（L141）は `TimeoutExpired` と `FileNotFoundError` を除く全例外を対象とするが、`RunnerError` は `FileNotFoundError` 捕捉後に変換されるため、このブロックに `RunnerError` が入ることはない。副作用なし。

#### 例外クラスの情報漏洩確認
`CladeParallelError`、`ManifestError`、`RunnerError` はいずれも `Exception` を継承したシンプルなクラスであり、`__str__` は親クラスの実装（引数のみ）を継承する。`__repr__` も標準実装。スタックトレースや内部構造を自動的に公開するカスタム実装はないため、情報漏洩の問題はない。

`cli.py` ではエラーメッセージを `print(f"ManifestError: {exc}", file=sys.stderr)` / `print(f"RunnerError: {exc}", file=sys.stderr)` として stderr に出力しているが、これは CLI の正常な動作であり、クラス名と設定エラーメッセージが出力されることはユーザーへの適切なフィードバックである。

### (4) `cli.py` の `sys.exit()` → `return` 統一の副作用

#### `main()` の呼び出し側の確認
`pyproject.toml:L13` で `clade-parallel = "clade_parallel.cli:main"` として setuptools の entry_points に登録されている。setuptools が生成するエントリポイントスクリプトは以下の形式になる：

```python
import sys
from clade_parallel.cli import main

sys.exit(main())
```

これにより `main()` の戻り値（整数）が `sys.exit()` に渡され、適切な終了コードが設定される。`main()` が `return _EXIT_MANIFEST_ERROR` で `int` を返す設計は、setuptools エントリポイントの標準的なパターンと完全に一致している。**副作用なし。**

Python スクリプトとして直接呼び出す場合（`python -m clade_parallel.cli` や `python -c "from clade_parallel.cli import main; main()"`）でも、戻り値を無視して終了することになるが、これは仕様上の問題でなく使い方の問題であり、セキュリティ上の懸念はない。

---

## 問題なしと判断した項目

| チェック項目 | 判断 | 根拠 |
|---|---|---|
| OSコマンドインジェクション耐性 | **問題なし** | `Popen` は `shell=False`（デフォルト）でリスト形式の `cmd` を実行。`task.prompt` が任意文字列でもシェル展開は発生しない。旧 `subprocess.run` から後退なし。 |
| `subprocess.run` の完全除去 | **問題なし** | `grep -rn "subprocess.run" src/` で出力ゼロ。完全移行を実機確認済み。 |
| YAML 安全読み込み | **問題なし** | `yaml.safe_load()` のみ使用。`yaml.load()` は不使用（前サイクル T11 から変更なし）。 |
| PyYAML 既知脆弱性 | **問題なし** | インストール済み 6.0.3。`pip-audit` 出力に PyYAML のエントリなし。依存 `pip`/`setuptools`/`pytest` に CVE 有りだが、これらは開発依存のみでランタイム依存には含まれない（`pyproject.toml:L10` の `dependencies` は `PyYAML>=6.0` のみ）。 |
| ハードコードされた秘密情報 | **問題なし** | `src/clade_parallel/` 配下の全ファイルを確認。API キー・パスワード・トークンのハードコードなし。 |
| CLI サマリーへの stdout/stderr 混入 | **問題なし** | `_format_summary_line()` は `task_id`・`agent`・`duration_sec`・`returncode` のみ出力。`TaskResult.stdout` / `.stderr` の内容は含まれない（前サイクルから変更なし）。 |
| 例外クラス情報漏洩 | **問題なし** | `CladeParallelError` / `ManifestError` / `RunnerError` は標準 `Exception` 継承のシンプルな実装。`__str__`/`__repr__` はデフォルト実装。スタックトレース等の自動公開なし。 |

---

## v0.2 ロードマップ（T11 Medium/Low/Info の継続記録）

plan-report（T12 更新版）に記載の合意事項に従い、以下は v0.2 以降で対応する。本サイクル（T20）での未修正は許容。

| 深刻度 | 項目 | 一次ソース | 推奨バージョン |
|---|---|---|---|
| Medium | マニフェストファイルサイズ上限（推奨 1 MB） | T11 Medium #1 | v0.2 |
| Medium | タスク数上限（推奨 100 件） | T11 Medium #1 | v0.2 |
| Medium | `cwd` のパス境界検証（`resolved_cwd.relative_to(default_cwd)`）| T11 Medium #2 | v0.2 |
| Low | `--claude-exe` ヘルプにセキュリティ警告追加 | T11 Low #1 | v0.2 |
| Low | `task.env` 値の型チェック厳密化（int/None 等を ManifestError に）| T11 Low #2 | v0.2 |
| Low | `except Exception:` パスでの子プロセス後始末 | T20 新規（Low） | v0.2 |
| Low | `PATH` / `HOME` / `XDG_CONFIG_HOME` / `NODE_OPTIONS` / `HOSTALIASES` のブロックリスト追加 | T20 新規（観察） | v0.2 |
| Info | README に `os.environ` 引き渡しの明記と秘匿情報取扱い注意 | T11 Info #1 | v0.2 |
| Info | `text=True` から `text=True, encoding="utf-8", errors="replace"` への変更 | T20 新規（観察） | v0.2 |

---

## developerへの依頼事項

深刻度順:

1. **[Low 推奨・v0.2 対応] `runner.py`: `except Exception:` パスでの子プロセス後始末追加**
   `Popen.communicate()` が `TimeoutExpired` 以外の例外を送出した場合、子プロセスが残存する可能性がある。`except Exception:` ブロック内で `proc.kill()` + `proc.communicate()` を best-effort（try/except pass）で実行することを推奨する。

2. **[Info 推奨・v0.2 対応] `runner.py`: `text=True` にエンコーディング指定を追加**
   `subprocess.Popen(..., text=True, encoding="utf-8", errors="replace")` とすることで、子プロセスの不正なバイト列出力による `UnicodeDecodeError` を回避できる。

3. **[Low 推奨・v0.2 対応] `manifest.py`: `PATH` / `HOME` 等のキーをブロックリストに追加**
   T11 High #B の再現手順で言及された `PATH` をはじめ、`HOME`・`XDG_CONFIG_HOME`・`NODE_OPTIONS`・`HOSTALIASES` のブロックへの追加を v0.2 で検討すること。

4. **v0.2 以降: T11 Medium/Low/Info 項目（plan-report で合意済み）**
   ファイルサイズ・タスク数上限、`cwd` パス境界検証、`--claude-exe` ヘルプ警告、env 値型チェック、README 情報漏洩注記。詳細は plan-report の v0.2+ ロードマップを参照。

---

## 総評

**overall 評価: T11 High 2 件が完全解消され、v0.1 MVP のセキュリティ要件を充足**

### T11 High 指摘の解消確認
- **High #A（タイムアウト後の子プロセス残存）**: `subprocess.run` から `subprocess.Popen` + `communicate(timeout=)` + `proc.kill()` + `proc.communicate()` への切替が完全に実施されており、`subprocess.run` は `src/` から完全除去されている。Windows/macOS/Linux いずれでも `proc.kill()` が適切に動作する設計である。
- **High #B（`task.env` による危険な環境変数の無制限上書き）**: `_BLOCKED_ENV_KEYS: frozenset[str]` が T11 推奨の 6 キーを完全に網羅し、`_parse_task` の唯一の env 処理箇所に集約されている。バイパス経路なし、情報漏洩なし、通常キーの通過も維持されている。

### 新規 High/Critical 混入なし
F1/F2/F3/R1〜R4 の修正で新たな Critical/High 脆弱性は混入していない。OSコマンドインジェクション耐性（`shell=False`）、YAML 安全読み込み（`safe_load`）、例外階層の後方互換性、CLI エントリポイントの正常動作（`sys.exit(main())`）がすべて確認された。

### 新規 Low 指摘（1 件）
`except Exception:` パスで子プロセスが後始末されない可能性。稀なシステムエラー発生時にのみ顕在化するため、v0.1 DoD の妨げにはならない。v0.2 での修正を推奨。

**v0.1 のセキュリティ評価: DoD 達成を妨げる High/Critical 指摘ゼロ。**
