# セキュリティ診断レポート

## 診断日時
2026-04-20

## 担当タスク
T11: セキュリティ診断実施

## 参照したレポート
- 要件定義: requirements-report-20260420-221945.md
- アーキテクチャ: architecture-report-20260420-223210.md
- 計画: plan-report-20260420-224337.md
- テスト結果: test-report-20260420-234424.md
- コードレビュー: code-review-report-20260420-234958.md

## 診断対象

| ファイル | コミット範囲 |
|---|---|
| `src/clade_parallel/__init__.py` | 5d7b5de..HEAD |
| `src/clade_parallel/manifest.py` | 〃 |
| `src/clade_parallel/runner.py` | 〃 |
| `src/clade_parallel/cli.py` | 〃 |
| `pyproject.toml` | 〃 |

テストファイル群は OWASP 観点で関連する部分のみ参考として確認した。

---

## 診断結果サマリ

| 深刻度 | 件数 |
|---|---|
| Critical | 0 件 |
| High | 2 件 |
| Medium | 2 件 |
| Low | 2 件|
| Info | 1 件 |
| 問題なし | 5 項目 |

---

## 検出された脆弱性

### [High] タイムアウト後の子プロセス残存による DoS（CWE-400）

**種別:** 非制御リソース消費（DoS）
**該当箇所:** `runner.py:L155-167`
**OWASP 分類:** A05:2021 – Security Misconfiguration（リソース管理の不備）

**影響範囲:**
`subprocess.run()` はタイムアウト時に `TimeoutExpired` を発生させるが、Python の公式ドキュメントに明記されているとおり「タイムアウトが切れても子プロセスは kill されない」。現状の実装では `TimeoutExpired` を捕捉して `timed_out=True` の `TaskResult` を返すのみで、子プロセスの終了処理が実装されていない。

これにより以下の DoS シナリオが成立する:
1. **意図的な悪用**: 永久に応答しないプロセスを大量タスクとして記述したマニフェストを実行 → タイムアウト後もプロセスが残存し続け、次回実行でさらに蓄積 → OS のプロセステーブル・メモリ・CPU が枯渇
2. **CI/CD 環境での蓄積**: 繰り返し実行する環境では claude CLI がハングした場合にゾンビプロセスが積み重なる
3. **Windows 固有の問題**: SIGKILL が存在しないため、プロセスが残存した場合のクリーンアップが OS 側でも困難

**再現手順:**
```yaml
# 悪意あるマニフェスト（無限ループタスク）
---
clade_plan_version: "0.1"
name: dos-test
tasks:
  - id: hang-task
    agent: code-reviewer
    read_only: true
    timeout_sec: 1
    prompt: /agent-code-reviewer
---
```
`timeout_sec: 1` に設定し、实際の claude CLI は 1 秒以上かかるため即座にタイムアウト。しかしプロセスは `claude -p /agent-code-reviewer` として継続実行され続ける。複数回実行するとプロセスが蓄積する。

**修正方針:**
`subprocess.run()` を `subprocess.Popen` + `communicate(timeout=...)` + `proc.kill()` に切り替える。

```python
# 改善案（runner.py の _execute_task 内）
proc = subprocess.Popen(
    cmd,
    cwd=task.cwd,
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
try:
    stdout, stderr = proc.communicate(timeout=task.timeout_sec)
    duration = time.perf_counter() - start
    return TaskResult(
        task_id=task.id,
        agent=task.agent,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
        duration_sec=duration,
    )
except subprocess.TimeoutExpired:
    proc.kill()
    stdout, stderr = proc.communicate()  # バッファを確実にフラッシュ
    duration = time.perf_counter() - start
    return TaskResult(
        task_id=task.id,
        agent=task.agent,
        returncode=None,
        stdout=stdout,
        stderr=stderr,
        timed_out=True,
        duration_sec=duration,
    )
```

**備考:** code-review-report（T10）でもブロッカーとして指摘済み。セキュリティ観点からも High と評価し、次サイクルでの修正を必須とする。

---

### [High] `task.env` による危険な環境変数の無制限上書き（CWE-15）

**種別:** 環境変数インジェクション
**該当箇所:** `manifest.py:L158`, `runner.py:L128`
**OWASP 分類:** A03:2021 – Injection（環境変数経由の挙動変更）

