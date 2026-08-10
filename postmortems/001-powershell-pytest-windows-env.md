# PowerShell 与 pytest Windows 环境坑

**日期**: 2026-08-10
**影响**: 提交命令整体未执行、全量测试统计被掩盖
**发现人**: 主代理

## 问题

同一阶段收尾时连续踩两个 Windows/PowerShell 环境坑，均造成可避免的重试：git commit 多行消息用 bash heredoc 语法解析失败，整条命令（含前置 git add）未执行；pytest 在系统临时目录清理时报 PermissionError，掩盖测试 summary 与退出码。

## 现象

1. `git commit -m "$(cat <<'EOF' ... EOF)"` 报错：

```
ParserError: Missing file specification after redirection operator.
```

2. `pytest -q` 全部测试通过但退出码非 0：

```
PermissionError: [WinError 5] 拒绝访问。: '...\Temp\pytest-of-87088\pytest-current'
```

## 根因

- 错误假设：环境是 bash。实际终端是 PowerShell，`<<` 重定向与 `$()` 内 heredoc 语法不被支持，且 ParserError 发生在命令执行前，整条链式命令全部未执行
- 实际约束：pytest 在 Windows 上清理 `%LOCALAPPDATA%\Temp\pytest-of-<user>\pytest-current` 时，残留句柄或系统权限导致 `cleanup_dead_symlinks` 抛 PermissionError，发生在 sessionfinish 阶段，先于 summary 打印，掩盖测试结果与真实退出码

## 修复

1. 改用多个 `-m` 参数传多段提交信息（`git commit -m "标题" -m "正文"`）
2. 用 `pytest --basetemp=<项目内目录> -q` 绕开系统临时目录，测试后用后清理

## 预防

- 任何 git 提交带多行消息时，在 PowerShell 下用多个 `-m` 参数，不用 heredoc
- 全量测试命令固定加 `--basetemp=.pytest_tmp`（项目 gitignore 内），不要依赖系统临时目录；`.pytest_tmp` 用后删除
