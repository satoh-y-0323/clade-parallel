# コードレビューレポート

## レビュー日時
2026-04-21

## 担当タスク
T19: コードレビュー再実施（修正サイクル後）

## 参照したレポート
- 要件定義: requirements-report-20260420-221945.md
- アーキテクチャ: architecture-report-20260420-223210.md
- 計画（更新版）: plan-report-20260421-003014.md
- 前回 code-review-report（T10）: code-review-report-20260420-234958.md
- 最新 test-report（T18）: test-report-20260421-011220.md
- security-review-report（T11）: security-review-report-20260421-002428.md

## レビュー対象

| ファイル | 備考 |
|---|---|
| `src/clade_parallel/__init__.py` | 公開 API に `CladeParallelError` 追加 |
| `src/clade_parallel/_exceptions.py` | 新規作成（R4） |
| `src/clade_parallel/manifest.py` | F3（env ブロックリスト）・R2（未使用引数削除）適用 |
| `src/clade_parallel/runner.py` | F1（Popen+kill）・F2（多重 RunnerError 修正）適用 |
| `src/clade_parallel/cli.py` | R3（sys.exit → return 統一）適用 |
| `tests/conftest.py` | FakePopen 対応に更新 |
| `tests/test_manifest.py` | F3 対応テスト追加 |
| `tests/test_runner.py` | F1/F2 対応テスト追加・R1（slow テスト修正） |
| `tests/test_cli.py` | 既存テスト維持 |
| `tests/test_integration.py` | FakePopenCounter 対応 |

コミット範囲: `5d98f37..HEAD`（T12 承認後の修正サイクル）

---

## 良い点

1. **F1 の Popen 移行が模範的**: `_execute_task` は `Popen` 生成フェーズと `communicate` フェーズを明確に分離し、`FileNotFoundError` を Popen 生成時のみ捕捉、`TimeoutExpired` を communicate 時のみ捕捉している。各 except ブロックに正確な後始末が実装されており、正常・タイムアウト・異常の全パスで `duration_sec` が記録される点も優れている。
2. **F2 の RunnerError 抑制ロジックが明快**: `runner_error is None` のガードで「最初の 1 件のみ保持、以降は抑制」というルールがコード読者にすぐ伝わる。インラインコメントも実装と整合しており、以前の誤解を招くコメントが完全に除去されている。
3. **F3 の `_BLOCKED_ENV_KEYS` 設計が適切**: `frozenset` としてモジュールレベルに定義し、`_parse_task` の唯一の呼び出し箇所に集約している。変更コストが低く、将来キーを追加する際も定数定義のみ変更すれば済む。
4. **`_exceptions.py` の導入によりライブラリ利用者の使い勝手が向上**: `from clade_parallel import CladeParallelError` で全例外をまとめて捕捉できる設計になり、`ManifestError` / `RunnerError` の公開 API 化も `__init__.py` の `__all__` で明示されている。
5. **`conftest.py` の `fake_claude_runner` が Popen 対応に適切に刷新された**: `FakePopenInstance.communicate` が `_communicate_call_count` でタイムアウト→kill→再 communicate の 2 フェーズを正しく模倣しており、F1 修正の検証に十分な精度を持つ。
6. **slow テスト（R1）が全件 pass**: `test_実プロセス経由でのスモークテスト` がプラットフォーム別ラッパースクリプト方式（`.bat` / `.sh`）に修正され、前回レポートで懸念していた「`-p` オプション誤用による恒久的な失敗」が解消されている。
7. **前サイクルで良点として挙げた品質特性がすべて維持されている**: `frozen=True` dataclass・`ThreadPoolExecutor` の `with` 文・`pathlib.Path` 統一・全 public API の型注釈と Google Style docstring・疎結合（`import clade` ゼロ）。

---

## 前回 T10 指摘の解消状況

| 指摘 ID | 内容 | 解消状況 | 根拠 |
|---|---|---|---|
| T10 ブロッカー #1 | タイムアウト後の子プロセス残存 | **解消** | `runner.py:L119-140` で Popen+kill+communicate の後始末が全パスで実装。`test_タイムアウト時に子プロセスがkillされる` が pass |
| T10 ブロッカー #2 | 多重 RunnerError の誤処理（else: raise exc） | **解消** | `runner.py:L218-224` で `isinstance(exc, RunnerError)` + `runner_error is None` ガードに変更。コメントも実態と整合。`test_複数タスクがRunnerErrorを起こしても最初の1件のみ送出される` が pass |
| T10 推奨 R1 | slow テスト（test_スモークテスト）のラッパースクリプト化 | **解消** | `test_runner.py:L524-562` がプラットフォーム別 `.bat`/`.sh` 方式に変更。slow 2 件全件 pass |
| T10 推奨 R2 | `_parse_task` の `default_agent_prompt` 未使用引数削除 | **解消** | `manifest.py:L133` のシグネチャから削除済み。mypy/ruff クリア |
| T10 推奨 R3 | `cli.py` の `sys.exit()` → `return` 統一 | **解消** | `cli.py:L161-163` が `return _EXIT_MANIFEST_ERROR` に変更。`test_no_args_returns_nonzero` が SystemExit なしで pass |
| T10 推奨 R4 | `CladeParallelError` ベースクラスの追加 | **解消** | `_exceptions.py` 新設。`ManifestError` / `RunnerError` が `CladeParallelError` を継承。`__init__.py` の `__all__` に公開 |
| T11 High #A（= T10 #1） | Popen+kill で子プロセス残存解消 | **解消** | T10 ブロッカー #1 と同一。上記参照 |
| T11 High #B | `task.env` による危険キー上書き | **解消** | `manifest.py:L25-34` で `_BLOCKED_ENV_KEYS` を定義、`_parse_task:L177-181` で ManifestError を送出。parametrize 6 ケース全 pass |