**影響範囲:**
`manifest.py:L158` でタスクの `env` フィールドがバリデーションなしで受け入れられ、`runner.py:L128` で `env = {**os.environ, **task.env}` として子プロセスに渡される。この設計により、マニフェストを制御できる攻撃者は以下の危険なキーを上書きして claude プロセスの挙動を操作できる:

| 危険なキー | 悪用シナリオ |
|---|---|
| `LD_PRELOAD` | 任意の共有ライブラリを claude プロセスにインジェクト（Linux/macOS） |
| `LD_LIBRARY_PATH` | 正規ライブラリを攻撃者制御のものに差し替え |
| `PYTHONPATH` | Python の import パスを汚染し、モジュールハイジャック |
| `PATH` | `claude` バイナリ自体を別のプログラムに差し替え |
| `CLAUDE_API_KEY` 等 | 実行時に設定されている API キーを上書き・消去 |
| `HOME` / `XDG_CONFIG_HOME` | 設定ファイルの読み込み先を攻撃者制御のパスに変更 |

**再現手順:**
```yaml
# 悪意あるマニフェスト（ライブラリインジェクション）
---
clade_plan_version: "0.1"
name: env-injection
tasks:
  - id: inject-task
    agent: code-reviewer
    read_only: true
    prompt: /agent-code-reviewer
    env:
      LD_PRELOAD: /tmp/evil.so
      PATH: /tmp/attacker-bin:/usr/bin:/bin
---
```
マニフェストを受け取った claude プロセスは `/tmp/evil.so` をロードした状態で実行される。

**攻撃条件:**
- 攻撃者がマニフェストファイルを作成または改ざんできる立場（例: CI/CD パイプラインへの悪意あるプルリクエスト、マニフェストファイルが公開リポジトリに置かれている場合）

**修正方針:**
`manifest.py` の `_parse_task()` 内に env キーのブロックリストチェックを追加する。

```python
# manifest.py _parse_task() に追加
_BLOCKED_ENV_KEYS: frozenset[str] = frozenset({
    "LD_PRELOAD",
    "LD_LIBRARY_PATH", 
    "LD_AUDIT",
    "PYTHONPATH",
    "DYLD_INSERT_LIBRARIES",  # macOS 相当
    "DYLD_LIBRARY_PATH",
})

for key in env:
    if key in _BLOCKED_ENV_KEYS:
        raise ManifestError(
            f"Task '{task_id}': env key '{key}' is not allowed for security reasons."
        )
```

またはより防御的に、許可リスト（allowlist）方式で特定のプレフィックスのみを許可することも検討すべき。

---

### [Medium] マニフェストファイルサイズ・タスク数の上限なし（CWE-400）

**種別:** 非制御リソース消費（DoS）
**該当箇所:** `manifest.py:L200`, `runner.py:L213`
**OWASP 分類:** A05:2021 – Security Misconfiguration

**影響範囲:**

1. **無制限ファイル読み込み**: `manifest.py:L200` の `resolved.read_text(encoding="utf-8")` はファイルサイズの上限チェックなしで全内容をメモリに読み込む。数 GB のファイルを指定すれば OOM を引き起こせる。

2. **過大なタスク数によるスレッド枯渇**: `runner.py:L213` で `max_workers = max(1, len(tasks))` としており、デフォルトではタスク数と同数のスレッドを生成する。例えば 10,000 タスクのマニフェストは 10,000 スレッドを起動しようとし、OS のスレッドテーブルを枯渇させる可能性がある。

3. **YAML アリアス展開**: PyYAML の `safe_load` は `yaml.load` のような任意オブジェクト構築は禁止しているが、アリアス参照は共有オブジェクトとして展開する（コピーではなくリファレンス）。そのため「Billion Laughs」スタイルの真のメモリ爆発は発生しないが、アリアス数が非常に多い場合に YAML パーサーのスタックオーバーフローが発生する可能性は排除できない。

**再現手順（タスク数 DoS）:**
```python
# 10000 タスクのマニフェストを生成
tasks = "\n".join([
    f"  - id: task-{i}\n    agent: code-reviewer\n    read_only: true\n    prompt: /agent-code-reviewer"
    for i in range(10000)
])
```
`max_workers=None`（デフォルト）で実行すると 10,000 スレッドが生成される。

