import os
import uuid
import json
import time
import logging
import boto3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from botocore.exceptions import ClientError

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("emerald-service")

app = FastAPI(title="Emerald Routing Service")

# Environment Variables
REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE", "EmeraldGlobalStore")
# NEW: Get the Lambda function name from environment variables
METRICS_LAMBDA_NAME = os.getenv("METRICS_LAMBDA_NAME")

# AWS Clients
dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)
bedrock_runtime = boto3.client("bedrock-agentcore", region_name=REGION)
# NEW: Lambda Client
lambda_client = boto3.client("lambda", region_name=REGION)

# --- Data Models ---
class ChatRequest(BaseModel):
    session_id: str = str(uuid.uuid4())
    message: str
    agent_id: str

class ChatResponse(BaseModel):
    session_id: str
    response: str
    processing_time: float
    region: str

# --- Routes ---

@app.get("/health")
async def health_check():
    return {"status": "healthy", "region": REGION}

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    start_time = time.time()
    logger.info(f"Received chat request for session {request.session_id}")

    agent_response_text = ""
    error_msg = None

    # 1. Invoke Bedrock Agent
    try:
        logger.info(f"Invoking Agent: {request.agent_id}")
        payload = json.dumps({"prompt": request.message}).encode()

        response = bedrock_runtime.invoke_agent_runtime(
            agentRuntimeArn=request.agent_id,
            qualifier="DEFAULT",
            runtimeSessionId=request.session_id,
            payload=payload
        )

        # ... (Your existing parsing logic here) ...
        # [Abbreviated for brevity, keep your existing parsing logic]
        # For simplicity in this snippet, assuming text response:
        # Check your original code for the full streaming/json parsing block
        agent_response_text = "Simulated Agent Response" # Placeholder for valid parsing
        
        logger.info("Successfully received response from Bedrock Agent")

    except ClientError as e:
        logger.error(f"Bedrock Invocation Failed: {e}")
        agent_response_text = f"Error invoking agent: {str(e)}"
        error_msg = str(e)

    # 2. Write to DynamoDB (Direct History)
    try:
        item = {
            "PK": f"SESSION#{request.session_id}",
            "SK": f"MSG#{int(time.time()*1000)}",
            "UserMessage": request.message,
            "AgentResponse": agent_response_text,
            "Region": REGION,
            "AgentID": request.agent_id,
            "Timestamp": int(time.time())
        }
        table.put_item(Item=item)
    except ClientError as e:
        logger.error(f"DynamoDB Write Failed: {e}")

    # 3. NEW: Invoke Metrics Lambda (Async)
    # We use InvocationType='Event' so we don't wait for the Lambda to finish
    if METRICS_LAMBDA_NAME:
        try:
            metric_payload = {
                "type": "chat_metric",
                "session_id": request.session_id,
                "agent_id": request.agent_id,
                "latency": time.time() - start_time,
                "status": "error" if error_msg else "success",
                "timestamp": int(time.time())
            }
            
            lambda_client.invoke(
                FunctionName=METRICS_LAMBDA_NAME,
                InvocationType='Event', 
                Payload=json.dumps(metric_payload)
            )
            logger.info(f"Invoked Metrics Lambda: {METRICS_LAMBDA_NAME}")
        except Exception as e:
            logger.error(f"Failed to invoke Lambda: {e}")
    else:
        logger.warning("METRICS_LAMBDA_NAME not set, skipping metric emission.")

    processing_time = time.time() - start_time
    return ChatResponse(
        session_id=request.session_id,
        response=agent_response_text,
        processing_time=processing_time,
        region=REGION
    )