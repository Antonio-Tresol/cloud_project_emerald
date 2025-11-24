import os
import uuid
import json
import time
import logging
import boto3
from fastapi import FastAPI
from pydantic import BaseModel, Field
from botocore.exceptions import ClientError

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("emerald-service")

app = FastAPI(title="Emerald Routing Service")

# Environment Variables
REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE")
METRICS_LAMBDA_NAME = os.getenv("METRICS_LAMBDA_NAME")

# --- AWS Clients ---
# Resilient initialization: If a client fails to load, the app starts but logs the error.

try:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(DYNAMODB_TABLE_NAME)
except Exception as e:
    logger.error(f"Failed to initialize DynamoDB client: {e}")
    table = None

# try:
#     # Using the exact client name you insisted on
#     bedrock_client = boto3.client("bedrock-agentcore", region_name=REGION)
# except Exception as e:
#     logger.error(f"Failed to initialize Bedrock AgentCore client: {e}")
#     bedrock_client = None

try:
    lambda_client = boto3.client("lambda", region_name=REGION)
except Exception as e:
    logger.error(f"Failed to initialize Lambda client: {e}")
    lambda_client = None


# --- Data Models ---
class ChatRequest(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message: str
    agent_id: str

class ChatResponse(BaseModel):
    session_id: str
    response: str
    processing_time: float
    region: str
    status: str
    # Added optional error field to return error details without crashing
    error: str | None = None

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
    status_code = "success"

    try:
        # -------------------------------------------------------
        # 1. Invoke Bedrock Agent (Strands Server)
        # -------------------------------------------------------
        # if bedrock_client:
        #     try:
        #         # MATCHING THE STRANDS AGENT SCHEMA:
        #         # class InvocationRequest(BaseModel): prompt: str
        #         payload_dict = {"prompt": request.message}
        #         payload_bytes = json.dumps(payload_dict).encode('utf-8')

        #         logger.info(f"Invoking Agent: {request.agent_id} with payload keys: {list(payload_dict.keys())}")
                
        #         # Using the specific invoke_agent_runtime from your snippet
        #         response = bedrock_client.invoke_agent_runtime(
        #             agentRuntimeArn=request.agent_id,
        #             runtimeSessionId=request.session_id,
        #             payload=payload_bytes
        #         )
                
        #         # Fallback to string representation if parsing fails later
        #         raw_output_string = str(response)

        #         # --- Extract 'output' from Strands InvocationResponse ---
        #         # The agent returns a dictionary where one key is a StreamingBody.
        #         try:
        #             # 1. Handle direct StreamingBody (rare but possible in some boto3 mocks)
        #             if hasattr(response, 'read'):
        #                 raw_output_string = response.read().decode('utf-8')
                    
        #             # 2. Handle standard boto3 dict response
        #             elif isinstance(response, dict):
        #                 # Check specific keys known to contain the stream
        #                 if 'body' in response and hasattr(response['body'], 'read'):
        #                     raw_output_string = response['body'].read().decode('utf-8')
        #                 elif 'response' in response and hasattr(response['response'], 'read'):
        #                     # THIS IS THE KEY your log showed: 'response': <StreamingBody ...>
        #                     raw_output_string = response['response'].read().decode('utf-8')
        #                 elif 'Payload' in response and hasattr(response['Payload'], 'read'):
        #                      # Common in Lambda/SageMaker
        #                     raw_output_string = response['Payload'].read().decode('utf-8')

        #             # 3. Try to parse whatever string we extracted as JSON
        #             try:
        #                 response_json = json.loads(raw_output_string)
        #                 # Check if it matches the Strands InvocationResponse schema
        #                 if isinstance(response_json, dict) and "output" in response_json:
        #                     agent_response_text = response_json["output"]
        #                 else:
        #                     agent_response_text = raw_output_string
        #             except json.JSONDecodeError:
        #                 # If it's just a plain string (not JSON), use it as is
        #                 agent_response_text = raw_output_string

        #         except (TypeError, AttributeError, Exception) as e:
        #             # Fallback if stream reading fails
        #             logger.error(f"Stream reading error: {e}")
        #             agent_response_text = str(raw_output_string)

        #         logger.info("Successfully received response from Agent")

        #     except ClientError as e:
        #         logger.error(f"Bedrock ClientError: {e}")
        #         status_code = "error"
        #         error_msg = str(e)
        #         agent_response_text = f"Error invoking agent: {e}"
                
        #     except Exception as e:
        #         logger.error(f"General Exception during Agent Invocation: {e}")
        #         status_code = "error"
        #         error_msg = str(e)
        #         agent_response_text = "An unexpected error occurred during invocation."
        # else:
        #     logger.error("Bedrock client not initialized")
        #     status_code = "error"
        #     error_msg = "Bedrock client not initialized"
        #     agent_response_text = "Service Error: Agent client unavailable."
        agent_response_text = "I'm an ai agent, not a doctor."
        # -------------------------------------------------------
        # 2. Write to DynamoDB (Resilient)
        # -------------------------------------------------------
        if table:
            try:
                item = {
                    "PK": f"SESSION#{request.session_id}",
                    "SK": f"MSG#{int(time.time()*1000)}",
                    "UserMessage": request.message,
                    "AgentResponse": agent_response_text,
                    "Region": REGION,
                    "AgentID": request.agent_id,
                    "Timestamp": int(time.time()),
                    "Status": status_code
                }
                if error_msg:
                    item["Error"] = error_msg
                
                table.put_item(Item=item)
            except Exception as e:
                # Log only, do not crash
                logger.error(f"DynamoDB Write Failed: {e}")

        # -------------------------------------------------------
        # 3. Invoke Metrics Lambda (Resilient & Async)
        # -------------------------------------------------------
        if lambda_client and METRICS_LAMBDA_NAME:
            try:
                metric_payload = {
                    "type": "chat_metric",
                    "session_id": request.session_id,
                    "agent_id": request.agent_id,
                    "latency": time.time() - start_time,
                    "status": status_code,
                    "timestamp": int(time.time())
                }
                
                lambda_client.invoke(
                    FunctionName=METRICS_LAMBDA_NAME,
                    InvocationType='Event', 
                    Payload=json.dumps(metric_payload)
                )
            except Exception as e:
                logger.warning(f"Metrics Lambda Invocation Failed: {e}")

    except Exception as e:
        # Catch-all for any logic error outside the inner blocks (e.g. variable assignment failures)
        logger.error(f"Critical Service Error: {e}")
        status_code = "error"
        error_msg = str(e)
        agent_response_text = "Critical Internal Error"

    # -------------------------------------------------------
    # 4. Return Response (Guaranteed)
    # -------------------------------------------------------
    return ChatResponse(
        session_id=request.session_id,
        response=agent_response_text,
        processing_time=time.time() - start_time,
        region=REGION,
        status=status_code,
        error=error_msg
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=80)