**修正方針:**

```python
# manifest.py load_manifest() に追加（ファイルサイズ上限）
MAX_MANIFEST_SIZE_BYTES = 1 * 1024 * 1024  # 1MB
if resolved.stat().st_size > MAX_MANIFEST_SIZE_BYTES:
    raise ManifestError(
        f"Manifest file is too large ({resolved.stat().st_size} bytes). "
        f"Maximum allowed: {MAX_MANIFEST_SIZE_BYTES} bytes."
    )

# manifest.py load_manifest() に追加（タスク数上限）
MAX_TASKS = 100
if len(raw_tasks) > MAX_TASKS:
    raise ManifestError(
        f"Too many tasks: {len(raw_tasks)}. Maximum allowed: {MAX_TASKS}."
    )
```

---

### [Medium] `cwd` のパス境界検証なし（CWE-22）

**種別:** パストラバーサル（ディレクトリトラバーサル）
**該当箇所:** `manifest.py:L160`
**OWASP 分類:** A01:2021 – Broken Access Control

**影響範囲:**
`manifest.py:L160` で `cwd: Path = Path(cwd_raw).resolve()` としている。`resolve()` はシンボリックリンクを追跡して最終的な絶対パスを返すが、マニフェストのあるディレクトリ外への脱出を検証していない。

攻撃者がマニフェストの `cwd` フィールドを制御できる場合:
- `cwd: /etc` → claude が `/etc` ディレクトリを作業ディレクトリとして実行される
- `cwd: /root` → スーパーユーザーホームディレクトリでの操作が可能
- シンボリックリンク経由でも同様の脱出が可能（`resolve()` がシンボリックリンクを解決してしまうため）
- read-only エージェントであっても、`cwd` を変えることで別ディレクトリのファイルにアクセスさせることができる

**攻撃の現実性:**
- v0.1 では read-only エージェントのみがスコープ内のため、直接的なファイル書き込みは制限される
- しかし read-only の claude エージェントが `/etc` や `/root` を `cwd` として実行される場合、その出力（stderr）に機密ファイルの内容が含まれる可能性がある

**修正方針:**

```python
# manifest.py _parse_task() 内で cwd を検証
cwd_raw = raw.get("cwd")
if cwd_raw is not None:
    resolved_cwd = Path(cwd_raw).resolve()
    # cwd は manifest のあるディレクトリ配下に限定する
    try:
        resolved_cwd.relative_to(default_cwd)
    except ValueError:
        raise ManifestError(
            f"Task '{task_id}': 'cwd' must be within the manifest directory. "
            f"Got: {resolved_cwd}, allowed root: {default_cwd}"
        )
    cwd = resolved_cwd
else:
    cwd = default_cwd
```

ただし、意図的にマニフェスト外ディレクトリを指定するユースケース（例: モノレポの別サブディレクトリを cwd にする）を許容したい場合は、警告ログの出力や設定フラグで許可制にすることも選択肢となる。

---

### [Low] `--claude-exe` による任意実行ファイル指定に関する警告なし

**種別:** 任意コード実行への経路（悪用要件あり）
**該当箇所:** `cli.py:L69-71`
**OWASP 分類:** A05:2021 – Security Misconfiguration

**影響範囲:**
CLI の `--claude-exe` オプションはファイルシステム上の任意の実行ファイルを指定できる。`runner.py:L127` で `cmd = [claude_exe, "-p", task.prompt]` として実行されるため、`--claude-exe /bin/bash` などを指定すれば bash が起動される。

ただし本オプションはコマンドライン引数として渡す必要があり、CLI へのアクセスが前提となる。攻撃者が CLI を直接操作できる環境では、そもそも任意コマンドを実行する手段は他にも多数存在するため、独立した脆弱性とは言いにくい。

一方で、以下の状況では問題になりうる:
- **スクリプト化された自動化環境**: 外部入力を `--claude-exe` に渡す自動化スクリプトが存在する場合
- **マニュアル読者への誤誘導**: ドキュメントで `--claude-exe` の危険性が説明されていない場合、ユーザーが意図せず危険な使い方をする可能性

