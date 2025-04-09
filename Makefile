.PHONY: build run pip-update

RED="\033[0;31m"
BLUE="\033[0;34m"
RC="\033[0m"
NL="\n"

IMAGE := registry.gitlab.cvut.cz/rasemailcz/mamrakovinucz/mamrakovinucz
PWD := $(shell pwd)


build:
	@echo -e $(NL)$(RED)" >>>"$(RC) Building image $(NL)
	docker build --tag $(IMAGE) .

run: build
	@echo -e $(NL)$(RED)" >>>"$(RC) Starting application $(NL)
	@docker run $(IMAGE)


pip-update:
	@echo -e $(NL)$(RED)" >>>"$(RC) Updating pipenv dependencies $(NL)
	docker run --rm \
		-v "$(PWD)":/pipenv_tmp:rw \
		-w /pipenv_tmp \
		$(IMAGE) \
		pipenv lock --dev
	@echo -e $(NL)$(RED)" >>>"$(RC) Rebuilding image $(NL)
	docker build --tag $(IMAGE) .
