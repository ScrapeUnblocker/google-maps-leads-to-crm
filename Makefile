.PHONY: install lint format test run dry-run status test-destinations clean

install:
	python -m pip install -e ".[dev]"

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

test:
	pytest

run:
	google-maps-leads run

dry-run:
	google-maps-leads run --dry-run

status:
	google-maps-leads status

test-destinations:
	google-maps-leads test-destinations

clean:
	rm -rf build dist src/*.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
