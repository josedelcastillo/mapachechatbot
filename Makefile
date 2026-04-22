.PHONY: install synth bootstrap deploy update-frontend-config destroy test-local

install:
	python3 -m venv .venv
	source .venv/bin/activate && pip install -r infra/requirements.txt

synth:
	source .venv/bin/activate && JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1 cdk synth

bootstrap:
	source .venv/bin/activate && JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1 cdk bootstrap

deploy:
	source .venv/bin/activate && JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1 cdk deploy --require-approval never
	$(MAKE) update-frontend-config

update-frontend-config:
	@API_URL=$$(aws cloudformation describe-stacks \
		--stack-name MapacheChatbotStack \
		--query "Stacks[0].Outputs[?ExportName=='MapacheChatbotApiEndpoint'].OutputValue" \
		--output text); \
	FRONTEND_BUCKET=$$(aws cloudformation describe-stacks \
		--stack-name MapacheChatbotStack \
		--query "Stacks[0].Outputs[?ExportName=='MapacheChatbotFrontendBucket'].OutputValue" \
		--output text); \
	DISTRIBUTION_ID=$$(aws cloudformation describe-stacks \
		--stack-name MapacheChatbotStack \
		--query "Stacks[0].Outputs[?ExportName=='MapacheChatbotDistributionId'].OutputValue" \
		--output text); \
	if [ -z "$$API_URL" ]; then echo "ERROR: Stack not deployed or output not found."; exit 1; fi; \
	printf '// Auto-generado por: make deploy\n// No editar manualmente — ejecuta `make deploy` para actualizar\nconst API_URL = "%s";\n' "$$API_URL" > frontend/config.js; \
	echo "frontend/config.js actualizado: $$API_URL"; \
	aws s3 cp frontend/config.js s3://$$FRONTEND_BUCKET/config.js --content-type "application/javascript"; \
	echo "config.js subido a s3://$$FRONTEND_BUCKET/"; \
	aws cloudfront create-invalidation --distribution-id $$DISTRIBUTION_ID --paths "/config.js" "/index.html" > /dev/null; \
	echo "CloudFront invalidado: $$DISTRIBUTION_ID"

destroy:
	source .venv/bin/activate && JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1 cdk destroy

# Test the API locally (requires AWS credentials and a deployed stack)
# Usage: SESSION_ID=test-1 MESSAGE="Mi hijo no quiere estudiar" make test-local
test-local:
	@ENDPOINT=$$(aws cloudformation describe-stacks \
		--stack-name MapacheChatbotStack \
		--query "Stacks[0].Outputs[?ExportName=='MapacheChatbotApiEndpoint'].OutputValue" \
		--output text); \
	curl -s -X POST $$ENDPOINT \
		-H "Content-Type: application/json" \
		-d "{\"session_id\": \"$${SESSION_ID:-test-1}\", \"message\": \"$${MESSAGE:-Hola, necesito ayuda con mi hijo}\"}" \
		| python3 -m json.tool
