---
clade_plan_version: "0.1"
name: "Minimal Hello Demo"
tasks:
  - id: hello-math
    agent: general-purpose
    read_only: true
    prompt: "Reply with exactly the string: 'task1:2+2=4'. Do not add any commentary."
    timeout_sec: 120
  - id: hello-string
    agent: general-purpose
    read_only: true
    prompt: "Reply with exactly the string: 'task2:hello'. Do not add any commentary."
    timeout_sec: 120
---

# Minimal Hello Demo

短時間で並列動作を確認するための最小マニフェスト。各タスクは claude に対する短い質問を並列で送り、応答を受けて exit 0 で終了する。

## 実行方法

```bash
clade-parallel run examples/manifest-hello.md
```

## 期待される挙動

- `hello-math` と `hello-string` の 2 タスクが並列で起動される
- 各タスクが `claude -p` に短いプロンプトを送信し、指定された文字列で応答を受け取る
- 両タスクとも exit 0 で完了する
- 通常は数秒〜十数秒で完了する（timeout_sec: 120 は安全上の上限値）

## 注意点

- 実行には `claude` CLI が `PATH` に含まれている必要があります
- カスタムパスの場合は `--claude-exe` オプションで指定してください
