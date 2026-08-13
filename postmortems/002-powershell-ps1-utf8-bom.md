# PowerShell 5.1 读取无 BOM UTF-8 脚本导致 ParserError（引号被中文吞掉）

**日期**: 2026-08-13
**影响**: `scheduled_tasks.ps1` 从未能执行 → 6 个 Windows 计划任务（ETL 05:00 + 5 爬虫 cron）从未注册 → 08-08 起 T+1 违约
**发现人**: 主代理（全流程闭环审查 P0 修复）

## 问题

`scheduled_tasks.ps1`（含中文注释）以无 BOM UTF-8 保存。PowerShell 5.1（Windows PowerShell，非 pwsh 7）读取脚本文件时按系统 ANSI 代码页（中文系统 = GBK）解码，UTF-8 多字节序列中的字节被误读为引号字符 `"` / `'`，导致字符串意外截断，解析报错：

```
所在位置 ...scheduled_tasks.ps1:103 字符: 124
+ ... th 'uv' -ArgumentList 'run','arq','app.workers.tasks.WorkerSettings'"
+                                                                         ~
字符串缺少终止符: "。
    + CategoryInfo          : ParserError: (:) [], ParseException
```

## 根因

- Windows PowerShell 5.1 对无 BOM 的 .ps1 按 ANSI 代码页解码（UTF-8 需 BOM 才能被识别）
- 中文 UTF-8 字节中部分字节值恰好落在引号字符区间 → ParserError 在解析阶段即失败，脚本完全不可用
- 与 001（PowerShell 终端执行 git heredoc 失败）同源：都是 PowerShell 5.1 对 UTF-8 的处理差异，但触发点不同（001 是交互命令，002 是脚本文件解码）

## 修复

脚本文件头加 UTF-8 BOM（3 字节 `EF BB BF`），PowerShell 5.1 即按 UTF-8 解码。BOM 修复后脚本正常解析并注册 6 个计划任务。

## 预防

- 仓库内任何 `.ps1` 文件必须保存为 **UTF-8 with BOM**（尤其含中文注释时）
- 新增/编辑 .ps1 后先在本机 PowerShell 5.1 执行 `powershell -NoProfile -Command "& '.\script.ps1'"` 冒烟验证，确认无 ParserError 再提交
- 判断依据：`head -c 3 file.ps1 | xxd` 应为 `efbbbf` 开头；若为 `23 20`（`# `）即无 BOM
