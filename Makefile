verify:
	python3 -m unittest discover -v

demo:
	python3 runtime_protocol.py

demo-map:
	python3 reconciliation.py

.PHONY: verify demo demo-map
