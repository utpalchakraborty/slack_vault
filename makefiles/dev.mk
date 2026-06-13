.PHONY: check dev format format-check install lint lint-fix pre-commit-install test typecheck

dev: install pre-commit-install

install:
	$(UV) sync --dev

pre-commit-install:
	$(UV_RUN) pre-commit install

format:
	$(UV_RUN) ruff format .

format-check:
	$(UV_RUN) ruff format --check .

lint:
	$(UV_RUN) ruff check .

lint-fix:
	$(UV_RUN) ruff check --fix .

typecheck:
	$(UV_RUN) mypy $(PYTHON_SOURCES)

test:
	$(UV_RUN) pytest

check: format-check lint typecheck test