**T10 ブロッカー 2 件・推奨 4 件・T11 High 2 件: 全件解消確認**

---

## 要件・設計との整合性確認

| 確認項目 | 判定 | 備考 |
|---|---|---|
| `import clade` / `from clade ` がゼロ（疎結合） | ○ | test-report T18 の grep 検索で 0 件確認 |
| ランタイム依存が `PyYAML>=6.0` のみ | ○ | 変更なし |
| `ThreadPoolExecutor` を `with` 文で使用 | ○ | `runner.py:L202` で確認 |
| `subprocess.Popen` が使用され kill 後始末あり | ○ | `runner.py:L119-162` で全パス確認 |
| `TimeoutExpired` 捕捉と `timed_out=True` 設定 | ○ | `runner.py:L135-140` で確認 |
| タイムアウト後の子プロセス kill 処理 | ○ | `proc.kill()` + `proc.communicate()` 実装済み（F1） |
| `task.env` の危険キー拒否 | ○ | `_BLOCKED_ENV_KEYS` + `_parse_task` 内バリデーション（F3） |
| `CladeParallelError` 例外階層 | ○ | `_exceptions.py` 新設・継承階層確立（R4） |
| 多重 RunnerError の最初の 1 件のみ送出 | ○ | `runner.py:L218-224` の `isinstance` ガード（F2） |
| `pathlib.Path` 統一・`os.path` 不使用 | ○ | 変更なし |
| 型注釈・docstring（PEP 484 / 257） | ○ | mypy strict 通過 |
| `SUPPORTED_PLAN_VERSIONS` の定義 | ○ | `frozenset({"0.1"})` で定義済み |
| CLI exit code マッピング（0/1/2/3） | ○ | `cli.py:L21-24` と `main()` 内で確認 |
| `sys.exit()` の排除・`return` 統一 | ○ | `cli.py:L163` が `return _EXIT_MANIFEST_ERROR` に変更済み（R3） |

---

## 指摘事項

### 新規指摘

#### [任意] `conftest.py:FakePopenInstance` の `returncode` 初期化が仕様上あいまい

`FakePopenInstance.__init__` で `self.returncode: int | None = spec.get("returncode", 0)` と設定されている（conftest.py:L82）。しかし `communicate` 時に `TimeoutExpired` が発生するケースでは `_execute_task` が `proc.returncode` を参照しないまま `timed_out=True` / `returncode=None` で返すため、`FakePopenInstance.returncode` はタイムアウトケースで参照されない。一方、正常終了ケースでは `proc.returncode` が `_execute_task:L134` で使われるため、`FakePopenInstance.returncode` の値が実際に反映される。

この挙動は現時点では正しく動作しているが、ドキュメントコメントに「タイムアウトケースでは returncode は参照されない」という補足があると将来のメンテナンス者の混乱を防げる。

理由: `proc.returncode` は `communicate()` が正常終了した後にのみ意味を持つが、タイムアウト時には `_execute_task` が `returncode = None` をハードコードしており、`FakePopenInstance.returncode` は参照されない。この非対称性がコードを読んだだけでは分かりにくい。

改善案: `FakePopenInstance` のクラスドキュメントコメントに以下を追記する。

```python
class FakePopenInstance:
    """Fake Popen instance returned by the patched Popen constructor.

    Note: ``returncode`` is consumed by _execute_task only in the normal
    (non-timeout) path.  In the timeout path _execute_task hard-codes
    returncode=None and does not access proc.returncode.
    """
```

---

#### [任意] `test_runner.py` のモジュールレベルリスト `_fake_popen_instances` がテスト間で共有される

`_fake_popen_instances: list[FakePopen] = []`（test_runner.py:L600）はモジュールレベルで定義されており、`test_タイムアウト時に子プロセスがkillされる` の内部で `_fake_popen_instances.clear()` を呼んでいる（L619）。

