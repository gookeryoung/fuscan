# Makefile - fuscan 项目快捷命令
# 运行 `make help` 查看所有可用命令

PACKAGE := fuscan
COV_THRESHOLD := 95
# 与 CI 一致：排除 slow（benchmark 用）测试
TEST_MARKERS := "not slow"
# 性能回归门禁：iter-1 ContentRegexPool 优化成果保护
PERF_TEST := tests/test_perf_regression.py
PERF_THRESHOLD := 10

.PHONY: help sync build b clean c test cov lint typecheck check doc tox bump patch minor major push perf perf-compare perf-list bump-core

help: ## 显示帮助信息
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z].*:.*##/ {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

sync: ## 安装开发依赖
	uv sync --extra dev

build b: ## 构建分发包 (wheel + sdist)
	uv build

clean c: ## 清理构建产物与缓存
	rm -rf build/ dist/ wheels/ fuscan-core-wheels/ *.egg-info htmlcov/ .coverage .coverage.* coverage.xml docs/_build/ .tox/
	rm -rf .ruff_cache/ .pyrefly_cache/ .mypy_cache/
	rm -rf packages/fuscan-core/target/ packages/fuscan-core/dist/
	find src tests -type d -name __pycache__ -exec rm -rf {} +
	find src tests -type f -name "*.py[oc]" -delete

download: ## 下载 OCR 模型
	python scripts/download_ocr_models.py

test: ## 运行测试（不含覆盖率，与 CI 一致）
	uv run pytest -m $(TEST_MARKERS)

cov: ## 运行测试并检查覆盖率
	uv run pytest -m $(TEST_MARKERS) --cov=$(PACKAGE) --cov-fail-under=$(COV_THRESHOLD) -n 8

lint: ## 代码风格检查 (ruff)
	uv run ruff check .
	uv run ruff format --check .

typecheck: ## 类型检查 (pyrefly)
	uv run pyrefly check

check: lint typecheck cov ## 运行全套门禁 (lint + typecheck + cov)

perf: ## 运行性能基准并保存为基线（首次建立或刷新基线用）
	uv run pytest -m slow $(PERF_TEST) --benchmark-save=baseline --benchmark-disable-gc --benchmark-warmup=on

perf-compare: ## 与基线对比性能，mean 退化 >$(PERF_THRESHOLD)% 失败
	uv run pytest -m slow $(PERF_TEST) --benchmark-compare \
		--benchmark-compare-fail=mean:$(PERF_THRESHOLD)% \
		--benchmark-disable-gc --benchmark-warmup=on

perf-list: ## 列出已保存的性能基线
	uv run pytest --benchmark-list

doc: ## 构建 Sphinx 文档（HTML + PDF 速查表）
	uv run --extra docs sphinx-build -b html docs docs/_build/html
	uv run python scripts/generate_manual_pdf.py


tox: ## 多版本测试 (tox)
	uvx tox -p auto

BUMP_PART := $(filter-out bump,$(MAKECMDGOALS))

bump: ## 版本号 bump (默认 patch，用法: make bump [minor|major])
	@uvx bump-my-version bump $(if $(BUMP_PART),$(firstword $(BUMP_PART)),patch) --tag

patch minor major:
	@:

pub:  ## 推送到pypi
	uvx twine upload ./dist/**

push: ## 推送代码到所有远程仓库
	@uv run python -c "import subprocess as sp; [print(f'\u63a8\u9001 {r}...',flush=True) or (sp.run(['git','push',r],check=True) and sp.run(['git','push',r,'--tags'],check=True)) for r in sp.check_output(['git','remote'],text=True).split()]"

CORE_BUMP_PART := $(filter-out bump-core,$(filter-out bump,$(MAKECMDGOALS)))

bump-core: ## fuscan-core 版本 bump (默认 patch，用法: make bump-core [minor|major])
	@uvx bump-my-version bump --config-file packages/fuscan-core/.bumpversion.toml $(if $(CORE_BUMP_PART),$(firstword $(CORE_BUMP_PART)),patch) --tag

