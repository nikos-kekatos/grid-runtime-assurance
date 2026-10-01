# Reproduce the results of the chapter
#   "From Measurement to Enforcement in IoT-Instrumented Distribution Grids".
#
#   make results   re-run the three experiment harnesses (about 25 minutes)
#   make verify    compare the fresh run with checks/reference/
#   make engines   re-run the five-engine comparison (needs Docker and Java, see README)

PY ?= python3

.PHONY: results verify engines clean

results:
	mkdir -p results
	cd harness && $(PY) run_energy_experiments.py > ../results/energy.out
	cd harness && $(PY) powerflow_validation.py   > ../results/powerflow.out
	cd harness && $(PY) voltage_sensitivity.py    > ../results/voltage.out
	@echo "transcripts in results/; now run 'make verify'"

verify:
	$(PY) verify.py

engines:
	cd harness && $(PY) sota_comparison.py

clean:
	rm -rf results harness/__pycache__ vendor/__pycache__
