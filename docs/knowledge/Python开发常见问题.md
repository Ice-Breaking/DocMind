# Python 开发常见问题手册

## 虚拟环境

### 为什么要用虚拟环境？
虚拟环境让每个项目拥有独立的依赖版本，避免不同项目之间的包冲突。常用工具有 venv（标准库自带）、conda、uv。

### 如何创建和激活虚拟环境？
创建：`python3 -m venv .venv`。激活：macOS/Linux 执行 `source .venv/bin/activate`，Windows 执行 `.venv\Scripts\activate`。退出用 `deactivate`。

### requirements.txt 怎么用？
导出依赖：`pip freeze > requirements.txt`。安装依赖：`pip install -r requirements.txt`。国内网络慢时加阿里云镜像源：`-i https://mirrors.aliyun.com/pypi/simple/`。

## 常见语法问题

### list 和 tuple 有什么区别？
list 可变，tuple 不可变。tuple 因为不可变所以可以作为字典的键，且创建和访问更快。函数返回多个值时习惯用 tuple。

### 深拷贝和浅拷贝的区别？
浅拷贝（copy）只复制最外层容器，内部元素仍是引用共享；深拷贝（deepcopy）递归复制所有层级。修改嵌套结构时要注意区分，避免意外的数据污染。

### 什么是装饰器？
装饰器是接收函数并返回新函数的高阶函数，用 @语法 应用。常用于日志记录、权限校验、缓存、重试等横切逻辑，是 Python 工程化开发的常用手段。

## 异步编程

### asyncio 的事件循环是什么？
事件循环是 asyncio 的核心调度器，负责执行异步任务、处理 IO 事件。同一个异步资源（如网络连接）必须在同一个事件循环中使用，跨循环使用会报错，这是新手常见的坑。

### async def 和普通函数有什么区别？
async def 定义协程函数，调用它返回协程对象而不会立即执行，需要用 await 或事件循环驱动。协程适合 IO 密集型场景（网络请求、数据库查询），CPU 密集型任务应使用多进程。

## 依赖管理最佳实践

### 如何锁定依赖版本？
生产项目应钉住依赖版本范围，尤其是大版本边界。例如 `mcp>=1.2.0,<2.0.0` 防止升级到不兼容的大版本。更严格的方案是用 pip-tools 或 uv 生成锁文件。

### 如何排查依赖冲突？
用 `pip check` 检查已安装包的依赖冲突，用 `pipdeptree` 查看依赖树。出现版本冲突时优先满足核心库的要求，再调整外围依赖。
