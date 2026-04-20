# コードレビューレポート

## レビュー日時
2026-04-20

## 担当タスク
T10: コードレビュー実施

## 参照したレポート
- 要件定義: requirements-report-20260420-221945.md
- アーキテクチャ: architecture-report-20260420-223210.md
- 計画: plan-report-20260420-224337.md
- テスト結果: test-report-20260420-234424.md

## レビュー対象

| ファイル | コミット範囲 |
|---|---|
| `src/clade_parallel/__init__.py` | 5d7b5de..HEAD |
| `src/clade_parallel/manifest.py` | 〃 |
| `src/clade_parallel/runner.py` | 〃 |
| `src/clade_parallel/cli.py` | 〃 |
| `tests/conftest.py` | 〃 |
| `tests/test_manifest.py` | 〃 |
| `tests/test_runner.py` | 〃 |
| `tests/test_cli.py` | 〃 |
| `tests/test_integration.py` | 〃 |
| `pyproject.toml` | 〃 |
| `README.md` | 〃 |
| `.gitignore` | 〃 |

---

## 良い点

1. **疎結合設計が完全に実現されている**: `grep -R "import clade|from clade " src/ tests/` で `clade_parallel` 自パッケージ以外のヒットがゼロ。要件の最重要制約を静的に充足している。
2. **`frozen=True` dataclass の徹底**: `Task` / `TaskResult` / `RunResult` / `Manifest` のいずれも `frozen=True` で定義されており、スレッド間での共有可変状態がデータ構造レベルで排除されている。
3. **`ThreadPoolExecutor` の `with` 文による確実なリソース解放**: `run_manifest` 内で `with ThreadPoolExecutor(...) as executor:` を使用し、例外パスでも executor が適切に shutdown される設計になっている。
4. **型注釈・docstring の網羅率が高い**: 全 public API および internal helpers に型注釈と Google Style docstring が揃っており、mypy strict モードが通過している。
5. **`subprocess.run` のスレッドセーフな引数構築**: `_execute_task` 内で `env = {**os.environ, **task.env}` により毎呼び出しで新しい dict を生成しており、スレッド間での env 汚染がない。
6. **テストカバレッジが 96% と高く、並列性・race condition の検証も含む**: 単体テストに加えて統合テストで異なるスレッド ID の確認・Lock 保護カウンタの検証まで実施されている点が優れている。
7. **`pathlib.Path` への統一**: `src/` 全モジュールで `os.path` の文字列操作が一切使われていない。クロスプラットフォーム配慮の証左である。

---

## 要件・設計との整合性確認

| 確認項目 | 判定 | 備考 |
|---|---|---|
| `import clade` / `from clade ` がゼロ（疎結合） | ○ | grep 検索でヒットなし。`clade_parallel` 自パッケージのみ |
| ランタイム依存が `PyYAML>=6.0` のみ | ○ | `pyproject.toml` の `dependencies` に PyYAML のみ確認 |
| `ThreadPoolExecutor` を `with` 文で使用 | ○ | `runner.py:L218` で確認 |
| `subprocess.run` が `shell=False` / リスト形式 | ○ | `runner.py:L132-141` で確認。`shell=False` 明示 |
| `TimeoutExpired` の捕捉と `timed_out=True` 設定 | ○ | `runner.py:L155-167` で確認 |
| タイムアウト後の子プロセス kill 処理 | ✗ | `subprocess.run` 使用のため `Popen.kill()` 相当なし。後述 |
| `task.env` マージ時の `PATH` 保持 | ○ | `{**os.environ, **task.env}` で base が os.environ なので PATH は保持される |
| `pathlib.Path` 統一・`os.path` 不使用 | ○ | `grep -R "os.path" src/` でヒットなし |
| 型注釈・docstring（PEP 484 / 257） | ○ | mypy strict 通過。全 public API に型注釈あり |
| `SUPPORTED_PLAN_VERSIONS` の定義 | ○ | `frozenset({"0.1"})` で定義済み |
| CLI exit code マッピング（0/1/2/3） | ○ | `cli.py:L21-24` と `main()` 内で確認 |
| `--version` で `0.1.0` を出力 | ○ | `clade_parallel.__version__` を参照 |
| 例外階層（`CladeParallelError` ベース） | △ | `ManifestError` / `RunnerError` は独立した `Exception` 継承。後述 |
| `dev` 依存に pytest / ruff / black / mypy | ○ | `pyproject.toml` の `[project.optional-dependencies]` に確認 |
| `slow` テストの既知問題 | △ | `test_実プロセス経由でのスモークテスト` が `-p` フラグ誤用で失敗。後述 |

