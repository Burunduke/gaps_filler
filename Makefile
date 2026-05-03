.PHONY: compile clean test help

help:
	@echo "Available targets:"
	@echo "  compile  Compile resources.qrc -> resources.py via pyrcc5"
	@echo "  clean    Remove generated resources.py"
	@echo "  test     Run plugin tests (placeholder)"
	@echo "  help     Show this message"

compile:
	pyrcc5 -o resources.py resources.qrc

clean:
	rm -f resources.py

test:
	@echo "No tests wired yet"
