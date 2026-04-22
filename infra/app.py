#!/usr/bin/env python3
import aws_cdk as cdk
from chatbot_stack import ChatbotStack

app = cdk.App()

ChatbotStack(
    app,
    "MapacheChatbotStack",
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region") or "us-east-1",
    ),
    description="Tinkuy Marka - Mapache Hero's Journey Chatbot",
)

app.synth()