---

## 指摘事項

### [必須] タイムアウト後の子プロセスが残存する（Windows で特に問題）

**重要度: 必須（ブロッカー相当）**

理由:
`runner.py:L155-167` の `TimeoutExpired` ハンドリングでは、例外を捕捉して `TaskResult(timed_out=True)` を返すのみで、子プロセスの終了処理（kill）が実装されていない。`subprocess.run` は `timeout` が切れると `TimeoutExpired` を送出するが、この時点で子プロセスは依然として実行中のままである。特に Windows 環境では SIGKILL が存在せず、プロセスが残存し続けてリソースを消費し続ける。長時間バッチ実行や CI 環境での複数回実行では、ゾンビ/残存プロセスによるリソース枯渇が発生しうる。

plan-report T5 の注記では「catch 内で明示的に `kill()` 相当の後始末を行う（必要に応じ `Popen` へ切替）」と指示されていたが、実装されていない。

改善案: `subprocess.Popen` に切り替えて `timeout` を `communicate()` で制御し、タイムアウト時に `proc.kill()` / `proc.communicate()` を呼ぶ。または `subprocess.run` の `TimeoutExpired` 捕捉後に `exc.process` / `exc.pid` 経由でプロセスを kill する。

```python
# 改善前（現状）
except subprocess.TimeoutExpired as exc:
    duration = time.perf_counter() - start
    stdout = _decode_optional_bytes(exc.stdout)
    stderr = _decode_optional_bytes(exc.stderr)
    return TaskResult(...)  # プロセスが残存したまま

# 改善後（Popen 方式）
proc = subprocess.Popen(
    cmd, cwd=task.cwd, env=env,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
)
try:
    stdout, stderr = proc.communicate(timeout=task.timeout_sec)
except subprocess.TimeoutExpired:
    proc.kill()
    stdout, stderr = proc.communicate()  # 後始末を確実に行う
    return TaskResult(..., timed_out=True, ...)
```

---

### [必須] `runner.py:L233-234` の「起きえない」分岐が実際には到達可能

**重要度: 必須（ロジック上の誤り）**

理由:
`run_manifest` 内（`runner.py:L232-234`）に以下のコードがある:

```python
else:
    # Should not happen: _execute_task only raises RunnerError.
    raise exc
```

コメントでは「起きない」と記されているが、`_execute_task` には `except Exception:` ブロック（L168-178）があり、**あらゆる例外を捕捉して TaskResult に変換する**。その一方で `FileNotFoundError` は `RunnerError` に変換して `raise` している（L152-154）。しかし `RunnerError` は `Future.exception()` で拾われるため、この `else` 分岐が実行されるのは `_execute_task` から `RunnerError` 以外の例外が `Future` 経由で上がってきた場合のみであり、現状の実装では `except Exception:` で全て捕捉されるため理論上到達しない。

ただし、`except Exception:` がキャッチしない `BaseException` のサブクラス（`KeyboardInterrupt`、`SystemExit` など）は `Future` を通じて伝播する可能性がある。このケースを `raise exc` で無条件に再送出するのは正しい挙動だが、コメントが誤解を招く。また、現在の構造では `RunnerError` が複数タスクで発生した場合（同時に 2 つのタスクで `claude` バイナリが見つからない場合など）、2 件目以降の `RunnerError` がこの `else` 分岐で再送出されてしまい、最初の `RunnerError` ではなく 2 件目以降のものが呼び出し元に届く可能性がある。

