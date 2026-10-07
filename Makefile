PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: install test lint demo run docker clean

install:  ## create a virtualenv and install dependencies
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -r requirements-dev.txt

test:     ## run the offline test suite (no keys, no network)
	$(BIN)/python -m pytest

lint:     ## static checks (ruff)
	$(BIN)/ruff check product_extractor scripts tests

demo:     ## extract the six fictional fixture pages and print a table
	$(BIN)/python -m scripts.demo

run:      ## start the API on http://localhost:8000 (docs at /docs)
	$(BIN)/uvicorn product_extractor.api:app --reload --port 8000

docker:   ## build and start with docker compose
	docker compose up --build

clean:
	rm -rf $(VENV) .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
