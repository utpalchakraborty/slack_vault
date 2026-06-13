.PHONY: init-vault init-vault-overwrite run show-config

run: show-config

show-config:
	$(UV_RUN) slack-vault show-config

init-vault:
	$(UV_RUN) slack-vault init-vault

init-vault-overwrite:
	$(UV_RUN) slack-vault init-vault --overwrite