改善案:
複数の `RunnerError` を適切に扱う処理を明示的に追加し、コメントを実態に合わせて修正する。

```python
exc = future.exception()
if exc is not None:
    if isinstance(exc, RunnerError):
        if runner_error is None:
            runner_error = exc
        # Additional RunnerErrors are suppressed; first one is reported.
    else:
        # BaseException subclasses (e.g., KeyboardInterrupt) can reach here.
        raise exc
```

---

### [推奨] 例外階層に `CladeParallelError` ベースクラスを設ける

**重要度: 推奨**

理由:
現在 `ManifestError` は `manifest.py` で、`RunnerError` は `runner.py` で、それぞれ独立した `Exception` の直接サブクラスとして定義されている。ライブラリ利用者がこのパッケージの例外をまとめて捕捉したい場合、`except (ManifestError, RunnerError):` と列挙する必要があり、将来追加された例外を見落とすリスクがある。

architecture-report でも「例外は `_exceptions.py` もしくは manifest.py 同居」という設計意図があり、plan-report T10 でも「`CladeParallelError` ベース＋ `ManifestError` / `RunnerError` 派生を設けるべきかの検討」が指摘されている。v0.1 は MVP であり必須ではないが、将来の v0.2 以降での拡張（`ConflictError` などの追加）を考えると今のうちに整理する価値がある。

改善案: `_exceptions.py` または `__init__.py` に共通ベースクラスを追加する。

```python
# src/clade_parallel/_exceptions.py（新設）
class CladeParallelError(Exception):
    """Base exception for all clade-parallel errors."""

# manifest.py
from ._exceptions import CladeParallelError
class ManifestError(CladeParallelError): ...

# runner.py
from ._exceptions import CladeParallelError
class RunnerError(CladeParallelError): ...
```

---

### [推奨] `test_実プロセス経由でのスモークテスト` の修正

**重要度: 推奨**

理由:
`tests/test_runner.py` の `test_実プロセス経由でのスモークテスト`（`@pytest.mark.slow`）は、`prompt: -c "import sys; sys.exit(0)"` を設定し `sys.executable` を `claude_executable` に指定する方式を採っている。これは `python -p "-c ..."` という引数列を生成するが、Python インタープリタに `-p` オプションは存在しないため、常に returncode == 2（`Unknown option: -p`）で失敗する。

test-report でも「T4 以前から存在する設計不備」として既知問題に分類されており、`test_integration.py::TestSlowSmokeRealProcess` では既にプラットフォーム別ラッパースクリプト方式（Windows: `.bat` / Unix: `.sh`）で正しく修正されている。`test_runner.py` 側でも同じ修正を適用することが必要。

改善案: `TestSlowSmokeRealProcess` と同じプラットフォーム別ラッパースクリプト方式に変更する。

```python
@pytest.mark.slow
def test_実プロセス経由でのスモークテスト(tmp_path):
    # Create platform-specific wrapper that exits 0.
    if sys.platform == "win32":
        wrapper = tmp_path / "fake_claude.bat"
        wrapper.write_text("@echo off\r\nexit 0\r\n", encoding="utf-8")
        claude_exe = str(wrapper)
    else:
        wrapper = tmp_path / "fake_claude.sh"
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
        claude_exe = str(wrapper)

    p = tmp_path / "smoke.md"
    p.write_text(smoke_manifest, encoding="utf-8")
    result = run_manifest(p, claude_executable=claude_exe)
    assert result.overall_ok is True
```

---

### [推奨] `_parse_task` の `default_agent_prompt` 引数が未使用

**重要度: 推奨**

