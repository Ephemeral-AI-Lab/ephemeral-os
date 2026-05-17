.PHONY: install test lint clean

install:
	uv sync --extra dev

test:
	uv run pytest -q

lint:
	uv run ruff check backend/src backend/tests

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache backend/.pytest_cache
