.PHONY: all build manifest test test-vm test-storage
all: build
build:
	./tools/build
manifest:
	./tools/build --manifest-only
# Python is a development-only dependency of this external test harness.
test:
	python3 test/integration.py
test-vm:
	python3 test/integration.py --vm
test-storage:
	python3 test/storage.py
	python3 test/storage.py --native
