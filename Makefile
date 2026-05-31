.PHONY: install-dev lint test test-cov build clean

install-dev:
	pip install -e ".[dev]"

lint:
	ruff check src/ tests/
	mypy src/

test:
	pytest tests/

test-cov:
	pytest tests/ --cov=src/fcompare --cov-report=term-missing --cov-fail-under=90

build:
	python -m build

clean:
	rm -rf dist/ build/ .mypy_cache/ .ruff_cache/ .pytest_cache/ __pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