**修正方針:**
ヘルプテキストにセキュリティ警告を追加する。

```python
# cli.py _build_parser() 内
run_parser.add_argument(
    "--claude-exe",
    default=_DEFAULT_CLAUDE_EXE,
    metavar="PATH",
    help=(
        "Name or path of the claude executable (default: claude). "
        "WARNING: Only use trusted executables. This option executes the "
        "specified binary with manifest-controlled arguments."
    ),
)
```

---

### [Low] `task.env` のバリデーションで値の型チェックが不十分

**種別:** 入力値バリデーション不備
**該当箇所:** `manifest.py:L158`
**OWASP 分類:** A03:2021 – Injection

**影響範囲:**
`env: dict[str, str] = dict(raw.get("env", {}))` は YAML で `env` フィールドが `dict` である場合はそのまま受け入れる。しかし値がすべて `str` であるかの検証がない。例えば以下の YAML:

```yaml
env:
  MY_VAR: 123  # YAML では整数として解釈される
```

`dict(raw.get("env", {}))` は `{"MY_VAR": 123}` の型のままとなり、`subprocess.run(env=...)` に `str` 以外の値が混入する。Python の `subprocess.run` は env の値が `str` でない場合に `TypeError` を発生させるが、この例外は `_execute_task` の `except Exception:` ブロックで捕捉されてエラーログに記録されるだけとなり、YAML の記述ミスを適切にフィードバックできない。

**修正方針:**

```python
# manifest.py _parse_task() 内
env: dict[str, str] = {}
raw_env = raw.get("env", {})
if not isinstance(raw_env, dict):
    raise ManifestError(
        f"Task '{task_id}': 'env' must be a YAML mapping, got {type(raw_env)!r}."
    )
for k, v in raw_env.items():
    if not isinstance(k, str):
        raise ManifestError(
            f"Task '{task_id}': env key must be a string, got {type(k)!r}."
        )
    if not isinstance(v, str):
        raise ManifestError(
            f"Task '{task_id}': env value for '{k}' must be a string, got {type(v)!r}."
        )
    env[k] = v
```

---

### [Info] `os.environ` 全体を子プロセスに引き渡す設計について

**種別:** 情報漏洩リスク（設計上の留意事項）
**該当箇所:** `runner.py:L128`
**OWASP 分類:** A02:2021 – Cryptographic Failures（機密情報の不適切な伝達）

**内容:**
`env = {**os.environ, **task.env}` により、実行時の全環境変数（`os.environ` 全体）が claude 子プロセスに渡される。これは `PATH` の保持という設計意図を達成するために必要であり、v0.1 の要件（plan-report 記載）として承認済みの設計である。

