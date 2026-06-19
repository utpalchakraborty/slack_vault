.PHONY: ask check-slack-setup ingest-file init-vault init-vault-overwrite run run-slack show-config slack-qa-worker slack-worker validate-vault-diff

run: show-config

show-config:
	$(UV_RUN) slack-vault show-config

init-vault:
	$(UV_RUN) slack-vault init-vault

init-vault-overwrite:
	$(UV_RUN) slack-vault init-vault --overwrite

ingest-file:
ifndef FILE
	$(error FILE is required. Usage: make ingest-file FILE=path/to/source)
endif
	$(UV_RUN) slack-vault ingest-file "$(FILE)" $(if $(UPLOADED_BY),--uploaded-by "$(UPLOADED_BY)") $(if $(ENHANCE),--enhance) $(if $(SYNTHESIZE),--synthesize) $(if $(NO_GIT_COMMIT),--no-git-commit)

ask:
ifndef QUESTION
	$(error QUESTION is required. Usage: make ask QUESTION="question")
endif
	$(UV_RUN) slack-vault ask "$(QUESTION)" $(if $(LIMIT),--limit "$(LIMIT)")

validate-vault-diff:
ifndef SOURCE_ID
	$(error SOURCE_ID is required. Usage: make validate-vault-diff SOURCE_ID=source-...)
endif
	$(UV_RUN) slack-vault validate-vault-diff --source-id "$(SOURCE_ID)" $(if $(PRIMARY_NOTE),--primary-note "$(PRIMARY_NOTE)")

run-slack:
	$(UV_RUN) slack-vault run-slack

check-slack-setup:
	$(UV_RUN) slack-vault check-slack-setup

slack-worker:
	$(UV_RUN) slack-vault slack-worker $(if $(ONCE),--once)

slack-qa-worker:
	$(UV_RUN) slack-vault slack-qa-worker $(if $(ONCE),--once)
