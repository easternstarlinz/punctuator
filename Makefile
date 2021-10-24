PY_SOURCE_FILES=data_process/ training/ #this can be modified to include more files

test:
	python -m unittest discover -s test -p '*_test.py'

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache
	find . -name '*.pyc' -type f -exec rm -rf {} +
	find . -name '__pycache__' -exec rm -rf {} +

format:
	autoflake --in-place --remove-all-unused-imports --recursive ${PY_SOURCE_FILES}
	isort ${PY_SOURCE_FILES}
	black ${PY_SOURCE_FILES}

lint:
	isort --check --diff ${PY_SOURCE_FILES}
	black --check --diff ${PY_SOURCE_FILES}
	flake8 ${PY_SOURCE_FILES} --count --show-source --statistics --max-line-length 120
	revive -config revive.toml -formatter friendly .

