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
# Logs sent to stdout automatically go to CloudWatch in ECS Fargate
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("emerald-service")

app = FastAPI(title="Emerald Routing Service")

# Environment Variables (Injected by ECS Task Definition)
REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE", "EmeraldGlobalStore")

# AWS Clients
# Note: No explicit credentials needed; ECS Task Role provides them automatically.
dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)
# Use the AgentCore runtime client per the Bedrock AgentCore runtime docs
# Correct service name: 'bedrock-agentcore' (not 'bedrock-agent-runtime')
bedrock_runtime = boto3.client("bedrock-agentcore", region_name=REGION)

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
    """Simple health check for the ALB Target Group."""
    return {"status": "healthy", "region": REGION}

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    start_time = time.time()
    logger.info(f"Received chat request for session {request.session_id}")

    # 1. Invoke Bedrock Agent
    agent_response_text = ""
    try:
        logger.info(f"Invoking Agent: {request.agent_id} (Alias: {request.agent_alias_id})")
        
        # Use the AgentCore runtime invocation API which expects a runtime ARN
        # and a payload (bytes). The API returns either a streaming event
        # stream (text/event-stream) or application/json content.
        payload = json.dumps({"prompt": request.message}).encode()

        response = bedrock_runtime.invoke_agent_runtime(
            agentRuntimeArn=request.agent_id,
            qualifier="DEFAULT",
            runtimeSessionId=request.session_id,
            payload=payload
        )

        # Parse the response payload depending on content type
        content_type = response.get("contentType", "")
        if "text/event-stream" in content_type:
            # streaming response: build text lines that begin with 'data: '
            for line in response["response"].iter_lines(chunk_size=10):
                if not line:
                    continue
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    agent_response_text += line[6:]

        elif response.get("contentType") == "application/json":
            # response['response'] may be an iterable of bytes
            json_chunks = []
            for chunk in response.get("response", []):
                json_chunks.append(chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk))
            try:
                parsed = json.loads(''.join(json_chunks))
                # store a readable string version in the response field
                agent_response_text = json.dumps(parsed)
            except Exception:
                agent_response_text = ''.join(json_chunks)

        else:
            # fallback: attempt to coerce raw response into string
            raw = response.get("response")
            try:
                if isinstance(raw, (bytes, bytearray)):
                    agent_response_text = raw.decode("utf-8", errors="ignore")
                else:
                    agent_response_text = str(raw)
            except Exception:
                agent_response_text = str(response)
        
        logger.info("Successfully received response from Bedrock Agent")

    except ClientError as e:
        logger.error(f"Bedrock Invocation Failed: {e}")
        # Fallback to allow testing DynamoDB even if Agent fails
        agent_response_text = f"Error invoking agent: {str(e)}"

    # 2. Write Metrics/History to DynamoDB Global Table
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
        logger.info(f"Successfully wrote to DynamoDB table: {DYNAMODB_TABLE_NAME}")
    except ClientError as e:
        logger.error(f"DynamoDB Write Failed: {e}")
        raise HTTPException(status_code=500, detail="Database write failed")

    # 3. Return Response
    processing_time = time.time() - start_time
    return ChatResponse(
        session_id=request.session_id,
        response=agent_response_text,
        processing_time=processing_time,
        region=REGION
    )