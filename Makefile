.DEFAULT_GOAL := help

UV ?= uv
UV_RUN := $(UV) run
PYTHON_SOURCES := src tests

include makefiles/dev.mk
include makefiles/run.mk

.PHONY: help
help:
	@printf "Development targets:\n"
	@printf "  make dev                  Install dev dependencies and hooks\n"
	@printf "  make install              Install dev dependencies\n"
	@printf "  make format               Format Python code\n"
	@printf "  make check                Run format check, lint, mypy, and tests\n"
	@printf "\nRun targets:\n"
	@printf "  make show-config          Print resolved app settings\n"
	@printf "  make init-vault           Initialize configured Obsidian vault\n"
	@printf "  make init-vault-overwrite Rewrite starter vault files\n"
