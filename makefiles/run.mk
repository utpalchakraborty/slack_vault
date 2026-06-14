.PHONY: ask ingest-file init-vault init-vault-overwrite run show-config

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
