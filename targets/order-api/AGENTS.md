# order-api

这是 Forge 盯着的演示下单服务，端口 8790。

- 一次只修 `active_bug.txt` 里的那一题。不要改别的 bug，也不要改这个文件。
- 先看金丝雀 `/canary` 和进程日志，再改代码。
- 改完用 `run_tests`（本仓库 tests）。测试过了再 `git_commit` / `git_push`，最后 `restart_service`。
- 不要用重启当试错。不要 force push。
