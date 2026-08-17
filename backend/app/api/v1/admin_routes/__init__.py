"""admin 域路由拆分包。

每个子域收敛为独立模块，admin.py 只保留根 router（prefix=/admin + RBAC
依赖）并依次 include 子 router：accounts → audit → crawl → position_reviews
→ position_edit → config（顺序即路由注册顺序，/positions/pending 必须先于
/positions/{position_name} 注册）。供 admin.py facade 显式导入，模块本身
不触发隐式副作用。
"""
