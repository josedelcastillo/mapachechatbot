from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_s3_deployment as s3_deployment,
    aws_lambda as lambda_,
    aws_apigateway as apigw,
    aws_iam as iam,
    aws_logs as logs,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
)
from constructs import Construct
import os


class ChatbotStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── DynamoDB: session storage ─────────────────────────────────────────
        sessions_table = dynamodb.Table(
            self,
            "SessionsTable",
            table_name="mapache-chatbot-sessions",
            partition_key=dynamodb.Attribute(
                name="session_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )

        # ── S3: knowledge base storage ────────────────────────────────────────
        kb_bucket = s3.Bucket(
            self,
            "KnowledgeBaseBucket",
            bucket_name=f"mapache-chatbot-kb-{self.account}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    noncurrent_version_expiration=Duration.days(30),
                )
            ],
        )

        # Upload knowledge base JSON to S3 at deploy time
        kb_dir = os.path.join(os.path.dirname(__file__), "..", "knowledge_base")
        s3_deployment.BucketDeployment(
            self,
            "KnowledgeBaseDeployment",
            sources=[s3_deployment.Source.asset(kb_dir)],
            destination_bucket=kb_bucket,
            destination_key_prefix="knowledge_base",
        )

        # ── IAM: Lambda execution role ────────────────────────────────────────
        lambda_role = iam.Role(
            self,
            "ChatbotLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        # DynamoDB permissions
        sessions_table.grant_read_write_data(lambda_role)

        # S3 permissions (KB bucket, read-only)
        kb_bucket.grant_read(lambda_role)

        # Bedrock permissions
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel"],
                resources=[
                    f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"
                ],
            )
        )

        # ── Lambda: chatbot function ──────────────────────────────────────────
        src_dir = os.path.join(os.path.dirname(__file__), "..", "src")

        chatbot_fn = lambda_.Function(
            self,
            "ChatbotFunction",
            function_name="mapache-chatbot",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(src_dir),
            role=lambda_role,
            timeout=Duration.seconds(30),
            memory_size=256,
            environment={
                "DYNAMODB_TABLE_NAME": sessions_table.table_name,
                "S3_BUCKET_NAME": kb_bucket.bucket_name,
                "KB_S3_KEY": "knowledge_base/journey.json",
                "BEDROCK_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
                "AWS_REGION_NAME": self.region,
            },
            log_group=logs.LogGroup(
                self,
                "ChatbotFunctionLogGroup",
                log_group_name="/aws/lambda/mapache-chatbot",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )

        # ── API Gateway ───────────────────────────────────────────────────────
        log_group = logs.LogGroup(
            self,
            "ApiGatewayLogs",
            log_group_name="/mapache-chatbot/api-gateway",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        api = apigw.RestApi(
            self,
            "ChatbotApi",
            rest_api_name="mapache-chatbot-api",
            description="Tinkuy Marka - Mapache Hero's Journey Chatbot API",
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                throttling_burst_limit=50,
                throttling_rate_limit=20,
                logging_level=apigw.MethodLoggingLevel.INFO,
                access_log_destination=apigw.LogGroupLogDestination(log_group),
                access_log_format=apigw.AccessLogFormat.json_with_standard_fields(
                    caller=False,
                    http_method=True,
                    ip=True,
                    protocol=True,
                    request_time=True,
                    resource_path=True,
                    response_length=True,
                    status=True,
                    user=False,
                ),
            ),
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=["POST", "OPTIONS"],
                allow_headers=["Content-Type", "Authorization"],
            ),
        )

        lambda_integration = apigw.LambdaIntegration(
            chatbot_fn,
            request_templates={"application/json": '{"statusCode": "200"}'},
        )

        chat_resource = api.root.add_resource("chat")
        chat_resource.add_method(
            "POST",
            lambda_integration,
            method_responses=[
                apigw.MethodResponse(
                    status_code="200",
                    response_models={"application/json": apigw.Model.EMPTY_MODEL},
                ),
                apigw.MethodResponse(status_code="400"),
                apigw.MethodResponse(status_code="500"),
            ],
        )

        # ── S3: frontend hosting ──────────────────────────────────────────────
        frontend_bucket = s3.Bucket(
            self,
            "ChatBotTinkuyFrontendBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # CloudFront distribution (OAC via S3BucketOrigin)
        distribution = cloudfront.Distribution(
            self,
            "ChatBotTinkuyFrontendDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(
                    frontend_bucket
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
        )

        # Deploy index.html to frontend bucket (config.js uploaded separately by Makefile)
        frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
        s3_deployment.BucketDeployment(
            self,
            "ChatBotTinkuyFrontendDeployment",
            sources=[
                s3_deployment.Source.asset(frontend_dir, exclude=["config.js"])
            ],
            destination_bucket=frontend_bucket,
            distribution=distribution,
            distribution_paths=["/*"],
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        CfnOutput(
            self,
            "ApiEndpoint",
            value=f"{api.url}chat",
            description="Chatbot API endpoint",
            export_name="MapacheChatbotApiEndpoint",
        )

        CfnOutput(
            self,
            "DynamoDBTableName",
            value=sessions_table.table_name,
            description="DynamoDB sessions table",
            export_name="MapacheChatbotSessionsTable",
        )

        CfnOutput(
            self,
            "KnowledgeBaseBucketName",
            value=kb_bucket.bucket_name,
            description="S3 bucket with knowledge base",
            export_name="MapacheChatbotKnowledgeBaseBucket",
        )

        CfnOutput(
            self,
            "LambdaFunctionName",
            value=chatbot_fn.function_name,
            description="Chatbot Lambda function",
            export_name="MapacheChatbotFunction",
        )

        CfnOutput(
            self,
            "FrontendUrl",
            value=f"https://{distribution.distribution_domain_name}",
            description="Frontend CloudFront URL",
            export_name="MapacheChatbotFrontendUrl",
        )

        CfnOutput(
            self,
            "FrontendBucketName",
            value=frontend_bucket.bucket_name,
            description="Frontend S3 bucket",
            export_name="MapacheChatbotFrontendBucket",
        )

        CfnOutput(
            self,
            "DistributionId",
            value=distribution.distribution_id,
            description="CloudFront distribution ID",
            export_name="MapacheChatbotDistributionId",
        )
