# Python 开发常见问题

## 1. 虚拟环境管理

### 创建虚拟环境
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate   # Windows
```

### 为什么要使用虚拟环境？
- 隔离项目依赖
- 避免版本冲突
- 便于项目迁移

## 2. pip 包管理

### 安装依赖
```bash
pip install requests
pip install -r requirements.txt
```

### 导出依赖
```bash
pip freeze > requirements.txt
```

## 3. 常见错误及解决

### ModuleNotFoundError
**原因**：模块未安装或虚拟环境未激活

**解决**：
1. 确认虚拟环境已激活
2. 运行 `pip install <module_name>`

### IndentationError
**原因**：缩进不一致（混用空格和 Tab）

**解决**：统一使用 4 个空格缩进

## 4. 性能优化技巧

- 使用列表推导式代替 for 循环
- 使用生成器处理大文件
- 使用 `__slots__` 减少内存占用
- 使用 `lru_cache` 缓存函数结果
