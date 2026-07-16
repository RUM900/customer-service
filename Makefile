.PHONY: run test clean

run:
	uvicorn src.main:app --host 0.0.0.0 --port 8000

test:
	pytest tests/ -v

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
