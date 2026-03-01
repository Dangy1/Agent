.PHONY: \
	up down logs ps \
	up-source down-source logs-source ps-source \
	bootstrap-source apply-schema-source ingest-sample-source \
	extract-nasr-url extract-nasr-zip extract-nasr-dir \
	ingest-extracted extract-and-ingest-nasr-url extract-and-ingest-nasr-zip

ENV_FILE ?= .env
NASR_WORK_DIR ?= ./work/nasr
NASR_OUTPUT_DIR ?= ./work/normalized
NASR_EFFECTIVE_FROM ?=
NASR_EFFECTIVE_TO ?= 9999-12-31
NASR_DATASET_VERSION ?=
NASR_AIRAC_CYCLE ?=
NASR_SOURCE_URL ?=
NASR_ZIP_URL ?=
NASR_ZIP_PATH ?=
NASR_DIR ?=

up:
	docker compose --env-file $(ENV_FILE) -f docker-compose.postgis.yml up -d

down:
	docker compose --env-file $(ENV_FILE) -f docker-compose.postgis.yml down

logs:
	docker compose --env-file $(ENV_FILE) -f docker-compose.postgis.yml logs -f

ps:
	docker compose --env-file $(ENV_FILE) -f docker-compose.postgis.yml ps

up-source:
	docker compose --env-file $(ENV_FILE) -f docker-compose.postgis-source.yml up -d

down-source:
	docker compose --env-file $(ENV_FILE) -f docker-compose.postgis-source.yml down

logs-source:
	docker compose --env-file $(ENV_FILE) -f docker-compose.postgis-source.yml logs -f

ps-source:
	docker compose --env-file $(ENV_FILE) -f docker-compose.postgis-source.yml ps

bootstrap-source:
	./scripts/bootstrap_postgis_source.sh

apply-schema-source:
	./scripts/apply_schema_via_container.sh

ingest-sample-source:
	@set -a; . ./$(ENV_FILE); set +a; \
	python ingest_faa_airspace.py \
		--dsn "$$FAA_AIRSPACE_DSN" \
		--dataset NASR \
		--dataset-version 2025-11-27 \
		--airac-cycle 2025-11-27 \
		--effective-from 2025-11-27 \
		--effective-to 2025-12-24 \
		--source-url "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2025-11-27/" \
		--features-csv samples/features.csv \
		--volumes-geojson samples/volumes.geojson \
		--schedules-csv samples/schedules.csv

extract-nasr-url:
	@test -n "$(NASR_ZIP_URL)" || (echo "Set NASR_ZIP_URL=<https://...zip>"; exit 1)
	@test -n "$(NASR_EFFECTIVE_FROM)" || (echo "Set NASR_EFFECTIVE_FROM=YYYY-MM-DD"; exit 1)
	python extract_nasr_to_normalized.py \
		--nasr-zip-url "$(NASR_ZIP_URL)" \
		--work-dir "$(NASR_WORK_DIR)" \
		--output-dir "$(NASR_OUTPUT_DIR)" \
		--effective-from "$(NASR_EFFECTIVE_FROM)" \
		--effective-to "$(NASR_EFFECTIVE_TO)"

extract-nasr-zip:
	@test -n "$(NASR_ZIP_PATH)" || (echo "Set NASR_ZIP_PATH=/path/to/NASR.zip"; exit 1)
	@test -n "$(NASR_EFFECTIVE_FROM)" || (echo "Set NASR_EFFECTIVE_FROM=YYYY-MM-DD"; exit 1)
	python extract_nasr_to_normalized.py \
		--nasr-zip-path "$(NASR_ZIP_PATH)" \
		--work-dir "$(NASR_WORK_DIR)" \
		--output-dir "$(NASR_OUTPUT_DIR)" \
		--effective-from "$(NASR_EFFECTIVE_FROM)" \
		--effective-to "$(NASR_EFFECTIVE_TO)"

extract-nasr-dir:
	@test -n "$(NASR_DIR)" || (echo "Set NASR_DIR=/path/to/extracted_nasr"; exit 1)
	@test -n "$(NASR_EFFECTIVE_FROM)" || (echo "Set NASR_EFFECTIVE_FROM=YYYY-MM-DD"; exit 1)
	python extract_nasr_to_normalized.py \
		--nasr-dir "$(NASR_DIR)" \
		--work-dir "$(NASR_WORK_DIR)" \
		--output-dir "$(NASR_OUTPUT_DIR)" \
		--effective-from "$(NASR_EFFECTIVE_FROM)" \
		--effective-to "$(NASR_EFFECTIVE_TO)"

ingest-extracted:
	@test -n "$(NASR_EFFECTIVE_FROM)" || (echo "Set NASR_EFFECTIVE_FROM=YYYY-MM-DD"; exit 1)
	@test -n "$(NASR_DATASET_VERSION)" || (echo "Set NASR_DATASET_VERSION=YYYY-MM-DD"; exit 1)
	@set -a; . ./$(ENV_FILE); set +a; \
	python ingest_faa_airspace.py \
		--dsn "$$FAA_AIRSPACE_DSN" \
		--dataset NASR \
		--dataset-version "$(NASR_DATASET_VERSION)" \
		--airac-cycle "$(NASR_AIRAC_CYCLE)" \
		--source-url "$(NASR_SOURCE_URL)" \
		--effective-from "$(NASR_EFFECTIVE_FROM)" \
		--effective-to "$(NASR_EFFECTIVE_TO)" \
		--features-csv "$(NASR_OUTPUT_DIR)/features.csv" \
		--volumes-geojson "$(NASR_OUTPUT_DIR)/volumes.geojson" \
		--schedules-csv "$(NASR_OUTPUT_DIR)/schedules.csv"

extract-and-ingest-nasr-url: extract-nasr-url ingest-extracted

extract-and-ingest-nasr-zip: extract-nasr-zip ingest-extracted