ただし、以下の情報が意図せず子プロセスに渡される可能性がある:
- `CLAUDE_API_KEY`（設定されていれば）
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` 等のクラウド認証情報
- `GITHUB_TOKEN` 等の CI/CD 認証情報
- その他、親プロセスの実行環境に設定された機密環境変数

v0.1 では「read-only な claude エージェントを実行する」用途であり、これらの認証情報が claude に渡ることは意図された動作とも言える（claude が API を呼び出すために必要）。一方で、**マニフェストの `task.env` でこれらの値を意図的に上書き・削除できる**点は前述の [High] 指摘と合わせて考慮が必要。

**対応:**
独立した修正を要する脆弱性ではないが、README またはドキュメントに「実行環境の環境変数が全て子プロセスに渡される」旨と、機密情報を環境変数で管理している場合の考慮事項を記載することを推奨する。

---

## 問題なしと判断した項目

| チェック項目 | 判断 | 根拠 |
|---|---|---|
| OSコマンドインジェクション耐性 | **問題なし** | `runner.py:L127-140` で `cmd` はリスト形式、`shell=False` 明示。`task.prompt` が任意文字列を含んでも、シェル展開は発生しない。コマンドインジェクション不可能。 |
| YAML 安全読み込み | **問題なし** | `manifest.py:L206` で `yaml.safe_load()` のみ使用。`yaml.load()` は使用されていない（`grep` で確認済み）。`safe_load` は任意 Python オブジェクト（`!!python/object` タグ）の構築を禁止している。 |
| PyYAML バージョンと既知脆弱性 | **問題なし** | `pyproject.toml` で `PyYAML>=6.0` を要求。CVE-2017-18342（`yaml.load()` の任意コード実行）は `yaml.safe_load()` 使用により影響なし。インストール済み 6.0.3 は現時点で既知の CVE なし（`pip-audit` による確認でも PyYAML に関するエントリは出力されなかった）。 |
| CLIサマリーへの stdout/stderr 混入 | **問題なし** | `cli.py` の `_format_summary_line()` および `_print_summary()` は `task_id`、`agent`、`duration_sec`、`returncode` のみを出力する。`TaskResult.stdout` / `TaskResult.stderr` の内容はサマリーに含まれないため、子プロセスの出力に機密情報が含まれていても CLI から漏洩しない。 |
| ハードコードされた秘密情報 | **問題なし** | `src/clade_parallel/` 配下の全ファイルを確認。API キー・パスワード・トークンのハードコードはない。定数として定義されているのは実行可能ファイル名 `"claude"` とフラグ文字列 `"-p"` のみ。 |

---

## developerへの依頼事項

深刻度順:

1. **[High 必須] `runner.py`: タイムアウト後の子プロセス kill 処理を実装する**
   `subprocess.run()` を `subprocess.Popen` + `communicate(timeout=...)` + `proc.kill()` + `proc.communicate()` に切り替え、TimeoutExpired 時に子プロセスを確実に終了させる。Windows/Linux 両環境で必要。詳細は code-review-report (T10) の改善案も参照のこと。

2. **[High 推奨] `manifest.py`: task.env の危険キーにブロックリストを適用する**
   `LD_PRELOAD` / `LD_LIBRARY_PATH` / `LD_AUDIT` / `DYLD_INSERT_LIBRARIES` / `DYLD_LIBRARY_PATH` / `PYTHONPATH` 等の環境変数をブロックリストで拒否する。許可リスト方式も検討。

3. **[Medium 推奨] `manifest.py`: ファイルサイズ上限とタスク数上限を追加する**
   `read_text()` 前にファイルサイズチェック（推奨: 1MB 以下）を実施する。タスク数の上限（推奨: 100 件以下）も検証する。

4. **[Medium 推奨] `manifest.py`: task.cwd のパス境界検証を追加する**
   `Path(cwd_raw).resolve()` 後にマニフェストディレクトリ外への脱出を検出する。`resolved_cwd.relative_to(default_cwd)` で `ValueError` が発生する場合は `ManifestError` を送出する。

5. **[Low 推奨] `cli.py`: `--claude-exe` のヘルプテキストにセキュリティ警告を追加する**
   ヘルプテキストに「WARNING: Only use trusted executables.」を追加して悪用リスクを明示する。

6. **[Low 推奨] `manifest.py`: task.env の値の型チェックを厳密化する**
   `env` の各キー・値が `str` であることを検証し、`int` などの非 str 値に対して `ManifestError` を送出する。

---

## 総評

**overall 評価: v0.1 MVP として概ね良好だが、2 件の High 指摘への対応が必要**

コマンドインジェクション耐性（`shell=False` + リスト形式）と YAML 安全読み込み（`safe_load` のみ）については正しく実装されており、最重要なセキュリティ要件は充足されている。依存 PyYAML のバージョン範囲（`>=6.0`）も適切で既知 CVE の影響はない。

**High 指摘（2 件）の概要:**
1. タイムアウト後の子プロセス残存 → DoS の直接的な経路。code-review-report (T10) でもブロッカーとして指摘済みであり、セキュリティ観点からも確認された。
2. `task.env` による `LD_PRELOAD` 等の危険な環境変数の無制限上書き → マニフェストを制御できる攻撃者による ライブラリインジェクションが成立する。

**Medium 指摘（2 件）の概要:**
- ファイルサイズ・タスク数無制限（DoS）
- `cwd` のパス境界検証なし（ディレクトリトラバーサル）

いずれも「攻撃者がマニフェストを制御できる」ことが前提となる。v0.1 のユースケース（信頼できるマニフェストの実行）では直接の悪用リスクは限定的だが、ライブラリとして配布・CI 自動化で利用される将来の用途を考慮すると修正が望ましい。
