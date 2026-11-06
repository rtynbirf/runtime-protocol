verify:
	python3 -m unittest discover -v

demo:
	python3 runtime_protocol.py

.PHONY: verify demo