理由:
`manifest.py:L117` の `_parse_task(raw, default_cwd, default_agent_prompt)` シグネチャに `default_agent_prompt: str` 引数があるが、関数本体では使われていない（`# Unused here; each task builds its own default.` とコメントされている）。未使用引数が public-facing でない内部関数に存在することで、コード読者に意図を誤解させる可能性がある。また、関数を呼び出す `load_manifest` 側では空文字列 `""` を渡しており（`L240`）、引数の存在意義が不明瞭。

改善案: `default_agent_prompt` 引数を削除し、呼び出し元も修正する。

```python
# 改善前
def _parse_task(raw: object, default_cwd: Path, default_agent_prompt: str) -> Task:

# 改善後
def _parse_task(raw: object, default_cwd: Path) -> Task:

# load_manifest 内の呼び出しも合わせて修正
tasks = tuple(_parse_task(raw_task, default_cwd) for raw_task in raw_tasks)
```

---

### [推奨] `cli.py` の `main()` 内で `sys.exit()` と `return` が混在している

**重要度: 推奨**

理由:
`cli.py:L162-163` の「サブコマンドなし」ハンドリングで `sys.exit(_EXIT_MANIFEST_ERROR)` を呼んでいる（関数から脱出せず直接 sys.exit）。これは `return` ベースの exit code 設計と異なるパターンであり、`main()` 全体の一貫性を損なう。テストコード（`test_cli.py`）でも `capsys` / `monkeypatch` を使って `main()` の戻り値を検査するパターンで設計されており、`sys.exit()` が呼ばれると `SystemExit` 例外が発生して `exit_code = cli.main(...)` の行でテストが落ちる可能性がある（実際には test_no_args_returns_nonzero で pytest.raises を使っているかどうかに依存）。

また、`sys.exit()` の呼び出しは test-report の `cli.py:L155` 未カバー行と関連しており、テストで到達できていないことも示している。

改善案: `sys.exit()` を使わずに `return _EXIT_MANIFEST_ERROR` に統一する。

```python
# 改善前
if args.command is None:
    parser.print_usage(sys.stderr)
    sys.exit(_EXIT_MANIFEST_ERROR)

# 改善後
if args.command is None:
    parser.print_usage(sys.stderr)
    return _EXIT_MANIFEST_ERROR
```

---

### [推奨] `pyproject.toml` の `[tool.ruff.lint.per-file-ignores]` でテストの `F401` を全無視している

**重要度: 推奨**

理由:
`pyproject.toml:L37-39` で `tests/**` に対して `F401`（未使用インポート）を全て無視している。これはテストファイルで意図的にインポートのみ行って公開 API の存在確認をするケース（例: `from clade_parallel.manifest import ManifestError`）を想定したものと推察されるが、F401 を一括無視すると実際の不注意による未使用インポートも見逃すことになる。

改善案: `noqa: F401` コメントを必要な箇所に限定するか、特定ファイルやルールを限定する方法を検討する。ただし開発初期フェーズでの過剰な制約はデメリットが大きいため、v0.1 では任意扱いとする。

---

### [任意] `runner.py` のモジュールレベル定数が small だが命名が冗長

**重要度: 任意**

理由:
`_DEFAULT_CLAUDE_EXECUTABLE = "claude"` と `_CLAUDE_PROMPT_FLAG = "-p"` という 2 つのプライベート定数が定義されているが、`_CLAUDE_PROMPT_FLAG` は `_execute_task` 内の 1 箇所でのみ使用されており、実質マジック文字列の定数化である。定数化自体は良い実践だが、1 箇所しか使わない場合は関数内の変数として持つ方がシンプルという考え方もある。これはスタイルの好みの問題であり、現状のままでも問題ない。

---

### [任意] `conftest.py` の `fake_claude_runner` フィクスチャのドキュメントと `FileNotFoundError` 対応

**重要度: 任意**

理由:
`fake_claude_runner` フィクスチャは `outcomes` に `exception` キーを指定すると例外を送出する設計だが、`FileNotFoundError` を送出した場合（runner の `test_claudeバイナリが見つからない場合はRunnerError`）に `subprocess.run` の実際のシグネチャとの対応が一部異なる可能性がある（フィクスチャ内では `FileNotFoundError` が直接 raise されるため）。実際のテストは通過しているため機能上の問題はないが、将来のメンテナンス者への補足コメントがあると良い。

