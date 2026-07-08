# Publishing to PyPI

把 `codegraph-py` 发到 PyPI 的标准流程。

## 前置条件

```bash
python -m pip install --upgrade build twine
```

发布者账号：https://pypi.org/account/register/ — 首次发包需要邮箱确认。

## 配置 PyPI Token（推荐）

1. 去 https://pypi.org/manage/account/token/ 生成 API token
2. **作用域选 "Entire account"（首次发包必须）**；同包二次上传选 "Project: codegraph-py"
3. 配置 `~/.pypirc`：

```ini
[distutils]
index-servers =
    pypi

[pypi]
username = __token__
password = pypi-AgEIcHlwaS5vcmcC...（你的 token）
```

> 不要把 token 提交进 git。CI 发布走 GitHub Secrets。

## 发布流程

### 1. 确认版本号

修改 `pyproject.toml` 中 `version = "..."` 和 `codegraph/__init__.py` 中 `__version__ = "..."` 保持一致。

### 2. 清理旧构建产物

```bash
rm -rf dist/ build/ *.egg-info codegraph_py.egg-info
```

### 3. 跑测试

```bash
python -m pip install -e ".[all]"
python -m pytest tests/ -v
```

必须 **25/25 全过** 才允许发布。

### 4. 构建 sdist + wheel

```bash
python -m build --sdist --wheel
```

产物在 `dist/`：
- `codegraph_py-X.Y.Z-py3-none-any.whl`
- `codegraph_py-X.Y.Z.tar.gz`

### 5. 检查包元数据

```bash
python -m twine check dist/*
```

必须看到 `PASSED` 才允许上传。

### 6. 上传到 TestPyPI（强烈推荐）

```bash
python -m twine upload --repository testpypi dist/*
```

去 https://test.pypi.org/project/codegraph-py/ 确认包信息正确（description、classifiers、URLs 都能正常显示）。

### 7. 从 TestPyPI 安装验证

```bash
python -m venv /tmp/cg-verify
/tmp/cg-verify/bin/python -m pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    "codegraph-py[all]==X.Y.Z"

/tmp/cg-verify/bin/codegraph --version
/tmp/cg-verify/bin/codegraph init /tmp/cg-smoke && cd /tmp/cg-smoke && \
    /tmp/cg-verify/bin/codegraph status
```

### 8. 上传到正式 PyPI

```bash
python -m twine upload dist/*
```

## 自动化：GitHub Actions

CI 文件模板见 `.github/workflows/publish.yml`（待补）。简化流程：

- 监听 `v*` tag push
- 跑 lint + 25/25 测试
- 构建 sdist+wheel
- `twine upload` 用 `PYPI_API_TOKEN` secret

## 常见错误

| 错误 | 原因 | 修复 |
|---|---|---|
| `License classifiers have been superseded` | 用了 `License :: OSI Approved :: ...` classifier + setuptools≥77 + PEP 639 | 改用 `license = "MIT"` 表达式并删 classifier |
| `BackendUnavailable: setuptools.backends._legacy` | 错的 build-backend 路径 | 改用 `setuptools.build_meta` |
| `File "x.egg-info/METADATA" not found` | 用户环境 dist-info 损坏 | 用干净的 `python -m venv` 重建 |
| `Object of type ExtractionError is not JSON serializable` | 早期版本的 bug，已修 | 升级到 ≥ 1.0.1 |

## 版本策略

- 修 bug → patch（1.0.0 → 1.0.1）
- 加新功能（向后兼容）→ minor（1.0.0 → 1.1.0）
- 破坏性 API 变更 → major（1.0.0 → 2.0.0）