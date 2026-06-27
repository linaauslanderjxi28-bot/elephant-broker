"""Qwen3 Reranker Server — 兼容 Cohere/Jina /v1/rerank API."""
import os
import torch
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModelForSequenceClassification, AutoTokenizer
import uvicorn

MODEL_PATH = os.environ.get("MODEL_PATH", "/model")
PORT = int(os.environ.get("PORT", "8000"))

app = FastAPI(title="Qwen3 Reranker")

tokenizer = None
model = None

@app.on_event("startup")
def load_model():
    global tokenizer, model
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_PATH, trust_remote_code=True, torch_dtype=torch.float16,
    )
    model.eval()
    if torch.cuda.is_available():
        model = model.cuda()


class RerankRequest(BaseModel):
    model: str = ""
    query: str
    documents: list[str]
    top_n: int | None = None


class RerankResult(BaseModel):
    index: int
    relevance_score: float


class RerankResponse(BaseModel):
    results: list[RerankResult]


@app.post("/v1/rerank")
def rerank(req: RerankRequest):
    pairs = [[req.query, doc] for doc in req.documents]
    inputs = tokenizer(pairs, padding=True, truncation=True, max_length=8192, return_tensors="pt")
    if torch.cuda.is_available():
        inputs = {k: v.cuda() for k, v in inputs.items()}

    with torch.no_grad():
        scores = model(**inputs, return_dict=True).logits.view(-1).float().tolist()

    results = [
        RerankResult(index=i, relevance_score=scores[i])
        for i in range(len(scores))
    ]
    results.sort(key=lambda x: x.relevance_score, reverse=True)
    return RerankResponse(results=results)


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": model is not None}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