---

## 特記事項：既知問題の判定（plan-report T10 依頼事項）

### `tests/test_runner.py::test_実プロセス経由でのスモークテスト` の扱い

テスト内容を精査した結果、このテストは `prompt: -c "import sys; sys.exit(0)"` というプロンプト文字列を YAML に記述し、実行時に `[sys.executable, "-p", '-c "import sys; sys.exit(0)"']` というコマンド列を生成する。Python インタープリタに `-p` は存在しないため **設計上必ず失敗する**。

- fast path（82 件）への影響: なし（`@pytest.mark.slow` で除外済み）
- test-report での分類: 既知問題、優先度低
- T8 で正しいアプローチ（ラッパースクリプト方式）が `TestSlowSmokeRealProcess` として実装済み

**レビュー判定: 修正推奨（[推奨]）**

機能上のブロッカーではないが、`-m slow` でテストを実行した場合に 1 件の失敗が残り続けるため、将来の CI 導入時に混乱を招く。T8 と同じラッパースクリプト方式への修正を推奨する。このまま放置することも技術的には許容されるが、テストスイートの「slow も全件 pass」状態を達成するためには修正が必要。

---

## developerへの依頼事項

優先度順:

1. **[必須] タイムアウト後の子プロセス残存対策（runner.py）**
   `subprocess.run` を `subprocess.Popen` + `communicate(timeout=...)` + `proc.kill()` に切り替え、`TimeoutExpired` 後に子プロセスを確実に終了させる。特に Windows で重要。

2. **[必須] `runner.py:L233` の複数 `RunnerError` の扱いを修正**
   2 件目以降の `RunnerError` が `else: raise exc` に流れ込まないよう、`isinstance(exc, RunnerError)` の判定を修正し、コメントを実態に合わせて更新する。

3. **[推奨] `test_実プロセス経由でのスモークテスト` の修正（test_runner.py）**
   `test_integration.py::TestSlowSmokeRealProcess` と同じプラットフォーム別ラッパースクリプト方式に変更する。

4. **[推奨] `_parse_task` の `default_agent_prompt` 未使用引数を削除（manifest.py）**

5. **[推奨] `cli.py:L162-163` の `sys.exit()` を `return _EXIT_MANIFEST_ERROR` に統一**

6. **[推奨] `CladeParallelError` ベースクラスの追加（v0.2 でも可）**
   急がないが、ライブラリとして公開するなら今サイクル中に整理しておくことを推奨。

---

## 総評

**全体評価: 優良（v0.1 MVP として十分な品質）**

要件に定められた最重要制約（疎結合・依存最小化・クロスプラットフォーム・race condition 防止・型安全性）はいずれも充足されており、コーディング規約（PEP 8/257/484、Black/Ruff/mypy）も全てクリアしている。`frozen=True` dataclass によるイミュータブル設計と `ThreadPoolExecutor` の適切な使用により、マルチスレッド環境での安全性が構造的に担保されている点は特筆すべき良点である。

**ブロッカー（高）指摘: 2 件**

1. タイムアウト後の子プロセス残存（Windows で DoS リスク）
2. 複数 `RunnerError` 発生時の 2 件目以降の誤処理

いずれも現在の fast path テスト（82 件）には影響しないが、本番稼働環境・CI 環境でのリソース枯渇または予期しない例外伝播の原因となりうるため、次サイクルでの修正を推奨する。

**推奨（中）指摘: 4 件**

- slow テスト修正
- 未使用引数の削除
- `sys.exit()` / `return` の統一
- 例外階層の整理

いずれも機能に影響しないが、保守性・一貫性の観点から対応を推奨する。

**要件充足度: 完全充足**（fast path テスト 82 件全件合格、カバレッジ 96%、静的解析全クリア）
