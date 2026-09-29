.PHONY: test generate benchmark report dashboard kafka-up kafka-down kafka-setup kafka-smoke distributed

PYTHON ?= python3
KAFKA_PYTHON ?= .venv/bin/python

test:
	$(PYTHON) -m unittest discover -s tests -v

generate:
	$(PYTHON) -m pathaware_governance.generator

benchmark:
	$(PYTHON) -m pathaware_governance.consistency --output benchmark-results
	$(PYTHON) -m pathaware_governance.benchmark --output benchmark-results

report:
	$(PYTHON) -m pathaware_governance.reporting --results benchmark-results

dashboard:
	$(PYTHON) -m pathaware_governance.dashboard --results benchmark-results

kafka-up:
	docker compose up -d --wait

kafka-down:
	docker compose stop

kafka-setup:
	$(KAFKA_PYTHON) -m pathaware_governance.streaming setup --partitions 12

kafka-smoke:
	$(KAFKA_PYTHON) -m pathaware_governance.kafka_smoke

distributed:
	$(KAFKA_PYTHON) -m pathaware_governance.distributed

