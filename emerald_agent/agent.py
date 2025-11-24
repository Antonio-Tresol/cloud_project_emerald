from fastapi import FastAPI
from pydantic import BaseModel
from strands import Agent
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Strands Agent Server", version="1.0.0")


class InvocationRequest(BaseModel):
    prompt: str

class InvocationResponse(BaseModel):
    output: str

@app.post("/invocations", response_model=InvocationResponse)
async def invoke_agent(request: InvocationRequest):
    try:
        agent = Agent(model="openai.gpt-oss-120b-1:0")
        output = agent(request.prompt)
        return InvocationResponse(output=str(output))
    except Exception as e:
        logger.error(f"Agent invocation failed: {e}")  
        return InvocationResponse(output=request.prompt)

@app.get("/ping")
async def ping():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)