現時点ではこのテストが単独で実行されるため問題は発生していないが、pytest が並列実行モード（`pytest-xdist`）などで複数テストプロセスを使う場合、あるいはテスト実行順序が変わった際に `clear()` のタイミング次第で他テストに影響が出る可能性がある。

理由: モジュールレベルのミュータブルな状態をテスト内で管理するパターンはテスト分離の観点で脆い。

改善案: `_fake_popen_instances` のクリア・登録・検査をすべてテスト関数内のローカルスコープに閉じ込めるか、`autouse=False` の fixture として切り出す方がテスト間の独立性が高い。ただし、現状のテスト構成（逐次実行・`clear()` が先頭にある）では実害はなく、v0.2 での改善で十分。

---

#### [推奨] `_execute_task` の `except Exception:` ブロックで `duration_sec` が `communicate` 失敗時点で計測される

`runner.py:L141-151` の `except Exception:` は `Popen.communicate()` が `TimeoutExpired` および `FileNotFoundError` 以外の例外を送出した場合に捕捉される。このパスでは `duration_sec = time.perf_counter() - start`（L142）が計測されるが、この時点では `proc.kill()` や `proc.communicate()` は呼ばれていない。

つまり、`communicate()` が予期しない例外（`OSError` や `BrokenPipeError` など）を送出した場合、子プロセスが残存したまま `TaskResult` が返される可能性がある。これは F1 で解消した「タイムアウト後の残存」とは異なるパスだが、同様の問題になりうる。

理由: `TimeoutExpired` 以外の通信エラーでも子プロセスは起動済みのままになっているため、後始末が必要。

改善案: `except Exception:` ブロック内でも `proc.kill()` + `proc.communicate()` を呼ぶ後始末を追加する。

```python
# 改善案
except Exception:
    # Ensure the child process is reaped even on unexpected errors.
    try:
        proc.kill()
        proc.communicate()
    except Exception:
        pass  # Best-effort cleanup; ignore secondary errors.
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

### 既存良点の維持確認（T19 特記事項）

| 確認観点 | 状況 |
|---|---|
| 疎結合設計（`import clade` ゼロ） | 維持 |
| `frozen=True` dataclass（Task/TaskResult/RunResult/Manifest） | 維持 |
| `ThreadPoolExecutor` の `with` 文 | 維持 |
| 型注釈・docstring の網羅率 | 維持（mypy strict 全クリア） |
| `pathlib.Path` 統一 | 維持 |
| テスト件数（82 件→91 件 fast / slow 1 件→2 件） | 改善 |
| カバレッジ（96%→97%） | 改善 |
| 静的解析（ruff/black/mypy）全クリア | 維持 |

---

## developerへの依頼事項

優先度順:

1. **[推奨] `except Exception:` ブロックでの子プロセス後始末追加（runner.py）**
   `Popen.communicate()` が `TimeoutExpired` 以外の例外を送出した場合、子プロセスが残存する可能性がある。`except Exception:` ブロック内で `proc.kill()` + `proc.communicate()` を best-effort で実行することを推奨する。現状のコードではこのパスに到達するのは `OSError`/`BrokenPipeError` 等の稀なケースに限られるため、v0.1 での対応は任意扱いだが v0.2 での修正を推奨する。

2. **[任意] `conftest.py:FakePopenInstance` のコメント補足**
   タイムアウトケースで `returncode` が参照されない旨をクラス docstring に追記する。

3. **[任意] `test_runner.py` の `_fake_popen_instances` をローカルスコープ化**
   モジュールレベルのミュータブルなリストをテスト内ローカルに移動するか fixture 化する。v0.2 で検討推奨。

---

## 総評

**全体評価: 優良（T10/T11 指摘全件解消・品質退行なし）**

T10 ブロッカー 2 件（子プロセス残存・多重 RunnerError 誤処理）および T11 High 2 件（同一・env injection）はいずれも適切に修正されており、修正の完全性をテストが担保している。T10 推奨 R1〜R4 の 4 件も全て完了しており、前回指摘した問題点は残件ゼロである。

**新規ブロッカー（高）指摘: 0 件**

今サイクルで導入した修正（F1/F2/F3/R1〜R4）に起因する品質退行は確認されなかった。コーディング規約（PEP 8/257/484、Black/Ruff/mypy）の遵守も継続している。

**新規推奨（中）指摘: 1 件**

- `except Exception:` パスでの子プロセス後始末（v0.1 では任意扱い・v0.2 推奨）

**新規提案（低）指摘: 2 件**

- `FakePopenInstance` のドキュメント補足
- `_fake_popen_instances` のスコープ整理

いずれも機能・安全性に直接影響しないため、現 v0.1 MVP としての DoD 達成を阻害しない。T20（security-reviewer）での最終確認を経てリリース判定が行えると評価する。

**要件充足度: 完全充足**（fast 91 件 + slow 2 件 全件合格・カバレッジ 97%・静的解析全クリア・T10/T11 指摘全解消）
