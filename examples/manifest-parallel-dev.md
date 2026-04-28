---
clade_plan_version: "0.1"
name: "Parallel Dev Demo"
tasks:
  - id: addition
    agent: worktree-developer
    read_only: false
    cwd: ..
    writes:
      - examples/scripts/addition.js
    prompt: |
      タスクID: addition

      examples/scripts/addition.js を実装してください。

      ## 仕様
      - Node.js（use strict）
      - コマンドライン引数 a, b を2つ受け取る
      - どちらかが数値でない場合は stderr にエラーを出力して exit 1
      - 正常時は `a + b = <result>` を標準出力して exit 0

      実装後に git add・git commit してください。
    timeout_sec: 300

  - id: multiplication
    agent: worktree-developer
    read_only: false
    cwd: ..
    writes:
      - examples/scripts/multiplication.js
    prompt: |
      タスクID: multiplication

      examples/scripts/multiplication.js を実装してください。

      ## 仕様
      - Node.js（use strict）
      - コマンドライン引数 a, b を2つ受け取る
      - どちらかが数値でない場合は stderr にエラーを出力して exit 1
      - 正常時は `a * b = <result>` を標準出力して exit 0

      実装後に git add・git commit してください。
    timeout_sec: 300
---

# Parallel Dev Demo

worktree-developer による並列実装のデモマニフェスト。

`addition.js` と `multiplication.js` を2つの独立した worktree で並列実装し、完了後に main ブランチへマージする。

## 実行方法

```bash
clade-parallel run examples/manifest-parallel-dev.md
```
